"""The C++ adapter: CMake + Ninja builds, CTest and GoogleTest, libclang indexing and clang-tidy
codemods (SPEC §7.1, §11.1)."""

from __future__ import annotations

import difflib
import json
import logging
import os
import re
import shlex
import shutil
import sys
import time
from pathlib import Path, PurePosixPath
from typing import Any, cast

from codecoremorph.adapters.base import AdapterContext, LanguageAdapter
from codecoremorph.adapters.cpp.cmake import (
    CXX98_INITIAL_CACHE,
    PRE_CXX11,
    STANDARD_ALIASES,
    SUPPORTED_STANDARDS,
    build_command,
    bump_cmake_standard,
    configure_command,
    cxx_standard_number,
    is_cmake_file,
    lowest_standard,
    std_flag,
)
from codecoremorph.adapters.cpp.detect import detect as detect_project
from codecoremorph.adapters.cpp.determinism import nondeterminism
from codecoremorph.adapters.cpp.diagnostics import parse_cmake_output, parse_compiler_output
from codecoremorph.adapters.cpp.fixes import (
    apply_fixes,
    fixed_sites,
    parse_fixes,
    requested_check,
)
from codecoremorph.adapters.cpp.gtest import assertions, drop_tests, find_cases
from codecoremorph.adapters.cpp.helpers.cpp_index_helper import split_windows
from codecoremorph.adapters.cpp.indexing import (
    COMPILE_COMMANDS,
    HEADER_EXTENSIONS,
    SOURCE_EXTENSIONS,
    build_files,
    run_index,
)
from codecoremorph.adapters.cpp.paths import PathMapper
from codecoremorph.adapters.cpp.registration import TARGET as CHARACTERIZATION_TARGET
from codecoremorph.adapters.cpp.registration import (
    characterization_cmake,
    with_block,
    without_block,
)
from codecoremorph.adapters.cpp.testing import (
    ReportParseError,
    ctest_regex,
    gtest_filter,
    merge_cases,
    parse_ctest_junit,
    read_gtest_dir,
)
from codecoremorph.core.errors import ConfigError
from codecoremorph.core.globs import PathMatcher
from codecoremorph.core.models import (
    AdapterCapabilities,
    BuildResult,
    CodemodChange,
    CodemodResult,
    CodemodSpec,
    Diagnostic,
    DiagnosticOrigin,
    LanguageConfig,
    ProjectDetection,
    RuleSite,
    Severity,
    SkippedReplacement,
    SymbolIndex,
    SymbolKind,
    TestAssertions,
    TestCaseResult,
    TestReport,
    TestSelection,
    TestStatus,
    ToolchainSpec,
    ToolRequirement,
)
from codecoremorph.patching.textfile import BinaryFileError, TextFile
from codecoremorph.rules.applicability import StandardOrder
from codecoremorph.rules.detectors import mask_c_family
from codecoremorph.sandbox.executor import full_output
from codecoremorph.store.index import INDEX_FILE_NAME, IndexStore

log = logging.getLogger(__name__)

MIN_LLVM = "18"
STYLE_FILES = (".clang-format", "_clang-format")
_STD_FLAG = re.compile(r"[-/]std[:=](c|gnu)\+\+(\w+)")
INSTALL_HINT = (
    "Install the pinned native toolchain with `codecoremorph toolchain install cpp` "
    "(or scripts/install-toolchain cpp), or use Docker (sandbox.mode: docker)."
)


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.codecoremorph-tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


