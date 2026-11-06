"""Make builds (D-68): configure, build, tests and their results, with a scripted executor
standing in for make and ./configure. The real tools run in tests/integration."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Literal

import pytest

from codecoremorph.adapters.base import AdapterContext
from codecoremorph.adapters.cpp.make import (
    TARGETS_FILE,
    MakeBuild,
    parse_configure_output,
    parse_make_output,
    parse_tap_output,
    parse_trs,
)
from codecoremorph.adapters.cpp.makefiles import has_make_build, is_make_file, standards_in
from codecoremorph.adapters.cpp.paths import PathMapper
from codecoremorph.core.models import (
    LanguageConfig,
    Severity,
    TestSelection,
    TestStatus,
)
from codecoremorph.sandbox.executor import ExecResult

Script = Callable[[list[str], Mapping[str, str], Path], tuple[int, str]]


class ScriptedExecutor:
    """Runs nothing: ``script(argv, env, cwd)`` returns the exit code and the output, and may
    write files as the real command would."""

    def __init__(self, script: Script | None = None) -> None:
        self.script = script or (lambda argv, env, cwd: (0, ""))
        self.commands: list[tuple[list[str], dict[str, str]]] = []

    @property
    def kind(self) -> Literal["native"]:
        return "native"

    def to_sandbox_path(self, host_path: Path) -> str:
        return host_path.as_posix()

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: str,
        env: Mapping[str, str] | None = None,
        timeout_s: float,
        stdin: bytes | None = None,
        log_path: Path | None = None,
    ) -> ExecResult:
        self.commands.append((list(argv), dict(env or {})))
        code, output = self.script(list(argv), env or {}, Path(cwd))
        return ExecResult(
            argv=list(argv), exit_code=code, stdout=output, stderr="", duration_s=0.1,
            log_path=log_path or Path("x.log"),
        )  # fmt: skip

    def commands_of(self, program: str) -> list[list[str]]:
        """The commands run through the wrappers' prelude, from ``program`` on."""
        return [argv[4:] for argv, _ in self.commands if argv[:2] == ["sh", "-c"]
                and argv[4] == program]  # fmt: skip


def _context(tmp_path: Path, script: Script | None = None, **lang: object) -> AdapterContext:
    for name in ("work", "build", "cache", "logs"):
        (tmp_path / name).mkdir(exist_ok=True)
    return AdapterContext(
        worktree=tmp_path / "work",
        build_dir=tmp_path / "build",
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
        executor=ScriptedExecutor(script),  # type: ignore[arg-type]
        lang=LanguageConfig(language="c", standard="c89", build_system="make", **lang),  # type: ignore[arg-type]
    )


def _executor(ctx: AdapterContext) -> ScriptedExecutor:
    assert isinstance(ctx.executor, ScriptedExecutor)
    return ctx.executor


def _log_call(env: Mapping[str, str], tool: str, cwd: str, *args: str) -> None:
    """Write a call log as capture.sh would."""
    capture = Path(env["CODECOREMORPH_CAPTURE"])
    number = len(list(capture.iterdir()))
    data = b"".join(f"{part}\0".encode() for part in [cwd, *args])
    (capture / f"{tool}.{number:04d}").write_bytes(data)


PLAIN = b"OBJS = a.o\nlibx.a: $(OBJS)\n\tar cq libx.a $(OBJS)\ntest: libx.a\n\t./run-tests\n"


# --------------------------------------------------------------------------- configure, build


