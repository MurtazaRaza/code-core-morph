"""Diagnostic parsers against captured and representative build output (SPEC §11.1)."""

from __future__ import annotations

from pathlib import Path

from codecoremorph.adapters.cpp.diagnostics import parse_cmake_output, parse_compiler_output
from codecoremorph.adapters.cpp.paths import PathMapper
from codecoremorph.core.models import DiagnosticOrigin, Severity

DATA = Path(__file__).resolve().parents[3] / "data" / "cpp" / "diagnostics"


def _read(name: str) -> str:
    return (DATA / name).read_bytes().decode("utf-8")


def test_clang_and_lld_on_windows() -> None:
    """Captured from clang 18 (llvm-mingw) and lld through CMake + Ninja."""
    root = "E:/codecoremorph-home/probe/golden"
    mapper = PathMapper(root, f"{root}/build", case_insensitive=True)
    diags = parse_compiler_output(_read("clang_lld_windows.log"), mapper, base=f"{root}/build")

    assert [(d.file_path, d.line, d.severity) for d in diags] == [
        (None, None, Severity.ERROR),
        ("src/inc/shapes.h", 6, Severity.ERROR),
        ("src/main.cpp", 7, Severity.ERROR),
        ("src/main.cpp", 9, Severity.ERROR),
    ]
    link = diags[0]
    assert link.origin is DiagnosticOrigin.LINKER
    assert link.code == "undefined-reference"
    assert link.message == "undefined symbol: helper(int)"
    assert link.notes[0].message.startswith("referenced by CMakeFiles/linkfail.dir")

    include_chain = diags[1].notes
    assert [(n.file_path, n.line, n.message) for n in include_chain] == [
        ("src/main.cpp", 1, "included from here")
    ]
    assert diags[1].column == 30

    abstract = diags[2]
    assert [(n.file_path, n.line) for n in abstract.notes] == [("src/inc/shapes.h", 3)]

    # A note in a system header keeps its location in the message.
    note = diags[3].notes[0]
    assert note.file_path is None
    assert "unique_ptr.h:190:55: copy constructor is implicitly deleted" in note.message


def test_gcc_and_gnu_ld_in_docker() -> None:
    mapper = PathMapper("/workspace", "/build", case_insensitive=False)
    diags = parse_compiler_output(_read("gcc_ld_linux.log"), mapper, base="/build")

    assert [(d.file_path, d.line, d.severity, d.code) for d in diags] == [
        ("src/util/legacy.h", 12, Severity.ERROR, None),
        ("src/main.cpp", 9, Severity.WARNING, "-Wunused-variable"),
        ("src/main.cpp", 11, Severity.ERROR, "-Wregister"),
        ("src/tool.cpp", 7, Severity.ERROR, "undefined-reference"),
        (None, None, Severity.ERROR, "duplicate-symbol"),
    ]
    legacy = diags[0]
    assert [(n.file_path, n.line) for n in legacy.notes] == [
        ("src/util/strings.h", 4),
        ("src/main.cpp", 2),
        ("src/util/legacy.h", 3),
    ]
    assert diags[2].notes[0].message.startswith("/usr/include/c++/13/bits/stl_algo.h:4500")
    assert diags[3].origin is DiagnosticOrigin.LINKER


def test_msvc_best_effort() -> None:
    mapper = PathMapper("C:/work/proj", "C:/work/proj/build", case_insensitive=True)
    diags = parse_compiler_output(_read("msvc.log"), mapper)

    assert [(d.file_path, d.line, d.column, d.severity, d.code) for d in diags] == [
        ("src/main.cpp", 12, None, Severity.ERROR, "C2065"),
        ("src/main.cpp", 20, 9, Severity.WARNING, "C4189"),
        ("include/legacy.h", 5, 10, Severity.ERROR, "C1083"),
        (None, None, None, Severity.ERROR, "LNK2019"),
        (None, None, None, Severity.ERROR, "LNK1120"),
    ]
    assert diags[3].origin is DiagnosticOrigin.LINKER