class CppAdapter(LanguageAdapter):
    """C and C++ projects built with CMake."""

    language = "cpp"
    version = "1"

    # ------------------------------------------------------------------ description

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            parse_check=True,
            format=True,
            register_test_files=True,
            extract_assertions=True,
            detect_disabled_tests=True,
            codemod_tools=["clang-tidy", "cmake-standard"],
            queries=["typedef_declarations"],
            standards=list(SUPPORTED_STANDARDS),
        )

    def toolchain_requirements(self, lang: LanguageConfig) -> ToolchainSpec:
        return ToolchainSpec(
            language="cpp",
            image="codecoremorph-cpp",
            tools=[
                ToolRequirement(name="clang", min_version=MIN_LLVM, purpose="C compiler"),
                ToolRequirement(name="clang++", min_version=MIN_LLVM, purpose="C++ compiler"),
                ToolRequirement(name="clang-tidy", min_version=MIN_LLVM, purpose="codemods"),
                ToolRequirement(
                    name="clang-format", min_version=MIN_LLVM, purpose="formatting codemod output"
                ),
                ToolRequirement(name="cmake", min_version="3.21", purpose="build system"),
                ToolRequirement(name="ctest", min_version="3.21", purpose="test runner"),
                ToolRequirement(name="ninja", min_version="1.10", purpose="build tool"),
            ],
            install_hint=INSTALL_HINT,
        )

    # ------------------------------------------------------------------ helpers

    def _mapper(self, ctx: AdapterContext) -> PathMapper:
        native_fold = ctx.executor.kind == "native" and sys.platform in ("win32", "darwin")
        return PathMapper(
            ctx.sandbox(ctx.worktree), ctx.sandbox(ctx.build_dir), case_insensitive=native_fold
        )

    def _compile_commands(self, ctx: AdapterContext) -> list[dict[str, Any]]:
        path = ctx.cache_dir / COMPILE_COMMANDS
        if not path.is_file():
            return []
        return cast(list[dict[str, Any]], json.loads(path.read_text(encoding="utf-8")))

    def _translation_units(self, ctx: AdapterContext, mapper: PathMapper) -> list[str]:
        units: list[str] = []
        for entry in self._compile_commands(ctx):
            rel = mapper.relative(str(entry["file"]), str(entry.get("directory", "")))
            if rel is not None and rel not in units:
                units.append(rel)
        return units

    def _units_for(self, ctx: AdapterContext, mapper: PathMapper, files: list[str]) -> list[str]:
        """Translation units that compile ``files``: the sources themselves, and for headers
        the sources that include them (from the index when there is one, else all)."""
        all_units = self._translation_units(ctx, mapper)
        known = set(all_units)
        units = [f for f in files if f in known]
        headers = [f for f in files if f not in known and f.lower().endswith(HEADER_EXTENSIONS)]
        if not headers:
            return units
        index_path = ctx.cache_dir / INDEX_FILE_NAME
        if not index_path.is_file():
            return all_units
        with IndexStore.open(index_path) as store:
            seen = set(headers)
            queue = list(headers)
            while queue:
                for dependent in store.dependents_of(queue.pop()):
                    if dependent not in seen:
                        seen.add(dependent)
                        queue.append(dependent)
        return sorted(set(units) | {f for f in seen if f in known})

    def _run(
        self, ctx: AdapterContext, argv: list[str], log_name: str, *, timeout_s: float, **kw: Any
    ) -> tuple[Any, str]:
        result = ctx.executor.run(
            argv,
            cwd=ctx.sandbox(ctx.worktree),
            timeout_s=timeout_s,
            log_path=ctx.log_dir / log_name,
            **kw,
        )
        stdout, stderr = full_output(result)
        return result, stdout + ("\n" if stdout and not stdout.endswith("\n") else "") + stderr

    # ------------------------------------------------------------------ build

    def configure(self, ctx: AdapterContext) -> BuildResult:
        ctx.build_dir.mkdir(parents=True, exist_ok=True)
        ctx.log_dir.mkdir(parents=True, exist_ok=True)
        mapper = self._mapper(ctx)
        build_options = ctx.lang.configure_args
        standard = cxx_standard_number(ctx.lang.standard)
        initial_cache: str | None = None
        if standard in PRE_CXX11:
            ctx.cache_dir.mkdir(parents=True, exist_ok=True)
            script = ctx.cache_dir / "cxx98-initial-cache.cmake"
            script.write_text(CXX98_INITIAL_CACHE, encoding="utf-8")
            initial_cache = ctx.sandbox(script)
        marker = ctx.cache_dir / "configured-standard"
        previous = marker.read_text(encoding="utf-8").strip() if marker.is_file() else None
        argv = configure_command(
            ctx.sandbox(ctx.worktree),
            ctx.sandbox(ctx.build_dir),
            standard=standard,
            extra_args=build_options,
            initial_cache=initial_cache,
            fresh=previous is not None and previous != standard,
        )
        result, output = self._run(ctx, argv, "configure.log", timeout_s=ctx.lang.build_timeout_s)
        if result.ok:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(standard, encoding="utf-8")
        diagnostics = parse_cmake_output(output, mapper, ctx.sandbox(ctx.worktree))
        diagnostics += parse_compiler_output(output, mapper)
        success = result.ok
        if success:
            copied = ctx.executor.run(
                [
                    "cmake",
                    "-E",
                    "copy",
                    f"{ctx.sandbox(ctx.build_dir)}/{COMPILE_COMMANDS}",
                    ctx.sandbox(ctx.cache_dir / COMPILE_COMMANDS),
                ],
                cwd=ctx.sandbox(ctx.worktree),
                timeout_s=60,
                log_path=ctx.log_dir / "configure-copy.log",
            )
            success = copied.ok
        if not success and not any(d.severity is Severity.ERROR for d in diagnostics):
            diagnostics.append(_failure_diagnostic("configure", result))
        return BuildResult(
            success=success,
            diagnostics=diagnostics,
            duration_s=result.duration_s,
            log_path=result.log_path,
        )

    def build(self, ctx: AdapterContext, targets: list[str] | None = None) -> BuildResult:
        mapper = self._mapper(ctx)
        chosen = list(targets) if targets is not None else list(ctx.lang.build_targets)
        argv = build_command(ctx.sandbox(ctx.build_dir), chosen)
        result, output = self._run(ctx, argv, "build.log", timeout_s=ctx.lang.build_timeout_s)
        diagnostics = parse_compiler_output(output, mapper, base=ctx.sandbox(ctx.build_dir))
        diagnostics += parse_cmake_output(output, mapper, ctx.sandbox(ctx.worktree))
        if not result.ok and not any(d.severity is Severity.ERROR for d in diagnostics):
            diagnostics.append(_failure_diagnostic("build", result))
        return BuildResult(
            success=result.ok,
            diagnostics=diagnostics,
            duration_s=result.duration_s,
            log_path=result.log_path,
        )

    def clean_build(self, ctx: AdapterContext) -> None:
        # The build tree may be a Docker volume the host can't see, so the sandbox empties it:
        # first the build outputs, then the CMake cache, which makes the next configure fresh.
        build = ctx.sandbox(ctx.build_dir)
        ctx.log_dir.mkdir(parents=True, exist_ok=True)
        self._run(ctx, ["cmake", "--build", build, "--target", "clean"], "clean.log", timeout_s=600)
        self._run(
            ctx,
            ["cmake", "-E", "rm", "-rf", f"{build}/CMakeCache.txt", f"{build}/CMakeFiles"],
            "clean-cache.log",
            timeout_s=120,
        )
        (ctx.cache_dir / "configured-standard").unlink(missing_ok=True)

    # ------------------------------------------------------------------ tests

    def run_tests(self, ctx: AdapterContext, selection: TestSelection | None = None) -> TestReport:
        work = ctx.cache_dir / "tests"
        shutil.rmtree(work, ignore_errors=True)
        gtest_dir = work / "gtest"
        gtest_dir.mkdir(parents=True)
        junit = work / "ctest-junit.xml"
        timeout = ctx.lang.test_timeout_s
        argv = [
            "ctest",
            "--test-dir",
            ctx.sandbox(ctx.build_dir),
            "--output-junit",
            ctx.sandbox(junit),
            "--timeout",
            str(int(timeout)),
            "-j",
            str(os.cpu_count() or 2),
        ]
        env = {"GTEST_OUTPUT": f"xml:{ctx.sandbox(gtest_dir)}/"}
        if selection is not None and selection.test_ids is not None:
            gtest_ids = [i for i in selection.test_ids if i.startswith("gtest:")]
            ctest_names = [
                i.removeprefix("ctest:") for i in selection.test_ids if i.startswith("ctest:")
            ]
            # Both may be given: CTest entries run, and inside them only these GoogleTest cases.
            if gtest_ids:
                env["GTEST_FILTER"] = gtest_filter(gtest_ids)
            if ctest_names:
                argv += ["-R", ctest_regex(ctest_names)]
        result = ctx.executor.run(
            argv,
            cwd=ctx.sandbox(ctx.worktree),
            env=env,
            timeout_s=timeout + 60,
            log_path=ctx.log_dir / "test.log",
        )
        groups: list[list[TestCaseResult]] = []
        if junit.is_file():
            try:
                groups.append(parse_ctest_junit(junit.read_bytes(), str(junit)))
            except ReportParseError as exc:
                groups.append([_error_case("ctest:(report)", str(exc))])
        gtest_cases, problems = read_gtest_dir(gtest_dir)
        groups.append(gtest_cases)
        groups.append([_error_case("gtest:(report)", p) for p in problems[:1]])
        if result.timed_out:
            groups.append(
                [
                    TestCaseResult(
                        test_id="ctest:(timed out)",
                        status=TestStatus.TIMEOUT,
                        duration_s=result.duration_s,
                        message=f"the test run exceeded {int(timeout)} s",
                    )
                ]
            )
        elif not junit.is_file() and not result.ok:
            _, stderr = full_output(result)
            groups.append([_error_case("ctest:(run)", stderr.strip()[-2000:] or "ctest failed")])
        return TestReport(
            cases=merge_cases(groups), duration_s=result.duration_s, log_path=result.log_path
        )

    # ------------------------------------------------------------------ index

    def detect_project(self, root: Path, files: list[str]) -> ProjectDetection | None:
        """CMake, the standard the CMake files set (or one guessed from the code), the layout
        and the configure arguments a run needs (FR-1.4)."""
        return detect_project(root, files)

    def project_standard(self, ctx: AdapterContext) -> str | None:
        """The oldest standard the project's own CMake files set (excluded paths don't count)."""
        texts: list[str] = []
        for rel in build_files(ctx.worktree, PathMatcher(ctx.lang.exclude)):
            try:
                texts.append(TextFile.decode((ctx.worktree / rel).read_bytes()).text)
            except BinaryFileError:
                continue  # not a CMake file after all
        return lowest_standard(texts)

    def index(self, ctx: AdapterContext, files: list[str] | None = None) -> SymbolIndex:
        # Unchanged translation units come from the helper's cache, so re-indexing after an
        # edit costs only the units that include the edited files (FR-2.4).
        return run_index(ctx, adapter_version=self.version)

    # ------------------------------------------------------------------ codemods

    def run_codemods(
        self, ctx: AdapterContext, codemods: list[CodemodSpec], files: list[str]
    ) -> CodemodResult:
        started = time.monotonic()
        totals: dict[str, int] = {}
        skipped: list[SkippedReplacement] = []
        remaining: list[Diagnostic] = []
        diagnostics: list[Diagnostic] = []
        logs: list[Path] = []
        originals: dict[str, bytes] = {}  # files clang-tidy changed, as they were before
        for number, spec in enumerate(codemods, start=1):
            if spec.tool == "cmake-standard":
                changes = self._bump_standard(ctx, files)
            elif spec.tool == "clang-tidy":
                changes, extra = self._clang_tidy(ctx, spec, files, number, originals)
                skipped += extra.skipped
                remaining += extra.remaining
                diagnostics += extra.diagnostics
                logs += extra.log_paths
            else:
                raise ConfigError(
                    f"the C++ adapter has no codemod tool {spec.tool!r}",
                    hint="Available: clang-tidy, cmake-standard.",
                )
            for path, count in changes.items():
                totals[path] = totals.get(path, 0) + count
        logs += self._format_changed_lines(ctx, originals)
        return CodemodResult(
            changes=[CodemodChange(file=f, replacements=n) for f, n in sorted(totals.items())],
            skipped=skipped,
            remaining=remaining,
            diagnostics=diagnostics,
            duration_s=time.monotonic() - started,
            log_paths=logs,
        )

    def _bump_standard(self, ctx: AdapterContext, files: list[str]) -> dict[str, int]:
        target = cxx_standard_number(ctx.lang.target_standard or ctx.lang.standard)
        changes: dict[str, int] = {}
        for path in files:
            if not is_cmake_file(path):
                continue
            host = ctx.worktree / path
            data = host.read_bytes()
            text_file = TextFile.decode(data)
            edit = bump_cmake_standard(text_file.text, target, top_level=path == "CMakeLists.txt")
            if edit.replacements:
                _write_atomic(host, text_file.encode(edit.text))
                changes[path] = edit.replacements
        return changes

    def _format_changed_lines(self, ctx: AdapterContext, originals: dict[str, bytes]) -> list[Path]:
        """clang-format the lines clang-tidy changed, in files a project ``.clang-format``
        covers: clang-tidy prints the types it writes in its own style (``Iterator *``), not
        the project's. Only the changed lines are formatted, never the rest of the file."""
        logs: list[Path] = []
        for number, (rel, before) in enumerate(sorted(originals.items()), start=1):
            if not _styled(ctx.worktree, rel):
                continue
            host = ctx.worktree / rel
            ranges = changed_line_ranges(before, host.read_bytes())
            if not ranges:
                continue
            argv = [
                "clang-format",
                "-i",
                "--style=file",
                "--fallback-style=none",
                *[f"--lines={first}:{last}" for first, last in ranges],
                ctx.sandbox(host),
            ]
            result, output = self._run(
                ctx, argv, f"codemod-format-{number}.log", timeout_s=ctx.lang.build_timeout_s
            )
            logs.append(result.log_path)
            if not result.ok:
                # Formatting is cosmetic: the codemod's change stands, unformatted.
                log.warning("clang-format failed on %s: %s", rel, output.strip()[-500:])
        return logs

    def _clang_tidy(
        self,
        ctx: AdapterContext,
        spec: CodemodSpec,
        files: list[str],
        number: int,
        originals: dict[str, bytes],
    ) -> tuple[dict[str, int], CodemodResult]:
        mapper = self._mapper(ctx)
        unit = set(files)
        units = self._units_for(ctx, mapper, files)
        empty = CodemodResult(duration_s=0)
        runs, standard_args = self._pass_standard(ctx, spec)
        if standard_args:
            # A C++ -std= flag breaks C translation units, and C++ checks don't apply to them.
            units = [u for u in units if not u.lower().endswith(".c")]
        if not units or not spec.checks or not runs:
            return {}, empty
        work = ctx.cache_dir / "codemods"
        work.mkdir(parents=True, exist_ok=True)
        fixes_path = work / f"fixes-{number}.yaml"
        fixes_path.unlink(missing_ok=True)
        headers = [f for f in files if f.lower().endswith(HEADER_EXTENSIONS)]
        argv = [
            "clang-tidy",
            "-p",
            ctx.sandbox(ctx.build_dir),
            f"--checks=-*,{','.join(spec.checks)}",
            f"--header-filter={_header_filter(headers)}",
            f"--export-fixes={ctx.sandbox(fixes_path)}",
            "--quiet",
            *standard_args,
        ]
        if spec.options:
            options = ", ".join(f"{k}: '{v}'" for k, v in sorted(spec.options.items()))
            argv.append(f"--config={{CheckOptions: {{{options}}}}}")
        argv += [mapper.sandbox(u) for u in units]
        result, output = self._run(
            ctx, argv, f"codemod-{number}-clang-tidy.log", timeout_s=ctx.lang.build_timeout_s
        )
        reported = parse_compiler_output(output, mapper, origin=DiagnosticOrigin.ANALYZER)
        compile_errors = [
            d
            for d in reported
            if d.severity is Severity.ERROR and (d.code in (None, "clang-diagnostic-error"))
        ]
        exported = (
            parse_fixes(fixes_path.read_text(encoding="utf-8")) if fixes_path.is_file() else []
        )
        # Only the requested checks' fixes. clang-tidy also exports the compiler's own fix-its
        # (clang-diagnostic-*, e.g. "did you mean '__unary_function'?"), which would rewrite
        # code to whatever a typo correction guesses.
        fixes = [f for f in exported if requested_check(f.check, spec.checks)]

        def key_of(path: str) -> str | None:
            rel = mapper.relative(path)
            return rel if rel in unit else None

        def read(rel: str) -> bytes:
            return (ctx.worktree / rel).read_bytes()

        with_fix = fixed_sites(fixes, key_of=key_of, read=read)
        outcome = apply_fixes(fixes, key_of=key_of, read=read)
        for rel, data in outcome.new_contents.items():
            originals.setdefault(rel, read(rel))
            _write_atomic(ctx.worktree / rel, data)
        remaining = [
            d
            for d in reported
            if d.code in spec.checks
            and d.file_path in unit
            and (d.file_path, d.line, d.code) not in with_fix
        ]
        return dict(outcome.applied), CodemodResult(
            skipped=[
                SkippedReplacement(file=path, check=check, reason=reason)
                for path, check, reason in outcome.skipped
            ],
            remaining=remaining,
            diagnostics=compile_errors,
            duration_s=result.duration_s,
            log_paths=[result.log_path],
        )

    def _pass_standard(self, ctx: AdapterContext, spec: CodemodSpec) -> tuple[bool, list[str]]:
        """Whether a clang-tidy pass runs, and the arguments that raise its parse standard.

        A pass with ``standard`` parses under at least that standard (the compile commands'
        own ``-std=`` comes first, so the appended one wins), and is skipped when the target
        is older: its replacements would not compile there.
        """
        if spec.standard is None:
            return True, []
        order = StandardOrder("cpp", SUPPORTED_STANDARDS)
        target = ctx.lang.target_standard or ctx.lang.standard
        if not order.at_least(target, spec.standard):
            return False, []
        current, gnu = self._compile_standard(ctx)
        if order.at_least(current, spec.standard):
            return True, []
        return True, [f"--extra-arg={std_flag(cxx_standard_number(spec.standard), gnu=gnu)}"]

    def _compile_standard(self, ctx: AdapterContext) -> tuple[str, bool]:
        """The C++ standard the compile commands use (``cpp11``), and whether it is the GNU
        dialect. Falls back to the configured standard when no command names one."""
        for entry in self._compile_commands(ctx):
            if str(entry.get("file", "")).lower().endswith(".c"):
                continue
            text = " ".join(entry.get("arguments") or [str(entry.get("command", ""))])
            flags = _STD_FLAG.findall(text)
            if flags:
                dialect, number = flags[-1]
                number = STANDARD_ALIASES.get(number, number)
                if f"cpp{number}" in SUPPORTED_STANDARDS:
                    return f"cpp{number}", dialect == "gnu"
        return f"cpp{cxx_standard_number(ctx.lang.standard)}", True

    # ------------------------------------------------------------------ optional

    def parse_check(self, ctx: AdapterContext, files: list[str]) -> list[Diagnostic]:
        """Each affected translation unit's own compile command, with ``-fsyntax-only``."""
        mapper = self._mapper(ctx)
        wanted = set(self._units_for(ctx, mapper, files))
        diagnostics: list[Diagnostic] = []
        for number, entry in enumerate(self._compile_commands(ctx), start=1):
            directory = str(entry.get("directory", ""))
            unit = mapper.relative(str(entry["file"]), directory)
            if unit is None or unit not in wanted:
                continue
            wanted.discard(unit)
            argv = syntax_only_command(entry, windows=ctx.executor.kind == "native" and _WINDOWS)
            result = ctx.executor.run(
                argv,
                cwd=directory,
                timeout_s=ctx.lang.build_timeout_s,
                log_path=ctx.log_dir / f"parse-check-{number}.log",
            )
            stdout, stderr = full_output(result)
            diagnostics += parse_compiler_output(stdout + "\n" + stderr, mapper, base=directory)
        return [d for d in diagnostics if d.severity is not Severity.NOTE]

    def format(self, ctx: AdapterContext, files: list[str]) -> None:
        if not any((ctx.worktree / name).is_file() for name in STYLE_FILES):
            return  # never impose a style on a project that has none
        code = [f for f in files if f.lower().endswith(SOURCE_EXTENSIONS + HEADER_EXTENSIONS)]
        if not code:
            return
        argv = [
            "clang-format",
            "-i",
            "--style=file",
            *[ctx.sandbox(ctx.worktree / f) for f in code],
        ]
        self._run(ctx, argv, "format.log", timeout_s=300)

    def detect_disabled_tests(self, ctx: AdapterContext, files: list[str]) -> list[str]:
        found: list[str] = []
        for path in files:
            text = _read_text(ctx.worktree / path)
            if text is not None:
                found += find_disabled_tests(path, text)
        return found

    # ------------------------------------------------------------------ characterization

    def characterization_test_file(self, test_dir: str, stem: str) -> str:
        return f"{test_dir.rstrip('/')}/{stem}_test.cpp"

    def register_test_files(
        self, ctx: AdapterContext, test_dir: str, files: list[str], sources: list[str]
    ) -> list[str]:
        root_file = ctx.worktree / "CMakeLists.txt"
        root = TextFile.decode(root_file.read_bytes())
        directory = PurePosixPath(test_dir.rstrip("/"))
        listing = ctx.worktree / directory / "CMakeLists.txt"
        if files:
            names = [PurePosixPath(f).relative_to(directory).as_posix() for f in files]
            listing.parent.mkdir(parents=True, exist_ok=True)
            _write_atomic(listing, characterization_cmake(names, sources).encode("utf-8"))
            text = with_block(root.text, directory.as_posix())
        else:
            listing.unlink(missing_ok=True)
            text = without_block(root.text)
        if text != root.text:
            _write_atomic(root_file, root.encode(text))
        return [(directory / "CMakeLists.txt").as_posix(), "CMakeLists.txt"]

    def characterization_tests(self, test_ids: list[str]) -> TestSelection:
        return TestSelection(test_ids=[f"ctest:{CHARACTERIZATION_TARGET}", *test_ids])

    def check_determinism(self, ctx: AdapterContext, files: list[str]) -> list[str]:
        found: list[str] = []
        for path in files:
            text = _read_text(ctx.worktree / path)
            if text is not None:
                found += nondeterminism(path, text)
        return found

    def drop_tests(self, ctx: AdapterContext, file: str, test_ids: list[str]) -> int:
        path = ctx.worktree / file
        decoded = TextFile.decode(path.read_bytes())
        names = {i.removeprefix("gtest:") for i in test_ids}
        text = drop_tests(decoded.text, names)
        if text != decoded.text:
            _write_atomic(path, decoded.encode(text))
        return len(find_cases(text))

    def extract_assertions(self, ctx: AdapterContext, files: list[str]) -> list[TestAssertions]:
        found: list[TestAssertions] = []
        for path in files:
            text = _read_text(ctx.worktree / path)
            if text is None:
                continue
            for block in find_cases(text):
                found.append(
                    TestAssertions(
                        file=path,
                        test=block.name,
                        test_id=f"gtest:{block.name}",
                        line=block.line,
                        assertions=assertions(text, block),
                    )
                )
        return found

    def query(self, ctx: AdapterContext, name: str, files: list[str]) -> list[RuleSite]:
        if name != "typedef_declarations":
            return super().query(ctx, name, files)
        index_path = ctx.cache_dir / INDEX_FILE_NAME
        if not index_path.is_file():
            raise ConfigError(
                "the typedef_declarations query needs an index",
                hint="Index the project first.",
            )
        wanted = set(files)
        sites: list[RuleSite] = []
        with IndexStore.open(index_path) as store:
            for symbol in store.symbols(kind=SymbolKind.TYPEDEF):
                if symbol.file in wanted:
                    sites.append(
                        RuleSite(
                            file=symbol.file,
                            line=symbol.range.start_line,
                            column=symbol.range.start_column,
                            text=symbol.signature or symbol.qualified_name,
                        )
                    )
        return sites


