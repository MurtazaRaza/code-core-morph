"""C characterization tests in the builds besides a plain Makefile (D-89), with the real
toolchain: a CMake project (a CTest test per program, its TAP read from CTest's JUnit report),
automake (``c-autotools``: the block in ``Makefile.am``, run from ``check-local``, its TAP read
from make's output since the harness reads ``.trs`` files) and a project whose ``configure``
writes the Makefile from ``Makefile.in``. In each, a case that holds passes and one that doesn't
fails, by the ID the case's function gives it, and the project's own tests still run. Marked
``docker``."""

from __future__ import annotations

import shutil
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from codecoremorph.adapters.base import AdapterContext
from codecoremorph.adapters.c import CAdapter
from codecoremorph.adapters.rust import RustAdapter
from codecoremorph.core.errors import SandboxError
from codecoremorph.core.ids import new_run_id
from codecoremorph.core.models import LanguageConfig, TestStatus

pytestmark = pytest.mark.docker

AUTOTOOLS = Path(__file__).resolve().parents[3] / "fixtures" / "c" / "c-autotools"
DIRECTORY = "tests/codecoremorph_characterization"

CALC = {
    "CMakeLists.txt": (
        "cmake_minimum_required(VERSION 3.16)\nproject(calc C)\nenable_testing()\n"
        "add_library(calc STATIC include/calc.h src/calc.c)\n"
        "target_include_directories(calc PUBLIC include)\n"
        "add_executable(test_calc tests/test_calc.c)\n"
        "target_link_libraries(test_calc calc)\n"
        "add_test(NAME test_calc COMMAND test_calc)\n"
    ),
    "include/calc.h": "#ifndef CALC_H\n#define CALC_H\nint calc_add(int a, int b);\n#endif\n",
    "src/calc.c": '#include "calc.h"\nint calc_add(int a, int b) { return a + b; }\n',
    "tests/test_calc.c": (
        '#include "calc.h"\nint main(void) { return calc_add(2, 3) == 5 ? 0 : 1; }\n'
    ),
}
CONFIGURED = {
    # configure writes the Makefile from Makefile.in, as mxml's does.
    "configure": '#!/bin/sh\nsed -e "s|@CC@|${CC:-cc}|g" -e "s|@AR@|${AR:-ar}|g" '
    "Makefile.in > Makefile\n",
    "Makefile.in": (
        "CC = @CC@\nAR = @AR@\nCFLAGS = -Iinclude\n\nall: libcalc.a\n\n"
        "libcalc.a: src/calc.o\n\t$(AR) rcs $@ src/calc.o\n\n"
        "src/calc.o: src/calc.c\n\t$(CC) $(CFLAGS) -c -o $@ src/calc.c\n\n"
        "test: all\n\t$(CC) $(CFLAGS) -o tests/test_calc tests/test_calc.c libcalc.a\n"
        "\t./tests/test_calc\n"
    ),
    "include/calc.h": CALC["include/calc.h"],
    "src/calc.c": CALC["src/calc.c"],
    "tests/test_calc.c": CALC["tests/test_calc.c"],
}


def _case_file(header: str, holds: str, wrong: str) -> str:
    return (
        f'#include "codecoremorph_tap.h"\n#include "{header}"\n\n'
        f"static void the_case_holds(void)\n{{\n    {holds}\n}}\n\n"
        f"static void a_wrong_value_fails(void)\n{{\n    {wrong}\n}}\n\n"
        "int main(void)\n{\n    CCM_RUN(the_case_holds);\n    CCM_RUN(a_wrong_value_fails);\n"
        "    return ccm_done();\n}\n"
    )


Make = Callable[[dict[str, str] | Path, str], AdapterContext]


