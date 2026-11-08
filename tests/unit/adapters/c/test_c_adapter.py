"""The C adapter (D-68): what `codecoremorph init` reads from a C project, its standards, and
the tools a Make build needs."""

from __future__ import annotations

from pathlib import Path

import pytest

from codecoremorph.adapters.base import AdapterContext
from codecoremorph.adapters.c import CAdapter
from codecoremorph.adapters.c.detect import C_STANDARDS, guess_standard
from codecoremorph.adapters.cpp import CppAdapter
from codecoremorph.adapters.registry import AdapterRegistry
from codecoremorph.core.models import LanguageConfig, ProjectDetection


def write(root: Path, files: dict[str, str]) -> list[str]:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return sorted(files)


def detected(root: Path, files: dict[str, str]) -> ProjectDetection | None:
    return CAdapter().detect_project(root, write(root, files))


MAKEFILE = "CC=gcc\nCFLAGS=-O2 -std=c99\nlibx.a: x.o\n\tar cq $@ $^\ntest: libx.a\n"


def test_the_adapter_is_registered_as_c() -> None:
    adapter = AdapterRegistry.discover().get("c")
    assert isinstance(adapter, CAdapter)
    assert adapter.capabilities().standards == list(C_STANDARDS) == [
        "c89", "c99", "c11", "c17", "c23",
    ]  # fmt: skip
    # Characterization tests of a C project are TAP programs in its Make build (D-74).
    assert adapter.capabilities().register_test_files


def test_a_make_project(tmp_path: Path) -> None:
    found = detected(
        tmp_path,
        {"Makefile": MAKEFILE, "x.c": "int x;\n", "x.h": "int x;\n", "tests/t.c": "int main;\n"},
    )
    assert found is not None
    assert (found.language, found.build_system, found.standard) == ("c", "make", "c99")
    assert not found.standard_guessed
    assert found.test_dirs == ["tests"]
    assert any("Docker" in n for n in found.notes)


def test_cmake_wins_when_the_project_has_both(tmp_path: Path) -> None:
    found = detected(
        tmp_path,
        {
            "CMakeLists.txt": "project(zlib C)\nset(CMAKE_C_STANDARD 90)\n",
            "configure": "#!/bin/sh\n",
            "Makefile.in": "all:\n",
            "adler32.c": "int a;\n",
        },
    )
    assert found is not None
    assert (found.build_system, found.name) == ("cmake", "zlib")
    assert any("builds with CMake and with Make" in n for n in found.notes)


def test_an_autotools_project_is_named_by_ac_init(tmp_path: Path) -> None:
    found = detected(
        tmp_path,
        {"configure.ac": "AC_INIT([yaml], [0.2.5])\n", "Makefile.am": "", "src/api.c": "int a;\n"},
    )
    assert found is not None
    assert (found.build_system, found.name, found.source_dirs) == ("make", "yaml", ["src"])


def test_cpp_code_is_the_cpp_adapters(tmp_path: Path) -> None:
    files = {"Makefile": MAKEFILE, "a.c": "int a;\n", "b.cpp": "int b;\n"}
    assert detected(tmp_path, files) is None
    assert CppAdapter().detect_project(tmp_path, sorted(files)) is not None


def test_no_c_sources_no_project(tmp_path: Path) -> None:
    assert detected(tmp_path, {"Makefile": MAKEFILE, "x.h": "int x;\n"}) is None


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("int f(a) int a; { return a; }\n", "c89"),  # K&R definitions are fine in C89
        ("#include <stdint.h>\n", "c99"),
        ("void f(void) { for (int i = 0; i < 3; i++) {} }\n", "c99"),
        ("int x; // a comment\n", "c99"),
        ('const char *s = "http://x";\n', "c89"),  # // inside a string isn't a comment
        ('_Static_assert(1, "x");\n', "c11"),
    ],
)
def test_the_guessed_standard(code: str, expected: str) -> None:
    assert guess_standard([("a.c", code)])[0] == expected


def _context(tmp_path: Path, build_system: str) -> AdapterContext:
    for name in ("work", "build", "cache", "logs"):
        (tmp_path / name).mkdir(exist_ok=True)
    return AdapterContext(
        worktree=tmp_path / "work",
        build_dir=tmp_path / "build",
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
        executor=None,  # type: ignore[arg-type]
        lang=LanguageConfig(language="c", standard="c89", build_system=build_system),
    )


def test_the_project_standard_of_a_make_build(tmp_path: Path) -> None:
    ctx = _context(tmp_path, "make")
    write(ctx.worktree, {"Makefile": "CFLAGS = -std=gnu99\n", "lib/rules.mk": "X = -std=c90\n"})
    assert CAdapter().project_standard(ctx) == "c89"  # c90 is C89; the oldest counts


