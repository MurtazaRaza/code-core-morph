"""GoogleTest parsing, the determinism lint and CMake registration for characterization tests
(SPEC §7.11, FR-7.3, I-4)."""

from __future__ import annotations

from pathlib import Path

from codecoremorph.adapters.base import AdapterContext
from codecoremorph.adapters.cpp.adapter import CppAdapter
from codecoremorph.adapters.cpp.determinism import nondeterminism
from codecoremorph.adapters.cpp.gtest import assertions, drop_tests, find_cases
from codecoremorph.adapters.cpp.registration import (
    BEGIN,
    characterization_cmake,
    with_block,
    without_block,
)
from codecoremorph.core.models import LanguageConfig

TESTS = """\
#include <gtest/gtest.h>
#include "counter.h"

// TEST(Commented, Out) { EXPECT_EQ(1, 2); }
TEST(Counter, StartsAtZero) {
  Counter c;
  EXPECT_EQ(0,
            c.value());  // a comment with EXPECT_TRUE(false)
}

TEST_F(CounterFixture, Adds) {
  ASSERT_TRUE(c.add(2));
  EXPECT_EQ(std::string("a(b"), c.name()) << "message " << 1;
  if (true) { EXPECT_NEAR(1.0, c.ratio(), 1e-9); }
}
"""


def test_finds_test_cases_with_their_lines() -> None:
    blocks = find_cases(TESTS)
    assert [(b.name, b.macro, b.line) for b in blocks] == [
        ("Counter.StartsAtZero", "TEST", 5),
        ("CounterFixture.Adds", "TEST_F", 11),
    ]


def test_assertions_are_normalized_and_keep_literals() -> None:
    first, second = find_cases(TESTS)
    assert assertions(TESTS, first) == ["EXPECT_EQ(0,c.value())"]
    assert assertions(TESTS, second) == [
        "ASSERT_TRUE(c.add(2))",
        'EXPECT_EQ(std::string("a(b"),c.name())',
        "EXPECT_NEAR(1.0,c.ratio(),1e-9)",
    ]


def test_layout_changes_keep_the_assertions_but_new_values_do_not() -> None:
    reformatted = TESTS.replace(
        "EXPECT_EQ(0,\n            c.value());", "EXPECT_EQ( 0, c.value() );"
    )
    changed = TESTS.replace("EXPECT_EQ(0,", "EXPECT_EQ(1,")
    first = find_cases(TESTS)[0]
    assert assertions(reformatted, find_cases(reformatted)[0]) == assertions(TESTS, first)
    assert assertions(changed, find_cases(changed)[0]) != assertions(TESTS, first)


def test_a_test_macro_without_a_body_is_not_a_case() -> None:
    assert find_cases("#define T TEST(A, B)\nTEST(A, B);\n") == []


def test_drop_tests_removes_whole_cases_and_their_line_breaks() -> None:
    dropped = drop_tests(TESTS, {"Counter.StartsAtZero"})
    assert [b.name for b in find_cases(dropped)] == ["CounterFixture.Adds"]
    assert "StartsAtZero" not in dropped
    assert dropped.startswith('#include <gtest/gtest.h>\n#include "counter.h"\n\n// TEST(Commented')
    assert drop_tests(TESTS, set()) == TESTS


def test_drop_tests_keeps_crlf_line_endings() -> None:
    crlf = TESTS.replace("\n", "\r\n")
    dropped = drop_tests(crlf, {"CounterFixture.Adds"})
    assert "\n" not in dropped.replace("\r\n", "")
    assert [b.name for b in find_cases(dropped)] == ["Counter.StartsAtZero"]


def test_determinism_lint_finds_clocks_randomness_files_network_and_threads() -> None:
    text = """\
#include <random>
#include <fstream>
#include <thread>
#include <sys/socket.h>
TEST(A, B) {
  auto now = std::chrono::steady_clock::now();
  std::srand(time(nullptr));
  std::ifstream in("data.txt");
  std::this_thread::sleep_for(std::chrono::milliseconds(5));
  FILE* f = fopen("x", "r");
  int s = socket(1, 2, 3);
}
"""
    found = nondeterminism("t.cpp", text)
    assert found == [
        "t.cpp:1: uses randomness (#include <random>)",
        "t.cpp:2: uses the filesystem (#include <fstream>)",
        "t.cpp:3: depends on thread timing (#include <thread>)",
        "t.cpp:4: uses the network (#include <sys/socket.h>)",
        "t.cpp:6: reads the clock (steady_clock)",
        "t.cpp:7: reads the clock (time()",
        "t.cpp:7: uses randomness (std::srand()",
        "t.cpp:8: uses the filesystem (ifstream)",
        "t.cpp:9: depends on thread timing (this_thread)",
        "t.cpp:10: uses the filesystem (fopen()",
        "t.cpp:11: uses the network (socket()",
    ]