_TEST_MACRO = re.compile(
    r"\b(?:TEST|TEST_F|TEST_P|TYPED_TEST|TYPED_TEST_P)\s*\(\s*(\w+)\s*,\s*(\w+)"
)
_SKIP = re.compile(r"\bGTEST_SKIP\s*\(")
_IF0 = re.compile(r"^[ \t]*#[ \t]*if[ \t]+0\b", re.MULTILINE)
_ENDIF = re.compile(r"^[ \t]*#[ \t]*(?:if|ifdef|ifndef|endif)\b", re.MULTILINE)


def find_disabled_tests(path: str, text: str) -> list[str]:
    """Markers that disable or skip GoogleTest tests (invariant I-3)."""
    masked = mask_c_family(text)
    found: list[tuple[int, str]] = []

    def line_of(offset: int) -> int:
        return masked.count("\n", 0, offset) + 1

    for match in _TEST_MACRO.finditer(masked):
        if match.group(1).startswith("DISABLED_") or match.group(2).startswith("DISABLED_"):
            found.append(
                (line_of(match.start()), f"disabled test {match.group(1)}.{match.group(2)}")
            )
    for match in _SKIP.finditer(masked):
        found.append((line_of(match.start()), "GTEST_SKIP"))
    for match in _IF0.finditer(masked):
        depth = 0
        end = len(masked)
        for directive in _ENDIF.finditer(masked, match.start()):
            word = directive.group(0).strip().lstrip("#").strip()
            if word.startswith("if"):
                depth += 1
            else:
                depth -= 1
                if depth == 0:
                    end = directive.start()
                    break
        if _TEST_MACRO.search(masked, match.end(), end):
            found.append((line_of(match.start()), "#if 0 around tests"))
    return [f"{path}:{line}: {what}" for line, what in sorted(found)]