def test_the_project_standard_of_a_cmake_build(tmp_path: Path) -> None:
    ctx = _context(tmp_path, "cmake")
    write(ctx.worktree, {"CMakeLists.txt": "set_target_properties(z PROPERTIES C_STANDARD 11)\n"})
    assert CAdapter().project_standard(ctx) == "c11"


def test_a_make_build_needs_make_and_no_cmake() -> None:
    make = CAdapter().toolchain_requirements(LanguageConfig(language="c", standard="c89",
                                                            build_system="make"))  # fmt: skip
    names = [t.name for t in make.tools]
    assert "make" in names
    assert not {"cmake", "ninja"} & set(names)
    assert "Docker" in make.install_hint
    cmake = CppAdapter().toolchain_requirements(LanguageConfig(language="cpp", standard="cpp11"))
    assert "cmake" in [t.name for t in cmake.tools]
    assert make.image == cmake.image == "codecoremorph-cpp"


def test_c_abi_definitions_are_what_other_files_use(tmp_path: Path) -> None:
    """In C, a definition behind a header's declaration or another file's ``extern`` one, or
    one another file uses, is the C interface; static helpers and test code aren't (D-68)."""
    from codecoremorph.adapters.c.adapter import c_definitions
    from codecoremorph.store.index import IndexStore, SymbolRow

    def symbol(sid: str, file: str, line: int, *, kind: str = "function", definition: int = 1,
               boundary: str = "c_abi", signature: str | None = None) -> SymbolRow:  # fmt: skip
        name = sid.rsplit("@", 1)[-1]
        return (sid, name, name, kind, file, line, 1, line, 9, signature, "none", definition,
                boundary)  # fmt: skip

    def file(path: str, role: str) -> tuple[str, str, str, str, int, int, str, str]:
        return (path, "c", "0" * 64, role, 1, 0, "utf-8", "lf")

    index = tmp_path / "index.sqlite"
    with IndexStore.open(index) as store:
        store.write(
            files=[
                file("lib.h", "header"),
                file("lib.c", "source"),
                file("app.c", "source"),
                file("tests/t.c", "test"),
            ],
            symbols=[
                symbol("c:@F@api", "lib.h", 1, definition=0),
                symbol("c:@F@api", "lib.c", 2),
                symbol("c:@F@hidden", "app.c", 1, definition=0),  # app.c's own extern
                symbol("c:@F@hidden", "lib.c", 5),
                symbol("c:@level", "lib.c", 8, kind="variable"),
                symbol("c:lib.c@F@helper", "lib.c", 10),
                symbol("c:@S@node", "lib.c", 12, kind="struct", boundary="none"),
                symbol("c:@F@case", "tests/t.c", 1),
                # Stable Rust can't define these (D-70): they stay C.
                symbol("c:@F@say", "lib.c", 14, signature="void say(const char *, ...)"),
                symbol("c:@F@vsay", "lib.c", 16, signature="void vsay(const char *, va_list)"),
            ],
            references=[
                ("app.c", None, "c:@level", "read", 3, 1),
                ("app.c", None, "c:@S@node", "type", 4, 1),
                ("lib.c", None, "c:lib.c@F@helper", "call", 3, 1),
                ("app.c", None, "c:@F@case", "call", 5, 1),
                ("app.c", None, "c:@F@say", "call", 6, 1),
                ("app.c", None, "c:@F@vsay", "call", 7, 1),
            ],
            dependencies=[("lib.c", "lib.h", "include")],
        )
    sites = c_definitions(index, ["app.c", "lib.c", "lib.h", "tests/t.c"])
    assert [(s.file, s.line, s.text) for s in sites] == [
        ("lib.c", 2, "api, declared in lib.h"),
        ("lib.c", 5, "hidden, used by app.c"),
        ("lib.c", 8, "level, used by app.c"),
    ]