def test_determinism_lint_ignores_comments_strings_members_and_project_names() -> None:
    text = """\
// std::random_device in a comment
TEST(A, B) {
  EXPECT_EQ("time(nullptr)", c.label());
  EXPECT_EQ(3, c.time());
  EXPECT_EQ(4, clock_ns::time(2));
  EXPECT_EQ(5, std::bind(f, 1)());
}
"""
    assert nondeterminism("t.cpp", text) == []


def test_generated_cmake_links_what_builds_the_sources() -> None:
    text = characterization_cmake(
        ["counter_test.cpp", "sub/strings_test.cpp"], ["src/core/counter.cpp", "src/a.cpp"]
    )
    assert '  "${CMAKE_SOURCE_DIR}/src/a.cpp"\n  "${CMAKE_SOURCE_DIR}/src/core/counter.cpp"' in text
    assert 'add_executable(codecoremorph_characterization\n  "counter_test.cpp"\n' in text
    assert "add_test(NAME codecoremorph_characterization COMMAND" in text
    assert "if(TARGET gtest_main)" in text
    assert "find_package(GTest REQUIRED)" in text


def test_root_block_is_added_once_and_removed_exactly() -> None:
    root = "cmake_minimum_required(VERSION 3.21)\nproject(P CXX)\n"
    added = with_block(root, "tests/codecoremorph_characterization")
    assert added.startswith(root + "\n" + BEGIN + "\nenable_testing()\n")
    assert 'add_subdirectory("tests/codecoremorph_characterization")' in added
    assert with_block(added, "tests/codecoremorph_characterization") == added
    assert without_block(added) == root


def test_root_block_follows_crlf_files() -> None:
    root = "project(P CXX)\r\nenable_testing()\r\n"
    added = with_block(root, "t")
    assert added.count("\r\n") == added.count("\n")
    assert without_block(added) == root


def _ctx(root: Path) -> AdapterContext:
    return AdapterContext(
        worktree=root,
        build_dir=root / "build",
        cache_dir=root / "cache",
        log_dir=root / "logs",
        executor=None,  # type: ignore[arg-type]  # these methods run no command
        lang=LanguageConfig(language="cpp", standard="cpp11"),
    )


def test_adapter_registers_and_unregisters_test_files(tmp_path: Path) -> None:
    (tmp_path / "CMakeLists.txt").write_bytes(b"project(P CXX)\r\n")
    adapter = CppAdapter()
    ctx = _ctx(tmp_path)
    tests_dir = "tests/codecoremorph_characterization"
    written = adapter.register_test_files(
        ctx, tests_dir, [f"{tests_dir}/counter_test.cpp"], ["src/counter.cpp"]
    )
    assert written == [f"{tests_dir}/CMakeLists.txt", "CMakeLists.txt"]
    listing = (tmp_path / tests_dir / "CMakeLists.txt").read_text(encoding="utf-8")
    assert '"counter_test.cpp"' in listing
    assert BEGIN.encode("utf-8") + b"\r\n" in (tmp_path / "CMakeLists.txt").read_bytes()
    adapter.register_test_files(ctx, tests_dir, [], [])
    assert not (tmp_path / tests_dir / "CMakeLists.txt").exists()
    assert (tmp_path / "CMakeLists.txt").read_bytes() == b"project(P CXX)\r\n"


def test_adapter_extracts_assertions_and_drops_tests(tmp_path: Path) -> None:
    (tmp_path / "t.cpp").write_bytes(TESTS.encode("utf-8"))
    adapter = CppAdapter()
    ctx = _ctx(tmp_path)
    found = adapter.extract_assertions(ctx, ["t.cpp", "missing.cpp"])
    assert [(a.test_id, a.line) for a in found] == [
        ("gtest:Counter.StartsAtZero", 5),
        ("gtest:CounterFixture.Adds", 11),
    ]
    assert adapter.drop_tests(ctx, "t.cpp", ["gtest:Counter.StartsAtZero"]) == 1
    selection = adapter.characterization_tests(["gtest:CounterFixture.Adds"])
    assert selection.test_ids == [
        "ctest:codecoremorph_characterization",
        "gtest:CounterFixture.Adds",
    ]