_WINDOWS = sys.platform == "win32"
_OUTPUT_FLAGS_WITH_VALUE = {"-o", "-MF", "-MT", "-MQ"}
_OUTPUT_FLAGS = {"-c", "-MD", "-MMD", "-M", "-MM"}


def syntax_only_command(entry: dict[str, Any], *, windows: bool) -> list[str]:
    """A compile-database entry's command, checking syntax only and writing nothing."""
    if "arguments" in entry:
        argv = [str(a) for a in cast(list[Any], entry["arguments"])]
    else:
        command = str(entry.get("command", ""))
        argv = split_windows(command) if windows else shlex.split(command)
    out: list[str] = []
    skip = False
    for arg in argv:
        if skip:
            skip = False
            continue
        if arg in _OUTPUT_FLAGS_WITH_VALUE:
            skip = True
            continue
        if arg in _OUTPUT_FLAGS or (arg.startswith(("-o", "-MF")) and len(arg) > 3):
            continue
        out.append(arg)
    return [*out, "-fsyntax-only"]


_REGEX_SPECIAL = re.compile(r"([.^$|()\[\]{}*+?\\])")


def _llvm_escape(text: str) -> str:
    """Escape for llvm::Regex (POSIX extended): only its metacharacters."""
    return _REGEX_SPECIAL.sub(r"\\\1", text)