def test_a_c_test_file_s_cases_come_from_the_last_test_run(tmp_path: Path) -> None:
    """TAP points exist only when the test runs: each belongs to the program that printed it,
    at the file that defines its main (D-68)."""
    from codecoremorph.adapters.c.adapter import test_cases
    from codecoremorph.core.models import BuildTarget, TargetKind, TestEntry
    from codecoremorph.store.index import IndexStore

    index = tmp_path / "index.sqlite"
    with IndexStore.open(index) as store:
        store.write(
            files=[
                ("tests/t1.c", "c", "0" * 64, "test", 1, 0, "utf-8", "lf"),
                ("tests/helper.c", "c", "1" * 64, "test", 1, 0, "utf-8", "lf"),
            ],
            symbols=[
                (
                    "c:@F@main",
                    "main",
                    "main",
                    "function",
                    "tests/t1.c",
                    7,
                    1,
                    20,
                    1,
                    None,
                    "none",
                    1,
                    "c_abi",
                )
            ],
            references=[],
            dependencies=[],
        )
        store.set_build_targets(
            [
                BuildTarget(
                    name="t1", kind=TargetKind.EXECUTABLE, files=["tests/helper.c", "tests/t1.c"]
                )
            ]
        )
        store.set_test_entries(
            [
                TestEntry(test_id="tap:tests/t1/b works", runs="t1"),
                TestEntry(test_id="tap:tests/t1/a works", runs="t1"),
                TestEntry(test_id="make:test", runs=None),
            ]
        )
    sites = test_cases(index, ["tests/helper.c", "tests/t1.c"])
    assert [(s.file, s.line, s.text) for s in sites] == [
        ("tests/t1.c", 7, "tap:tests/t1/a works"),
        ("tests/t1.c", 7, "tap:tests/t1/b works"),
    ]
    assert test_cases(index, ["tests/helper.c"]) == []


def test_a_c_test_s_checks_by_the_ids_of_the_last_run(tmp_path: Path) -> None:
    """I-4 for C (D-73): a TAP point's check is the call that prints it; a program automake's
    harness ran whole has every check of its files; a make target's exit status, none."""
    from codecoremorph.core.models import BuildTarget, TargetKind, TestEntry
    from codecoremorph.store.index import INDEX_FILE_NAME, IndexStore

    ctx = _context(tmp_path, "make")
    write(
        ctx.worktree,
        {
            "tests/t1.c": (
                '#include "tap.h"\nint main(void)\n{\n    ok(f() == 1, "f is one");\n'
                '    ok(g() == 2, "g is %d", 2);\n    return done();\n}\n'
            ),
            "tests/t2.c": (
                '#include "check.h"\nint main(void)\n{\n    CHECK(f() == 1);\n'
                '    CHECK_STR(name(), "x");\n#if 0\n    CHECK(later());\n#endif\n'
                '    return check_done("t2");\n}\n'
            ),
        },
    )
    main = ("c:@F@main", "main", "main", "function")
    with IndexStore.open(ctx.cache_dir / INDEX_FILE_NAME) as store:
        store.write(
            files=[
                (f, "c", str(n) * 64, "test", 1, 0, "utf-8", "lf")
                for n, f in enumerate(("tests/t1.c", "tests/t2.c"))
            ],
            symbols=[
                (*main, f, 2, 1, 7, 1, None, "none", 1, "c_abi")
                for f in ("tests/t1.c", "tests/t2.c")
            ],
            references=[],
            dependencies=[],
        )
        store.set_build_targets(
            [
                BuildTarget(name="t1", kind=TargetKind.EXECUTABLE, files=["tests/t1.c"]),
                BuildTarget(name="t2", kind=TargetKind.EXECUTABLE, files=["tests/t2.c"]),
            ]
        )
        store.set_test_entries(
            [
                TestEntry(test_id="tap:tests/t1/f is one", runs="t1"),
                TestEntry(test_id="tap:tests/t1/g is 2", runs="t1"),  # formatted: no call
                TestEntry(test_id="make:tests/t2", runs="t2"),
                TestEntry(test_id="make:test", runs=None),
            ]
        )
    adapter = CAdapter()
    found = adapter.extract_assertions(ctx, ["tests/t1.c", "tests/t2.c"])
    assert [(a.test_id, a.line, a.assertions) for a in found] == [
        ("tap:tests/t1/f is one", 4, ['ok(f() == 1, "f is one")']),
        ("make:tests/t2", 2, ["CHECK(f() == 1)", 'CHECK_STR(name(), "x")', "CHECK(later())"]),
    ]
    assert adapter.detect_disabled_tests(ctx, ["tests/t2.c"]) == ["tests/t2.c:6: #if 0"]


def test_c_disable_markers_are_read_in_c_files_only(tmp_path: Path) -> None:
    """A port's Rust program skips an iterator's first item: that is no TAP skip (D-73)."""
    ctx = _context(tmp_path, "make")
    write(
        ctx.worktree,
        {
            "rust/wordfreq_rs/src/main.rs": "fn main() { let a = std::env::args().skip(1); }\n",
            "tests/t.c": 'int main(void) { skip(1, "later"); return 0; }\n',
        },
    )
    found = CAdapter().detect_disabled_tests(ctx, ["rust/wordfreq_rs/src/main.rs", "tests/t.c"])
    assert found == ["tests/t.c:1: skip(...)"]
