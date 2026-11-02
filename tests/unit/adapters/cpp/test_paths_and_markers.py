"""PathMapper and the disabled-test markers (invariant I-3)."""

from __future__ import annotations

from codecoremorph.adapters.cpp.adapter import find_disabled_tests
from codecoremorph.adapters.cpp.paths import PathMapper


def test_windows_paths_fold_case_and_separators() -> None:
    mapper = PathMapper(r"E:\Work\Tree", r"E:\Work\Build", case_insensitive=True)
    assert mapper.relative(r"e:\work\tree\src\a.cpp") == "src/a.cpp"
    assert mapper.relative("E:/Work/Tree/include/x.h") == "include/x.h"
    assert mapper.relative("E:/Work/Treehouse/a.cpp") is None
    assert mapper.relative("E:/Work/Tree") is None
    assert mapper.relative("../Tree/src/b.cpp") == "src/b.cpp"  # relative to the build dir
    assert mapper.sandbox("src/a.cpp") == "E:/Work/Tree/src/a.cpp"


def test_posix_paths_are_case_sensitive() -> None:
    mapper = PathMapper("/workspace", "/build", case_insensitive=False)
    assert mapper.relative("/workspace/src/a.cpp") == "src/a.cpp"
    assert mapper.relative("/Workspace/src/a.cpp") is None
    assert mapper.relative("src/a.cpp", base="/workspace") == "src/a.cpp"
    assert mapper.relative("/usr/include/stdio.h") is None


def test_disabled_tests_are_found_outside_comments() -> None:
    text = """
TEST(Suite, DISABLED_Slow) {}
TEST_F(DISABLED_Fixture, Case) {}
// TEST(Suite, DISABLED_InAComment) {}
TEST(Suite, Skips) { GTEST_SKIP() << "later"; }
#if 0
TEST(Suite, Hidden) {}
#endif
#if 0
int notATest = 1;
#endif
const char* s = "DISABLED_ GTEST_SKIP(";
"""
    assert find_disabled_tests("t.cpp", text) == [
        "t.cpp:2: disabled test Suite.DISABLED_Slow",
        "t.cpp:3: disabled test DISABLED_Fixture.Case",
        "t.cpp:5: GTEST_SKIP",
        "t.cpp:6: #if 0 around tests",
    ]


def test_syntax_only_commands_write_nothing() -> None:
    from codecoremorph.adapters.cpp.adapter import syntax_only_command

    entry = {
        "directory": "/build",
        "arguments": ["clang++", "-Iinc", "-MD", "-MT", "x.o", "-MF", "x.d", "-o", "x.o",
                      "-c", "/w/x.cpp"],
    }  # fmt: skip
    assert syntax_only_command(entry, windows=False) == [
        "clang++", "-Iinc", "/w/x.cpp", "-fsyntax-only",
    ]  # fmt: skip
    windows = {"command": r'C:\llvm\clang++.exe -IC:\w\inc -oC:\b\x.obj -c "C:\w\a b.cpp"'}
    assert syntax_only_command(windows, windows=True) == [
        r"C:\llvm\clang++.exe", r"-IC:\w\inc", r"C:\w\a b.cpp", "-fsyntax-only",
    ]  # fmt: skip