def _header_filter(headers: list[str]) -> str:
    """clang-tidy's ``--header-filter`` for the unit's headers.

    clang names a header by the include directory it was found through, so the same file can
    be ``/workspace/db/x.h``, ``/workspace/./db/x.h`` (``include_directories(".")``) or
    ``/workspace/src/../db/x.h``. The filter therefore matches the worktree-relative path at
    the end of the name, with ``./`` allowed between its parts; fixes are then kept only for
    the unit's own files, by their normalized path. Plain groups only: llvm::Regex has no
    ``(?:``."""
    if not headers:
        return "^$"
    parts = [r"([/\\]\.)*[/\\]".join(_llvm_escape(s) for s in h.split("/")) for h in headers]
    return r"(^|[/\\])(" + "|".join(parts) + ")$"


def _styled(worktree: Path, rel: str) -> bool:
    """Whether a ``.clang-format`` of the project (not one above the worktree) covers ``rel``."""
    directory = (worktree / rel).parent
    while True:
        if any((directory / name).is_file() for name in STYLE_FILES):
            return True
        if directory == worktree or worktree not in directory.parents:
            return False
        directory = directory.parent


def changed_line_ranges(before: bytes, after: bytes) -> list[tuple[int, int]]:
    """The 1-based, inclusive line ranges of ``after`` that differ from ``before``."""
    try:
        old = TextFile.decode(before).text.splitlines()
        new = TextFile.decode(after).text.splitlines()
    except BinaryFileError:
        return []
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    ranges: list[tuple[int, int]] = []
    for tag, _, _, j1, j2 in matcher.get_opcodes():
        if tag == "equal" or j1 == j2:
            continue
        if ranges and ranges[-1][1] >= j1:
            ranges[-1] = (ranges[-1][0], j2)
        else:
            ranges.append((j1 + 1, j2))
    return ranges


def _failure_diagnostic(step: str, result: Any) -> Diagnostic:
    reason = "timed out" if result.timed_out else f"exited with code {result.exit_code}"
    return Diagnostic(
        file_path=None,
        severity=Severity.ERROR,
        message=f"{step} {reason}; see {result.log_path}",
        origin=DiagnosticOrigin.BUILD_SYSTEM,
    )


def _error_case(test_id: str, message: str) -> TestCaseResult:
    return TestCaseResult(test_id=test_id, status=TestStatus.ERROR, duration_s=0, message=message)


def _read_text(path: Path) -> str | None:
    """A source file's text, or None when it is missing or not text."""
    try:
        return TextFile.decode(path.read_bytes()).text
    except (OSError, ValueError):
        return None