def test_a_plain_makefile_needs_no_configure_but_one_build(tmp_path: Path) -> None:
    """The index needs compile commands after configure, and only a build shows them."""

    def script(argv: list[str], env: Mapping[str, str], cwd: Path) -> tuple[int, str]:
        if "make" in argv:
            _log_call(env, "cc", cwd.as_posix(), "-c", "a.c", "-o", "a.o")
            _log_call(env, "ar", cwd.as_posix(), "cq", "libx.a", "a.o")
        return 0, ""

    ctx = _context(tmp_path, script)
    (ctx.worktree / "Makefile").write_bytes(PLAIN)
    (ctx.worktree / "a.c").write_bytes(b"int a;\n")
    make = MakeBuild(ctx)
    assert make.configure().success
    executor = _executor(ctx)
    assert executor.commands_of("sh") == []  # no ./configure
    (build,) = executor.commands_of("make")
    tools = ctx.cache_dir / "helpers" / "capture"
    assert build[1].startswith("-j")
    assert build[2:5] == [f"CC={(tools / 'cc').as_posix()}", f"CXX={(tools / 'c++').as_posix()}",
                          f"AR={(tools / 'ar').as_posix()}"]  # fmt: skip
    _, env = executor.commands[-1]
    assert env["CODECOREMORPH_TOOLS"] == tools.as_posix()
    assert env["CODECOREMORPH_CC_EXTRA"] == "-fdiagnostics-absolute-paths"
    # The compile commands are the worktree's; the targets are the capture's.
    (command,) = json.loads((ctx.cache_dir / "compile_commands.json").read_text("utf-8"))
    assert command["file"] == (ctx.worktree / "a.c").as_posix()
    (library,) = make.targets()
    assert (library.name, library.files, library.artifacts) == ("x", ["a.c"], ["libx.a"])
    assert make.configure().success
    assert len(executor.commands_of("make")) == 1  # built once: the commands exist now


def test_configure_runs_when_its_inputs_change(tmp_path: Path) -> None:
    ctx = _context(tmp_path, configure_args=["--disable-shared"])
    (ctx.worktree / "configure").write_bytes(b"#!/bin/sh\n")
    (ctx.worktree / "Makefile.in").write_bytes(PLAIN)
    (ctx.cache_dir / "compile_commands.json").write_text("[]", encoding="utf-8")
    make = MakeBuild(ctx)
    executor = _executor(ctx)
    assert make.configure().success
    assert executor.commands_of("sh") == [["sh", "./configure", "--disable-shared"]]
    _, env = executor.commands[0]
    assert "CODECOREMORPH_CAPTURE" not in env  # configure's own test compiles aren't the build's
    make.configure()
    assert len(executor.commands_of("sh")) == 1
    (ctx.worktree / "Makefile.in").write_bytes(PLAIN + b"# edited\n")
    make.build()  # a build after a change to the build files configures first
    assert len(executor.commands_of("sh")) == 2


