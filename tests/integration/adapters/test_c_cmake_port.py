"""A C port of a CMake project with the real toolchain (D-89): the ``rust-module`` scaffold
puts the crate into CMake through Corrosion (and leaves the project's other Makefile alone),
the library's file is ported by hand, then the ``rust-bin`` scaffold takes the program's
executable out of the CMake files and Corrosion imports the Rust program under its name, so
CTest runs Rust for both. The project links its library with the plain
``target_link_libraries`` signature, as cJSON does, which a keyword one would break. Marked
``docker``."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from codecoremorph.adapters.base import AdapterContext
from codecoremorph.adapters.c import CAdapter
from codecoremorph.adapters.rust import RustAdapter
from codecoremorph.core.errors import SandboxError
from codecoremorph.core.ids import new_run_id
from codecoremorph.core.models import CodemodSpec, LanguageConfig, TestStatus

pytestmark = pytest.mark.docker

PROJECT = {
    "CMakeLists.txt": """\
cmake_minimum_required(VERSION 3.16)
project(calc C)
enable_testing()
add_library(calc STATIC include/calc.h src/calc.c)
target_include_directories(calc PUBLIC include)
target_link_libraries(calc m)
add_executable(calctool tools/calctool.c)
target_link_libraries(calctool calc)
add_executable(test_calc tests/test_calc.c)
target_link_libraries(test_calc calc)
add_test(NAME test_calc COMMAND test_calc)
add_test(NAME calctool_adds COMMAND calctool 2 3)
set_tests_properties(calctool_adds PROPERTIES PASS_REGULAR_EXPRESSION "^5")
""",
    "Makefile": "libcalc.a: src/calc.o\n\tar rcs $@ $^\n",
    "include/calc.h": "#ifndef CALC_H\n#define CALC_H\nint calc_add(int a, int b);\n#endif\n",
    "src/calc.c": '#include "calc.h"\nint calc_add(int a, int b) { return a + b; }\n',
    "tools/calctool.c": (
        '#include <stdio.h>\n#include <stdlib.h>\n#include "calc.h"\n'
        "int main(int argc, char **argv) {\n"
        "    if (argc < 3) return 2;\n"
        '    printf("%d\\n", calc_add(atoi(argv[1]), atoi(argv[2])));\n'
        "    return 0;\n}\n"
    ),
    "tests/test_calc.c": (
        '#include "calc.h"\nint main(void) { return calc_add(2, 3) == 5 ? 0 : 1; }\n'
    ),
}
MODULE = (
    "//! Ported from src/calc.c.\n\n/// `calc_add`.\n"
    "pub fn add(a: i32, b: i32) -> i32 {\n    a.wrapping_add(b)\n}\n"
)
BRIDGE = (
    "//! The C functions of src/calc.c.\n\nuse std::ffi::c_int;\n\n"
    '#[no_mangle]\npub extern "C" fn calc_add(a: c_int, b: c_int) -> c_int {\n'
    "    crate::calc::add(a, b)\n}\n"
)
MAIN = """\
//! The calctool program, ported from tools/calctool.c.

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() < 3 {
        std::process::exit(2);
    }
    let number = |text: &str| text.trim().parse::<i32>().unwrap_or(0);
    println!("{}", calc_rs::calc::add(number(&args[1]), number(&args[2])));
}
"""


@dataclass
class Contexts:
    c: AdapterContext
    rust: AdapterContext


@pytest.fixture
def ctx(tmp_path: Path) -> Iterator[Contexts]:
    from codecoremorph.sandbox.docker.executor import DockerExecutor, DockerSettings
    from codecoremorph.sandbox.provisioning import resolve_image

    work = tmp_path / "calc"
    for rel, text in PROJECT.items():
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        (work / rel).write_bytes(text.encode("utf-8"))
    for name in ("build", "cache", "logs"):
        (tmp_path / name).mkdir()
    rust_lang = LanguageConfig(
        language="rust",
        standard="rust2021",
        target_standard="rust2021",
        port_source="c",
        port_build_system="cmake",
        build_timeout_s=900,
    )
    try:
        image = resolve_image(RustAdapter().toolchain_requirements(rust_lang), None)
    except SandboxError as exc:
        pytest.skip(f"no Rust toolchain image: {exc}")
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
    try:
        c = AdapterContext(
            worktree=work,
            build_dir=tmp_path / "build",
            cache_dir=tmp_path / "cache",
            log_dir=tmp_path / "logs",
            executor=executor,
            lang=LanguageConfig(
                language="c",
                standard="c89",
                build_system="cmake",
                test_dirs=["tests"],
                public_api=["include/**"],
                build_timeout_s=900,
                test_timeout_s=300,
            ),
        )
        rust = replace(
            c, cache_dir=tmp_path / "cache" / "rust", log_dir=tmp_path / "logs" / "rust",
            lang=rust_lang,
        )  # fmt: skip
        yield Contexts(c, rust)
    finally:
        executor.stop()


def _tests(c: CAdapter, ctx: Contexts) -> dict[str, TestStatus]:
    assert c.configure(ctx.c).success
    built = c.build(ctx.c)
    assert built.success, built.diagnostics
    return {t.test_id: t.status for t in c.run_tests(ctx.c).cases}


def test_a_cmake_projects_library_and_program_go_to_rust(ctx: Contexts) -> None:
    c, rust = CAdapter(), RustAdapter()
    work = ctx.c.worktree
    assert c.configure(ctx.c).success  # compile_commands.json, for bindgen

    module = CodemodSpec(
        tool="rust-module",
        options={
            "language": "c",
            "crate": "calc_rs",
            "module": "calc",
            "build_target": "calc",
            "archive": "libcalc.a",
            "headers": "include/calc.h",
        },
    )
    scaffold = rust.run_codemods(ctx.rust, [module], ["CMakeLists.txt", "src/calc.c"])
    assert not scaffold.diagnostics, scaffold.diagnostics
    assert "Makefile" not in scaffold.changed_files  # the build that runs is CMake's
    top = (work / "CMakeLists.txt").read_text(encoding="utf-8")
    assert "corrosion_import_crate(MANIFEST_PATH rust/Cargo.toml FROZEN)" in top
    assert "set_property(TARGET calc APPEND PROPERTY LINK_LIBRARIES calc_rs)" in top

    # The library's file: Rust takes over, and the target keeps its header alone.
    (work / "rust/calc_rs/src/calc.rs").write_text(MODULE, encoding="utf-8")
    (work / "rust/calc_rs/src/bridge/calc.rs").write_text(BRIDGE, encoding="utf-8")
    (work / "src/calc.c").unlink()
    top = top.replace(
        "add_library(calc STATIC include/calc.h src/calc.c)",
        "add_library(calc STATIC include/calc.h)\n"
        "set_target_properties(calc PROPERTIES LINKER_LANGUAGE C)",
    )
    (work / "CMakeLists.txt").write_text(top, encoding="utf-8")
    assert rust.configure(ctx.rust).success
    statuses = _tests(c, ctx)
    assert statuses["ctest:test_calc"] is TestStatus.PASSED  # C test, Rust library
    assert statuses["ctest:calctool_adds"] is TestStatus.PASSED  # C program, Rust library

    # The program: its executable leaves the CMake files, and the Rust bin takes its name.
    program = CodemodSpec(
        tool="rust-bin",
        options={
            "language": "c",
            "crate": "calctool_rs",
            "bin": "calctool",
            "build_target": "calctool",
            "artifact": "calctool",
            "dependencies": "calc_rs",
        },
    )
    made = rust.run_codemods(ctx.rust, [program], ["CMakeLists.txt", "tools/calctool.c"])
    assert not made.diagnostics, made.diagnostics
    top = (work / "CMakeLists.txt").read_text(encoding="utf-8")
    assert "add_executable(calctool" not in top
    assert "target_link_libraries(calctool" not in top
    assert "add_test(NAME calctool_adds COMMAND calctool 2 3)" in top
    (work / "rust/calctool_rs/src/main.rs").write_text(MAIN, encoding="utf-8")
    (work / "tools/calctool.c").unlink()
    statuses = _tests(c, ctx)
    assert statuses["ctest:calctool_adds"] is TestStatus.PASSED  # the Rust program now
    assert statuses["ctest:test_calc"] is TestStatus.PASSED