@pytest.fixture
def make_ctx(tmp_path: Path) -> Iterator[Make]:
    from codecoremorph.sandbox.docker.executor import DockerExecutor, DockerSettings
    from codecoremorph.sandbox.provisioning import resolve_image

    rust_lang = LanguageConfig(language="rust", standard="rust2021", target_standard="rust2021")
    try:
        image = resolve_image(RustAdapter().toolchain_requirements(rust_lang), None)
    except SandboxError as exc:
        pytest.skip(f"no toolchain image: {exc}")
    executors: list[DockerExecutor] = []

    def make(project: dict[str, str] | Path, build_system: str) -> AdapterContext:
        work = tmp_path / "work"
        if isinstance(project, Path):
            shutil.copytree(project, work)
        else:
            for rel, text in project.items():
                (work / rel).parent.mkdir(parents=True, exist_ok=True)
                (work / rel).write_bytes(text.encode("utf-8"))
        for name in ("build", "cache", "logs"):
            (tmp_path / name).mkdir()
        executor = DockerExecutor(
            DockerSettings(
                image=image,
                run_id=new_run_id(),
                worktree=work,
                build_dir=tmp_path / "build",
                cache_dir=tmp_path / "cache",
                log_dir=tmp_path / "exec",
                toolchain_env="/opt/codecoremorph/toolchains/rust.env",
            )
        )
        executor.start()
        executors.append(executor)
        return AdapterContext(
            worktree=work,
            build_dir=tmp_path / "build",
            cache_dir=tmp_path / "cache",
            log_dir=tmp_path / "logs",
            executor=executor,
            lang=LanguageConfig(
                language="c",
                standard="c89",
                build_system=build_system,
                test_dirs=["tests"],
                public_api=["include/**"],
                build_timeout_s=900,
                test_timeout_s=300,
            ),
        )

    yield make
    for executor in executors:
        executor.stop()


def _characterize(ctx: AdapterContext, stem: str, text: str, source: str) -> dict[str, TestStatus]:
    c = CAdapter()
    assert c.configure(ctx).success
    assert c.build(ctx).success
    c.index(ctx)
    test = c.characterization_test_file(DIRECTORY, stem)
    (ctx.worktree / test).parent.mkdir(parents=True, exist_ok=True)
    (ctx.worktree / test).write_text(text, encoding="utf-8")
    assert c.characterization_unavailable(ctx) is None
    c.register_test_files(ctx, DIRECTORY, [test], [source])
    assert c.configure(ctx).success
    built = c.build(ctx)
    assert built.success, built.diagnostics
    return {t.test_id: t.status for t in c.run_tests(ctx).cases}


def test_a_cmake_projects_characterization_tests(make_ctx: Make) -> None:
    ctx = make_ctx(CALC, "cmake")
    text = _case_file(
        "calc.h", "CCM_CHECK_INT(calc_add(2, 3), 5);", "CCM_CHECK_INT(calc_add(1, 1), 3);"
    )
    statuses = _characterize(ctx, "calc", text, "src/calc.c")
    program = f"tap:{DIRECTORY}/calc_test"
    assert statuses[f"{program}/the_case_holds"] is TestStatus.PASSED
    assert statuses[f"{program}/a_wrong_value_fails"] is TestStatus.FAILED
    assert statuses["ctest:test_calc"] is TestStatus.PASSED  # the project's own test


def test_an_automake_projects_characterization_tests(make_ctx: Make) -> None:
    ctx = make_ctx(AUTOTOOLS, "make")
    text = _case_file(
        "kvconf.h",
        'CCM_CHECK_STR(kv_type_name(KV_INT), "int");',
        'CCM_CHECK_STR(kv_type_name(KV_STR), "str");',
    )
    statuses = _characterize(ctx, "value", text, "src/value.c")
    program = f"tap:{DIRECTORY}/value_test"
    assert statuses[f"{program}/the_case_holds"] is TestStatus.PASSED
    assert statuses[f"{program}/a_wrong_value_fails"] is TestStatus.FAILED
    assert [t for t in statuses if t.startswith("make:tests/")]  # automake's own tests ran


def test_a_configure_projects_characterization_tests(make_ctx: Make) -> None:
    ctx = make_ctx(CONFIGURED, "make")
    (ctx.worktree / "configure").chmod(0o755)
    text = _case_file(
        "calc.h", "CCM_CHECK_INT(calc_add(2, 3), 5);", "CCM_CHECK_INT(calc_add(1, 1), 3);"
    )
    statuses = _characterize(ctx, "calc", text, "src/calc.c")
    program = f"tap:{DIRECTORY}/calc_test"
    assert statuses[f"{program}/the_case_holds"] is TestStatus.PASSED
    assert statuses[f"{program}/a_wrong_value_fails"] is TestStatus.FAILED
    assert "make:test" in statuses