def test_autotools_without_configure_regenerate_first(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    (ctx.worktree / "configure.ac").write_bytes(b"AC_INIT([x], [1])\n")
    (ctx.worktree / "Makefile.am").write_bytes(b"lib_LIBRARIES = libx.a\n")
    (ctx.cache_dir / "compile_commands.json").write_text("[]", encoding="utf-8")
    MakeBuild(ctx).configure()
    executor = _executor(ctx)
    assert [argv[4:] for argv, _ in executor.commands][:2] == [
        ["autoreconf", "-fi"],
        ["sh", "./configure"],
    ]


def _steps(ctx: AdapterContext) -> list[str]:
    return [argv[4] if argv[4] != "sh" else argv[5] for argv, _ in _executor(ctx).commands]


def test_a_changed_makefile_am_regenerates_the_build_files(tmp_path: Path) -> None:
    """autoreconf's configure stays in the mirror, so a change to Makefile.am (a C port's
    scaffold writes one, D-70) runs it again, not ./configure alone over a stale Makefile.in."""
    ctx = _context(tmp_path)
    (ctx.worktree / "configure.ac").write_bytes(b"AC_INIT([x], [1])\n")
    (ctx.worktree / "Makefile.am").write_bytes(b"lib_LTLIBRARIES = libx.la\n")
    (ctx.cache_dir / "compile_commands.json").write_text("[]", encoding="utf-8")
    make = MakeBuild(ctx)
    make.configure()
    make.mirror.path("configure").write_bytes(b"#!/bin/sh\n")  # what autoreconf wrote
    (ctx.worktree / "Makefile.am").write_bytes(b"lib_LTLIBRARIES = libx.la\n# edited\n")
    make.build()
    assert _steps(ctx)[:2] == ["autoreconf", "./configure"]
    assert _steps(ctx)[2:5] == ["autoreconf", "./configure", "make"]


def test_a_committed_configure_is_used_until_the_autotools_inputs_change(
    tmp_path: Path,
) -> None:
    """A committed aclocal.m4 may hold macros autoreconf can't find again, so the baseline uses
    the project's own configure."""
    ctx = _context(tmp_path)
    for name in ("configure.ac", "configure", "Makefile.in"):
        (ctx.worktree / name).write_bytes(b"x\n")
    (ctx.worktree / "Makefile.am").write_bytes(b"lib_LTLIBRARIES = libx.la\n")
    (ctx.cache_dir / "compile_commands.json").write_text("[]", encoding="utf-8")
    make = MakeBuild(ctx)
    make.configure()
    assert _steps(ctx) == ["./configure"]
    (ctx.worktree / "Makefile.in").write_bytes(b"y\n")  # not autoreconf's input
    make.build()
    assert _steps(ctx)[1:] == ["./configure", "make"]
    (ctx.worktree / "Makefile.am").write_bytes(b"lib_LTLIBRARIES = libx.la\n# edited\n")
    make.build()
    assert _steps(ctx)[3:] == ["autoreconf", "./configure", "make"]


def test_a_failed_configure_says_why(tmp_path: Path) -> None:
    ctx = _context(
        tmp_path,
        lambda argv, env, cwd: (1, "checking for gcc... no\nconfigure: error: no acceptable C "
                                   "compiler found in $PATH\n"),
    )  # fmt: skip
    (ctx.worktree / "configure").write_bytes(b"#!/bin/sh\n")
    result = MakeBuild(ctx).configure()
    assert not result.success
    (error,) = result.errors
    assert (error.file_path, error.message) == (
        "configure",
        "no acceptable C compiler found in $PATH",
    )
    assert not (ctx.cache_dir / "make-configured").exists()


def test_build_errors_name_worktree_files(tmp_path: Path) -> None:
    mirror = (tmp_path / "cache" / "make-mirror").as_posix()
    output = (
        f"{mirror}/src/hash.c:12:5: error: use of undeclared identifier 'x'\n"
        "make: *** No rule to make target 'huffman.o', needed by 'libbz2.a'.  Stop.\n"
    )
    ctx = _context(tmp_path, lambda argv, env, cwd: (2, output) if "make" in argv else (0, ""))
    (ctx.worktree / "Makefile").write_bytes(PLAIN)
    (ctx.cache_dir / "compile_commands.json").write_text("[]", encoding="utf-8")
    result = MakeBuild(ctx).build()
    assert not result.success
    assert [(d.file_path, d.line, d.message) for d in result.errors] == [
        ("src/hash.c", 12, "use of undeclared identifier 'x'"),
        (None, None, "No rule to make target 'huffman.o', needed by 'libbz2.a'"),
    ]


def test_clean_forgets_the_build(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    (ctx.worktree / "Makefile").write_bytes(PLAIN)
    make = MakeBuild(ctx)
    make.configure()
    assert (ctx.cache_dir / "make-mirror" / "Makefile").is_file()
    make.clean()
    assert not (ctx.cache_dir / "make-mirror").exists()
    assert not (ctx.cache_dir / "compile_commands.json").exists()


# --------------------------------------------------------------------------- tests


def _with_makefile(tmp_path: Path, text: bytes, script: Script | None = None, **lang: object):
    ctx = _context(tmp_path, script, **lang)
    (ctx.worktree / "Makefile").write_bytes(text)
    make = MakeBuild(ctx)
    make.mirror.sync()
    return ctx, make


@pytest.mark.parametrize(
    ("text", "targets"),
    [(b"check: all\n\t./t\ntest: check\n", ["check"]), (PLAIN, ["test"]), (b"all:\n", [])],
)
def test_the_test_target(tmp_path: Path, text: bytes, targets: list[str]) -> None:
    _, make = _with_makefile(tmp_path, text)
    assert make.test_targets() == targets


def test_the_manifest_names_the_test_targets(tmp_path: Path) -> None:
    _, make = _with_makefile(tmp_path, PLAIN, test_targets=["regress", "smoke"])
    assert make.test_targets() == ["regress", "smoke"]


def test_automake_results_give_one_test_per_program(tmp_path: Path) -> None:
    def script(argv: list[str], env: Mapping[str, str], cwd: Path) -> tuple[int, str]:
        tests = cwd / "tests"
        tests.mkdir(exist_ok=True)
        (tests / "test-reader.trs").write_text(":test-result: PASS\n", encoding="utf-8")
        (tests / "test-version.trs").write_text(":test-result: FAIL\n", encoding="utf-8")
        (tests / "test-skip.trs").write_text(":test-result: SKIP\n", encoding="utf-8")
        return 2, ""

    ctx, make = _with_makefile(tmp_path, b"check:\n\t$(MAKE) -C tests check\n", script)
    stale = make.mirror.root / "tests" / "old.trs"
    stale.parent.mkdir(parents=True)
    stale.write_text(":test-result: FAIL\n", encoding="utf-8")
    report = make.run_tests()
    assert {c.test_id: c.status for c in report.cases} == {
        "make:tests/test-reader": TestStatus.PASSED,
        "make:tests/test-skip": TestStatus.SKIPPED,
        "make:tests/test-version": TestStatus.FAILED,
    }  # the stale result of a test that no longer runs is gone
    (ctx.cache_dir / TARGETS_FILE).write_text(
        json.dumps(
            [{"name": "test-reader", "kind": "executable", "files": ["tests/test-reader.c"]}]
        ),
        encoding="utf-8",
    )
    entries = {e.test_id: e.runs for e in make.test_entries()}
    assert entries["make:tests/test-reader"] == "test-reader"
    assert entries["make:tests/test-version"] is None  # no program of that name
    chosen = make.run_tests(TestSelection(test_ids=["make:tests/test-reader"]))
    assert [c.test_id for c in chosen.cases] == ["make:tests/test-reader"]


@pytest.mark.parametrize(("code", "status"), [(0, TestStatus.PASSED), (2, TestStatus.FAILED)])
def test_without_a_harness_the_exit_status_is_one_test(
    tmp_path: Path, code: int, status: TestStatus
) -> None:
    _, make = _with_makefile(tmp_path, PLAIN, lambda argv, env, cwd: (code, ""))
    (case,) = make.run_tests().cases
    assert (case.test_id, case.status) == ("make:test", status)


def test_a_project_without_a_test_target_has_no_tests(tmp_path: Path) -> None:
    _, make = _with_makefile(tmp_path, b"all:\n")
    assert make.run_tests().cases == []


# --------------------------------------------------------------------------- parsers, files


@pytest.mark.parametrize(
    ("text", "status"),
    [
        (":test-result: PASS\n:test-result: XFAIL\n", TestStatus.PASSED),
        (":test-result: PASS\n:test-result: ERROR\n", TestStatus.FAILED),
        (":test-result: XPASS\n", TestStatus.FAILED),  # an expected failure that passed
        (":test-result: SKIP\n:test-result: SKIP\n", TestStatus.SKIPPED),
        (":global-test-result: PASS\n", TestStatus.ERROR),  # no result: the test broke
    ],
)
def test_trs_files(text: str, status: TestStatus) -> None:
    assert parse_trs(text) == status


def test_make_errors() -> None:
    mapper = PathMapper("/m", "/m", case_insensitive=False)
    found = parse_make_output(
        "Makefile:31: *** missing separator.  Stop.\n"
        "make[1]: *** [Makefile:12: lib.a] Error 1\n"  # a failed recipe: its own output says why
        "make[2]: *** No rule to make target 'x.o'.  Stop.\n",
        mapper,
        "/m",
    )
    assert [(d.file_path, d.line, d.message, d.severity) for d in found] == [
        ("Makefile", 31, "missing separator", Severity.ERROR),
        (None, None, "No rule to make target 'x.o'", Severity.ERROR),
    ]


def test_configure_errors() -> None:
    (error,) = parse_configure_output("checking...\nconfigure: error: zlib not found\n")
    assert error.message == "zlib not found"


@pytest.mark.parametrize(
    ("path", "make"),
    [("Makefile", True), ("src/GNUmakefile", True), ("Makefile.am", True), ("m4/ax.m4", True),
     ("rules.mk", True), ("configure.ac", True), ("configure", True), ("Makefile.txt", False),
     ("CMakeLists.txt", False), ("src/make.c", False)],
)  # fmt: skip
def test_make_files(path: str, make: bool) -> None:
    assert is_make_file(path) is make


def test_standards_in_flags() -> None:
    text = "CFLAGS = -O2 -std=c99\nCXXFLAGS = -std=gnu++11\nX = -std=gnu89\n"
    assert standards_in([text], cxx=False) == ["c99", "c89"]
    assert standards_in([text], cxx=True) == ["cpp11"]
    assert standards_in(["CFLAGS = -O2 -ansi -pedantic\nY = --no-ansi-x\n"], cxx=False) == ["c89"]


TAP_RUN = """\
./tests/test_sbuf
ok 1 - a new buffer is empty
not ok 2 - append succeeds
#   at tests/test_sbuf.c line 14
ok 3 - grows # SKIP no memory limit here
not ok 4 - a known bug # TODO fix the overflow
ok 5
1..5
./tests/test_htab
ok 1 - add a word
1..3
./wordfreq 3 < tests/sample.txt > tests/sample.got
cmp tests/sample.got tests/sample.out
"""
PROGRAMS = {"./tests/test_sbuf": "tests/test_sbuf", "./tests/test_htab": "tests/test_htab",
            "./wordfreq": "wordfreq"}  # fmt: skip


def test_tap_points_belong_to_the_program_make_ran() -> None:
    cases = {c.test_id: c.status for c in parse_tap_output(TAP_RUN, PROGRAMS, "test")}
    assert cases == {
        "tap:tests/test_sbuf/a new buffer is empty": TestStatus.PASSED,
        "tap:tests/test_sbuf/append succeeds": TestStatus.FAILED,
        "tap:tests/test_sbuf/grows": TestStatus.SKIPPED,
        "tap:tests/test_sbuf/a known bug": TestStatus.PASSED,  # TODO: an expected failure
        "tap:tests/test_sbuf/#5": TestStatus.PASSED,
        "tap:tests/test_htab/add a word": TestStatus.PASSED,
        "tap:tests/test_htab/(plan)": TestStatus.ERROR,  # planned 3, ran 1
    }


def test_tap_without_an_echoed_program_and_repeated_descriptions() -> None:
    cases = parse_tap_output("ok 1 - same\nok 2 - same\nBail out! no database\n", {}, "check")
    assert [(c.test_id, c.status) for c in cases] == [
        ("tap:check/same", TestStatus.PASSED),
        ("tap:check/same (#2)", TestStatus.PASSED),
        ("tap:check/(bail out)", TestStatus.ERROR),
    ]


def test_tap_tests_and_the_targets_exit_status(tmp_path: Path) -> None:
    """The target's exit status stays a test of its own: it also covers the checks that
    print no TAP (here a cmp of the program's output that failed)."""

    def script(argv: list[str], env: Mapping[str, str], cwd: Path) -> tuple[int, str]:
        # The test target builds the test program first: its link is in the capture.
        _log_call(env, "cc", cwd.as_posix(), "-o", "tests/test_sbuf", "tests/test_sbuf.o")
        return 2, TAP_RUN

    _, make = _with_makefile(tmp_path, PLAIN, script)
    cases = {c.test_id: c.status for c in make.run_tests().cases}
    assert cases["tap:tests/test_sbuf/a new buffer is empty"] is TestStatus.PASSED
    assert cases["make:test"] is TestStatus.FAILED
    runs = {e.test_id: e.runs for e in make.test_entries()}
    assert runs["tap:tests/test_sbuf/append succeeds"] == "test_sbuf"
    assert runs["make:test"] is None


def test_a_make_build_is_found_by_its_files(tmp_path: Path) -> None:
    assert not has_make_build(tmp_path)
    (tmp_path / "configure.ac").write_bytes(b"")
    assert has_make_build(tmp_path)


def test_the_index_s_model_runs_the_tests_once_when_none_ran_here(tmp_path: Path) -> None:
    """A baseline that reused another run's test results ran no test in this cache, so the
    test programs never compiled under the capture: the model runs them once (D-69)."""
    calls: list[str] = []

    def script(argv: list[str], env: Mapping[str, str], cwd: Path) -> tuple[int, str]:
        if "test" in argv:
            calls.append("test")
            _log_call(env, "cc", cwd.as_posix(), "-o", "tests/test_sbuf", "tests/test_sbuf.o")
            return 0, TAP_RUN
        return 0, ""

    _, make = _with_makefile(tmp_path, PLAIN, script)
    targets, entries = make.model()
    assert calls == ["test"]
    assert "test_sbuf" in {t.name for t in targets}
    assert "tap:tests/test_sbuf/a new buffer is empty" in {e.test_id for e in entries}
    make.model()
    assert calls == ["test"]  # once: the test run's capture is there now