def test_cmake_errors_and_warnings() -> None:
    root = "E:/w"
    mapper = PathMapper(root, f"{root}/build", case_insensitive=True)
    diags = parse_cmake_output(_read("cmake_error.log"), mapper, root)

    assert [(d.file_path, d.line, d.severity) for d in diags] == [
        ("CMakeLists.txt", 4, Severity.WARNING),
        ("CMakeLists.txt", 3, Severity.ERROR),
        ("CMakeLists.txt", 3, Severity.ERROR),
    ]
    assert diags[0].message == "deprecated option used (in message)"
    assert diags[1].message == "Cannot find source file: missing.cpp (in add_executable)"
    assert all(d.origin is DiagnosticOrigin.BUILD_SYSTEM for d in diags)


def test_cmake_error_outside_the_worktree_keeps_its_path() -> None:
    mapper = PathMapper("/workspace", "/build", case_insensitive=False)
    text = "CMake Error at /opt/cmake/share/Modules/FindX.cmake:9 (message):\n  boom\n"
    (diag,) = parse_cmake_output(text, mapper, "/workspace")
    assert diag.file_path is None
    assert diag.message == "/opt/cmake/share/Modules/FindX.cmake:9: boom (in message)"


def test_codes_dedupe_and_analyzer_origin() -> None:
    mapper = PathMapper("/workspace", "/build", case_insensitive=False)
    text = "\n".join(
        [
            "/workspace/a.h:1:1: error: shared problem [-Werror,-Wshadow]",
            "/workspace/a.h:1:1: note: first note",
            "/workspace/a.h:1:1: error: shared problem [-Werror,-Wshadow]",
            "/workspace/a.h:1:1: note: repeated note is dropped with its duplicate",
            "/workspace/b.cpp:3:7: warning: use 'using' [modernize-use-using]",
            "clang++: error: no such file or directory: 'gone.cpp'",
            "/build/gen/x.h:2:1: warning: generated header",
        ]
    )
    diags = parse_compiler_output(text, mapper)
    assert [d.message for d in diags] == [
        "shared problem",
        "use 'using'",
        "no such file or directory: 'gone.cpp'",
        "/build/gen/x.h:2:1: generated header",
    ]
    assert diags[0].code == "-Wshadow"
    assert len(diags[0].notes) == 1
    assert diags[1].origin is DiagnosticOrigin.ANALYZER
    assert diags[1].code == "modernize-use-using"
    assert diags[2].file_path is None


def test_a_header_error_keeps_the_same_copy_in_any_build_order() -> None:
    """Every compile including a broken header reports its error, with its own include chain;
    a parallel build prints the compiles in the order they finish."""
    mapper = PathMapper("/workspace", "/build", case_insensitive=False)
    via_transaction = [
        "In file included from /workspace/src/transaction.cpp:1:",
        "In file included from /workspace/include/transaction.h:8:",
        "/workspace/include/compat.h:27:2: error: expected ';' after class",
    ]
    via_parse = [
        "In file included from /workspace/src/parse.cpp:5:",
        "/workspace/include/compat.h:27:2: error: expected ';' after class",
    ]
    own = ["/workspace/src/parse.cpp:11:12: error: no viable conversion"]
    one = parse_compiler_output("\n".join([*via_parse, *own, *via_transaction]), mapper)
    other = parse_compiler_output("\n".join([*via_transaction, *via_parse, *own]), mapper)
    assert sorted(one, key=lambda d: d.file_path or "") == sorted(
        other, key=lambda d: d.file_path or ""
    )
    (header,) = [d for d in one if d.file_path == "include/compat.h"]
    assert [n.file_path for n in header.notes] == ["src/transaction.cpp", "include/transaction.h"]


def test_relative_paths_resolve_against_the_build_directory() -> None:
    mapper = PathMapper("/workspace", "/build/sub", case_insensitive=False)
    (diag,) = parse_compiler_output("../../workspace/src/x.cpp:4:2: error: bad", mapper)
    assert (diag.file_path, diag.line) == ("src/x.cpp", 4)
