"""Make builds for the C and C++ adapters (D-68): a plain Makefile, ``./configure`` and Make,
or autotools.

Everything runs in a :class:`~codecoremorph.adapters.mirror.Mirror` of the worktree in the
adapter's cache, since old Makefiles build inside the source tree. ``helpers/capture.sh``,
installed as the build's ``cc``, ``c++`` and ``ar``, logs every compiler and archiver call;
:mod:`codecoremorph.adapters.cpp.capture` turns the log into the compile commands and the
build targets, which CMake's file API gives for a CMake project (D-59).

- **configure**: syncs the mirror; regenerates an autotools project with ``autoreconf`` when it
  has ``configure.ac`` but no ``configure``; runs ``./configure`` (network off) when its inputs
  changed. A project never built in this mirror is then built once, so the compile commands
  exist after configure, as they do for CMake.
- **build**: ``make -j`` with the wrappers; every build adds to the log.
- **tests**: the test target (``check``, else ``test``, or the manifest's ``test_targets``).
  Automake's harness leaves a ``.trs`` file per test, which gives ``make:<test>`` IDs;
  otherwise the TAP the programs print gives ``tap:<program>/<point>`` IDs, and the target's
  exit status is one test, ``make:<target>``. JUnit XML reports the tests write
  (``build.make.junit_reports``) give ``junit:<suite>#<case>`` IDs beside either (D-73).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import time
from collections.abc import Iterable, Mapping
from importlib import resources
from pathlib import Path
from typing import Any

from codecoremorph.adapters.base import AdapterContext
from codecoremorph.adapters.cpp.capture import (
    build_model,
    for_worktree,
    read_calls,
    worktree_exists,
)
from codecoremorph.adapters.cpp.diagnostics import parse_compiler_output
from codecoremorph.adapters.cpp.indexing import COMPILE_COMMANDS
from codecoremorph.adapters.cpp.makefiles import (
    MAKEFILE_NAMES,
    is_autotools_input,
    is_configure_input,
)
from codecoremorph.adapters.cpp.paths import PathMapper
from codecoremorph.adapters.junitxml import read_reports
from codecoremorph.adapters.mirror import Mirror
from codecoremorph.core.models import (
    BuildResult,
    BuildTarget,
    Diagnostic,
    DiagnosticOrigin,
    Severity,
    TargetKind,
    TestCaseResult,
    TestEntry,
    TestReport,
    TestSelection,
    TestStatus,
)
from codecoremorph.sandbox.executor import full_output

log = logging.getLogger(__name__)

BUILD_SYSTEM = "make"
TARGETS_FILE = "make-targets.json"
TESTS_FILE = "make-tests.json"
_DEFAULT_TEST_TARGETS = ("check", "test")
_TRS_FAILED = frozenset({"FAIL", "ERROR", "XPASS"})
_CONFIGURE_ERROR = re.compile(r"^configure: error: (.+)$")
_TAP_POINT = re.compile(
    r"^(?P<not>not )?ok\b(?:\s+(?P<num>\d+))?(?:\s+-)?\s*(?P<desc>[^#]*?)\s*"
    r"(?:#\s*(?P<directive>skip|todo)\b.*)?$",
    re.IGNORECASE,
)
_TAP_PLAN = re.compile(r"^1\.\.(\d+)\b")
_MAKE_ERROR = re.compile(
    r"^(?:(?P<file>[^\s:]+):(?P<line>\d+): \*\*\* (?P<msg>.+?)\.?\s+Stop\.|"
    r"g?make(?:\[\d+\])?: \*\*\* (?P<msg2>No rule to make target .+?)\.?\s+Stop\.)$"
)


def install_capture(cache_dir: Path) -> Path:
    """Install ``capture.sh`` as ``cc``, ``c++`` and ``ar`` in a directory of the cache."""
    data = resources.files("codecoremorph.adapters.cpp.helpers").joinpath("capture.sh").read_bytes()
    directory = cache_dir / "helpers" / "capture"
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("cc", "c++", "ar"):
        target = directory / name
        if not target.is_file() or target.read_bytes() != data:
            tmp = target.with_name(f".{name}.tmp")
            tmp.write_bytes(data)
            tmp.chmod(0o755)
            tmp.replace(target)
    return directory


# Runs a command with the wrappers (in $CODECOREMORPH_TOOLS) as CC, CXX and AR, and the real
# tools, the toolchain's CC, CXX and AR, named for the wrappers to run. Configure scripts read
# CC from the environment (zlib's refuses CC= arguments); make also gets them as arguments,
# which override a Makefile's own CC = gcc.
_WITH_WRAPPERS = (
    'CODECOREMORPH_CC="${CODECOREMORPH_CC:-${CC:-cc}}"; '
    'CODECOREMORPH_CXX="${CODECOREMORPH_CXX:-${CXX:-c++}}"; '
    'CODECOREMORPH_AR="${CODECOREMORPH_AR:-${AR:-ar}}"; '
    "export CODECOREMORPH_CC CODECOREMORPH_CXX CODECOREMORPH_AR; "
    'CC="$CODECOREMORPH_TOOLS/cc"; CXX="$CODECOREMORPH_TOOLS/c++"; AR="$CODECOREMORPH_TOOLS/ar"; '
    'export CC CXX AR; exec "$@"'
)


def _failure(step: str, result: Any) -> Diagnostic:
    reason = "timed out" if result.timed_out else f"exited with code {result.exit_code}"
    return Diagnostic(
        file_path=None,
        severity=Severity.ERROR,
        message=f"{step} {reason}; see {result.log_path}",
        origin=DiagnosticOrigin.BUILD_SYSTEM,
    )


def parse_make_output(text: str, mapper: PathMapper, base: str) -> list[Diagnostic]:
    """Make's own errors: ``Makefile:12: *** missing separator.  Stop.`` and ``make: ***
    No rule to make target 'x.o', needed by 'lib.a'.  Stop.``"""
    found: list[Diagnostic] = []
    for line in text.splitlines():
        m = _MAKE_ERROR.match(line.strip())
        if m is None:
            continue
        if m["msg2"]:
            found.append(
                Diagnostic(
                    file_path=None,
                    severity=Severity.ERROR,
                    message=m["msg2"],
                    origin=DiagnosticOrigin.BUILD_SYSTEM,
                )
            )
            continue
        found.append(
            Diagnostic(
                file_path=mapper.relative(m["file"], base),
                line=int(m["line"]),
                severity=Severity.ERROR,
                message=m["msg"],
                origin=DiagnosticOrigin.BUILD_SYSTEM,
            )
        )
    return found


def parse_configure_output(text: str) -> list[Diagnostic]:
    """``configure: error: ...`` lines (autoconf's, and most hand-written scripts')."""
    return [
        Diagnostic(
            file_path="configure",
            severity=Severity.ERROR,
            message=m.group(1).strip(),
            origin=DiagnosticOrigin.BUILD_SYSTEM,
        )
        for line in text.splitlines()
        if (m := _CONFIGURE_ERROR.match(line.strip())) is not None
    ]


def parse_tap_output(text: str, programs: Mapping[str, str], fallback: str) -> list[TestCaseResult]:
    """TAP from the test target's output. A point belongs to the program whose command make
    echoed last (``programs`` maps each program's command forms, ``./tests/t`` and
    ``tests/t``, to its path); points before any, to ``fallback``. Each point is a test
    ``tap:<program>/<description>`` (``/#<n>`` without one); ``# SKIP`` skips, ``# TODO``
    excuses a failure, and a plan that ran short or a ``Bail out!`` is an error. The ``#``
    lines right after a failed point are its message (TAP's diagnostics)."""
    cases: list[TestCaseResult] = []
    current = fallback
    seen: dict[str, int] = {}
    planned: dict[str, int] = {}
    notes: list[str] | None = None  # the diagnostics of the failed point just read
    for raw in text.splitlines():
        line = raw.strip()
        if notes is not None and line.startswith("#"):
            notes.append(line.lstrip("#").strip())
            cases[-1] = cases[-1].model_copy(update={"message": "\n".join(notes)[:2000]})
            continue
        notes = None
        first = line.split(" ", 1)[0] if line else ""
        if first in programs:
            current = programs[first]
            continue
        if m := _TAP_PLAN.match(line):
            planned[current] = int(m.group(1))
            continue
        if line.startswith("Bail out!"):
            cases.append(
                TestCaseResult(
                    test_id=f"tap:{current}/(bail out)",
                    status=TestStatus.ERROR,
                    duration_s=0.0,
                    message=line,
                )
            )
            continue
        m = _TAP_POINT.match(line)
        if m is None:
            continue
        seen[current] = seen.get(current, 0) + 1
        description = m["desc"].strip() or f"#{m['num'] or seen[current]}"
        test_id = f"tap:{current}/{description}"
        if any(c.test_id == test_id for c in cases):
            test_id = f"{test_id} (#{m['num'] or seen[current]})"
        directive = (m["directive"] or "").upper()
        if directive == "SKIP":
            status = TestStatus.SKIPPED
        elif m["not"] and directive != "TODO":
            status = TestStatus.FAILED
        else:
            status = TestStatus.PASSED
        cases.append(TestCaseResult(test_id=test_id, status=status, duration_s=0.0))
        if status is TestStatus.FAILED:
            notes = []
    for program, count in sorted(planned.items()):
        if seen.get(program, 0) < count:
            cases.append(
                TestCaseResult(
                    test_id=f"tap:{program}/(plan)",
                    status=TestStatus.ERROR,
                    duration_s=0.0,
                    message=f"planned {count} tests, ran {seen.get(program, 0)}",
                )
            )
    return cases


def parse_trs(text: str) -> TestStatus:
    """A ``.trs`` file's status: failed if any result failed, skipped if all were skipped,
    else passed (an expected failure passes)."""
    results = [
        line.split(":", 2)[2].strip().split(" ", 1)[0]
        for line in text.splitlines()
        if line.startswith(":test-result:")
    ]
    if any(r in _TRS_FAILED for r in results):
        return TestStatus.FAILED
    if results and all(r == "SKIP" for r in results):
        return TestStatus.SKIPPED
    return TestStatus.PASSED if results else TestStatus.ERROR


class MakeBuild:
    """The Make build of one adapter context."""

    def __init__(self, ctx: AdapterContext, *, case_insensitive: bool = False):
        self.ctx = ctx
        self.mirror = Mirror(ctx.worktree, ctx.cache_dir / "make-mirror")
        self.capture_dir = ctx.cache_dir / "make-capture"
        self.case_insensitive = case_insensitive

    # ------------------------------------------------------------------ helpers

    def mapper(self) -> PathMapper:
        """Maps paths in the build's output (the mirror's) to worktree-relative ones."""
        root = self.ctx.sandbox(self.mirror.root)
        return PathMapper(root, root, case_insensitive=self.case_insensitive)

    def _tools(self) -> dict[str, str]:
        directory = install_capture(self.ctx.cache_dir)
        return {
            "CC": self.ctx.sandbox(directory / "cc"),
            "CXX": self.ctx.sandbox(directory / "c++"),
            "AR": self.ctx.sandbox(directory / "ar"),
        }

    def _run(
        self,
        argv: list[str],
        log_name: str,
        *,
        timeout_s: float,
        capture: bool = False,
    ) -> tuple[Any, str]:
        env = {"CODECOREMORPH_TOOLS": self.ctx.sandbox(install_capture(self.ctx.cache_dir))}
        if capture:
            self.capture_dir.mkdir(parents=True, exist_ok=True)
            env["CODECOREMORPH_CAPTURE"] = self.ctx.sandbox(self.capture_dir)
            # Diagnostics name files by absolute path, whatever directory make recursed into.
            env["CODECOREMORPH_CC_EXTRA"] = "-fdiagnostics-absolute-paths"
        self.ctx.log_dir.mkdir(parents=True, exist_ok=True)
        result = self.ctx.executor.run(
            ["sh", "-c", _WITH_WRAPPERS, "codecoremorph-make", *argv],
            cwd=self.ctx.sandbox(self.mirror.root),
            env=env,
            timeout_s=timeout_s,
            log_path=self.ctx.log_dir / log_name,
        )
        stdout, stderr = full_output(result)
        return result, stdout + ("\n" if stdout and not stdout.endswith("\n") else "") + stderr

    def _make(self, targets: Iterable[str], log_name: str, *, timeout_s: float) -> tuple[Any, str]:
        tools = self._tools()
        jobs = f"-j{os.cpu_count() or 2}"
        variables = [f"{k}={v}" for k, v in tools.items()]
        return self._run(
            ["make", jobs, *variables, *targets], log_name, timeout_s=timeout_s, capture=True
        )

    def _configure_digest(self) -> str:
        digest = hashlib.sha256()
        for rel, path in sorted(self.mirror.worktree_files().items()):
            if is_configure_input(rel):
                digest.update(rel.encode() + b"\0" + path.read_bytes() + b"\0")
        digest.update(json.dumps(self.ctx.lang.configure_args).encode())
        return digest.hexdigest()

    def _autotools_digest(self) -> str:
        digest = hashlib.sha256()
        for rel, path in sorted(self.mirror.worktree_files().items()):
            if is_autotools_input(rel):
                digest.update(rel.encode() + b"\0" + path.read_bytes() + b"\0")
        return digest.hexdigest()

    def _regenerates(self, autotools: bool) -> bool:
        """Whether configure runs ``autoreconf`` first. A project with no ``configure`` of its
        own always does. One that commits its ``configure`` uses it until a ``Makefile.am``,
        ``configure.ac`` or m4 file changes (a C port's scaffold edits ``Makefile.am``, D-70),
        since the committed ``aclocal.m4`` may hold macros this machine's aclocal lacks."""
        if not autotools:
            return False
        if not (self.ctx.worktree / "configure").is_file():
            return True
        marker = self.ctx.cache_dir / "make-autotools"
        return marker.is_file() and marker.read_text(encoding="utf-8").strip() != (
            self._autotools_digest()
        )

    def _diagnostics(self, output: str) -> list[Diagnostic]:
        mapper = self.mapper()
        root = self.ctx.sandbox(self.mirror.root)
        found = parse_compiler_output(output, mapper, base=root)
        return found + parse_make_output(output, mapper, root)

    # ------------------------------------------------------------------ configure and build

    def configure(self) -> BuildResult:
        ctx = self.ctx
        started = time.monotonic()
        self.mirror.sync()
        marker = ctx.cache_dir / "make-configured"
        digest = self._configure_digest()
        diagnostics: list[Diagnostic] = []
        log_path = ctx.log_dir / "configure.log"
        success = True
        has_configure = self.mirror.path("configure").is_file()
        autotools = any(self.mirror.path(n).is_file() for n in ("configure.ac", "configure.in"))
        needed = (has_configure or autotools) and (
            not marker.is_file() or marker.read_text(encoding="utf-8").strip() != digest
        )
        if needed:
            if self._regenerates(autotools):
                result, output = self._run(
                    ["autoreconf", "-fi"], "autoreconf.log", timeout_s=ctx.lang.build_timeout_s
                )
                if not result.ok:
                    diagnostics.append(_failure("autoreconf", result))
                    return BuildResult(
                        success=False,
                        diagnostics=diagnostics,
                        duration_s=time.monotonic() - started,
                        log_path=result.log_path,
                    )
            result, output = self._run(
                ["sh", "./configure", *ctx.lang.configure_args],
                "configure.log",
                timeout_s=ctx.lang.build_timeout_s,
            )
            log_path = result.log_path
            success = result.ok
            if success:
                marker.write_text(digest, encoding="utf-8")
                if autotools:
                    (ctx.cache_dir / "make-autotools").write_text(
                        self._autotools_digest(), encoding="utf-8"
                    )
            else:
                marker.unlink(missing_ok=True)
                diagnostics += parse_configure_output(output)
                if not diagnostics:
                    diagnostics.append(_failure("./configure", result))
        if success and not (ctx.cache_dir / COMPILE_COMMANDS).is_file():
            # The index needs the compile commands, which only a build shows: build once.
            built = self.build(sync=False)
            if not built.success:
                log.info("the first build failed; the next build reports it (%s)", built.log_path)
        return BuildResult(
            success=success,
            diagnostics=diagnostics,
            duration_s=time.monotonic() - started,
            log_path=log_path,
        )

    def build(self, targets: list[str] | None = None, *, sync: bool = True) -> BuildResult:
        ctx = self.ctx
        if sync:
            self.mirror.sync()
            marker = ctx.cache_dir / "make-configured"
            if marker.is_file() and marker.read_text(encoding="utf-8").strip() != (
                self._configure_digest()
            ):
                configured = self.configure()  # the build files changed: configure first
                if not configured.success:
                    return configured
        chosen = list(targets) if targets is not None else list(ctx.lang.build_targets)
        result, output = self._make(chosen, "build.log", timeout_s=ctx.lang.build_timeout_s)
        self.record_model()
        diagnostics = self._diagnostics(output)
        if not result.ok and not any(d.severity is Severity.ERROR for d in diagnostics):
            diagnostics.append(_failure("make", result))
        return BuildResult(
            success=result.ok,
            diagnostics=diagnostics,
            duration_s=result.duration_s,
            log_path=result.log_path,
        )

    def clean(self) -> None:
        """Forget the build: the next configure starts from a fresh mirror."""
        self.mirror.wipe()
        shutil.rmtree(self.capture_dir, ignore_errors=True)
        for name in (
            "make-configured",
            "make-autotools",
            COMPILE_COMMANDS,
            TARGETS_FILE,
            TESTS_FILE,
        ):
            (self.ctx.cache_dir / name).unlink(missing_ok=True)

    # ------------------------------------------------------------------ the model

    def record_model(self) -> None:
        """Write the compile commands (for the worktree) and the targets from the log."""
        ctx = self.ctx
        root = ctx.sandbox(self.mirror.root)
        model = build_model(read_calls(self.capture_dir), root=root, compilers=self._compilers())
        commands = for_worktree(
            model.commands,
            mirror=root,
            worktree=ctx.sandbox(ctx.worktree),
            exists=worktree_exists(ctx.worktree),
        )
        _write_json(ctx.cache_dir / COMPILE_COMMANDS, commands)
        # Where CMake's is, for the tools of a port's target: bindgen reads the C headers with
        # the build's include directories and macros (D-68).
        _write_json(ctx.build_dir / COMPILE_COMMANDS, commands)
        _write_json(
            ctx.cache_dir / TARGETS_FILE, [t.model_dump(mode="json") for t in model.targets]
        )

    def _compilers(self) -> dict[str, str]:
        # The compile commands name a compiler the index helper can run: the toolchain's, which
        # the sandbox puts on PATH (clang and clang++ in both the image and native toolchains).
        return {"cc": "clang", "cxx": "clang++"}

    def targets(self) -> list[BuildTarget]:
        path = self.ctx.cache_dir / TARGETS_FILE
        if not path.is_file():
            return []
        return [BuildTarget.model_validate(t) for t in json.loads(path.read_text("utf-8"))]

    def model(self) -> tuple[list[BuildTarget], list[TestEntry]]:
        """The build's targets and tests, for the index. The test programs compile only when
        the tests run, so a cache with no test run yet (a baseline that reused another run's
        results on the same commit) runs them once first: without it the index would know
        neither the test files' compile commands nor the tests."""
        if not (self.ctx.cache_dir / TESTS_FILE).is_file() and self.test_targets():
            self.run_tests()
        return self.targets(), self.test_entries()

    def test_entries(self) -> list[TestEntry]:
        """The tests the last test run found, and the program each runs: automake's tests by
        name (``make:tests/test-reader`` runs ``test-reader``), TAP points by the program that
        printed them (``tap:tests/test_sbuf/...``)."""
        path = self.ctx.cache_dir / TESTS_FILE
        if not path.is_file():
            return []
        executables = [t for t in self.targets() if t.kind is TargetKind.EXECUTABLE]
        names = {t.name for t in executables}
        entries: list[TestEntry] = []
        for raw in json.loads(path.read_text("utf-8")):
            test_id = str(raw)
            runs: str | None = None
            if test_id.startswith("tap:"):
                runs = next(
                    (
                        t.name
                        for t in executables
                        for artifact in t.artifacts
                        if test_id.startswith(f"tap:{artifact}/")
                    ),
                    None,
                )
            else:
                name = test_id.removeprefix("make:").rsplit("/", 1)[-1]
                runs = name if name in names else None
            entries.append(TestEntry(test_id=test_id, runs=runs))
        return entries

    # ------------------------------------------------------------------ tests

    def test_targets(self) -> list[str]:
        """The manifest's test targets, else ``check`` or ``test``, whichever the top
        Makefile (configure's, in the mirror) has; none when it has neither."""
        if self.ctx.lang.test_targets:
            return list(self.ctx.lang.test_targets)
        makefile = next(
            (self.mirror.path(n) for n in sorted(MAKEFILE_NAMES) if self.mirror.path(n).is_file()),
            None,
        )
        if makefile is None:
            return []
        text = makefile.read_text(encoding="utf-8", errors="replace")
        for target in _DEFAULT_TEST_TARGETS:
            if re.search(rf"^{target}\s*:", text, re.MULTILINE):
                return [target]
        return []

    def run_tests(self, selection: TestSelection | None = None) -> TestReport:
        ctx = self.ctx
        targets = self.test_targets()
        log_path = ctx.log_dir / "test.log"
        if not targets:
            return TestReport(cases=[], duration_s=0.0, log_path=log_path)
        for stale in [*self._trs_files(), *self._junit_files()]:
            stale.unlink(missing_ok=True)
        timeout = ctx.lang.test_timeout_s
        result, output = self._make(targets, "test.log", timeout_s=timeout + 60)
        self.record_model()  # the test programs compile during the test run
        junit, unreadable = read_reports(self._junit_files())
        cases: list[TestCaseResult] = []
        for trs in self._trs_files():
            rel = trs.relative_to(self.mirror.root).as_posix().removesuffix(".trs")
            status = parse_trs(trs.read_text(encoding="utf-8", errors="replace"))
            cases.append(TestCaseResult(test_id=f"make:{rel}", status=status, duration_s=0.0))
        if not cases:
            # Without automake's harness, TAP the programs print, and the target's exit status,
            # which also covers its checks that print no TAP (a cmp of a program's output).
            cases = parse_tap_output(output, self._program_commands(), targets[0])
            status = (
                TestStatus.TIMEOUT
                if result.timed_out
                else TestStatus.PASSED
                if result.ok
                else TestStatus.FAILED
            )
            message = None if result.ok else f"see {result.log_path}"
            for target in targets:
                cases.append(
                    TestCaseResult(
                        test_id=f"make:{target}",
                        status=status,
                        duration_s=result.duration_s,
                        message=message,
                    )
                )
        elif result.timed_out:
            cases.append(
                TestCaseResult(
                    test_id="make:(timed out)",
                    status=TestStatus.TIMEOUT,
                    duration_s=result.duration_s,
                    message=f"the test run exceeded {int(timeout)} s",
                )
            )
        # The JUnit XML reports the tests wrote (build.make.junit_reports, D-73): a test each.
        known = {c.test_id for c in cases}
        cases += [c for c in junit if c.test_id not in known]
        if unreadable:
            cases.append(
                TestCaseResult(
                    test_id="junit:(unreadable report)",
                    status=TestStatus.ERROR,
                    duration_s=0.0,
                    message="; ".join(unreadable)[:2000],
                )
            )
        _write_json(ctx.cache_dir / TESTS_FILE, sorted(c.test_id for c in cases))
        if selection is not None and selection.test_ids is not None:
            wanted = set(selection.test_ids)
            cases = [c for c in cases if c.test_id in wanted]
        return TestReport(cases=cases, duration_s=result.duration_s, log_path=result.log_path)

    def _program_commands(self) -> dict[str, str]:
        """How a recipe names each of the build's programs (``tests/t``, ``./tests/t`` and the
        sandbox's absolute path), and the program's path."""
        root = self.ctx.sandbox(self.mirror.root)
        forms: dict[str, str] = {}
        for target in self.targets():
            if target.kind is not TargetKind.EXECUTABLE:
                continue
            for artifact in target.artifacts:
                for form in (artifact, f"./{artifact}", f"{root}/{artifact}"):
                    forms[form] = artifact
        return forms

    def _trs_files(self) -> list[Path]:
        if not self.mirror.root.is_dir():
            return []
        return sorted(self.mirror.root.rglob("*.trs"))

    def _junit_files(self) -> list[Path]:
        """The reports ``build.make.junit_reports`` names, in the build: those the tests
        wrote, not files of the project's own that a glob happens to match."""
        root = self.mirror.root
        if not root.is_dir():
            return []
        found: set[Path] = set()
        for pattern in self.ctx.lang.junit_reports:
            for path in root.glob(pattern):
                rel = path.relative_to(root)
                if path.is_file() and not (self.ctx.worktree / rel).exists():
                    found.add(path)
        return sorted(found)


def _write_json(path: Path, data: object) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(path)
