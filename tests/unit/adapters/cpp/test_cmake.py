"""CMake command lines and the standard-bump edit of build files."""

from __future__ import annotations

import pytest

from codecoremorph.adapters.cpp.cmake import (
    SUPPORTED_STANDARDS,
    build_command,
    bump_cmake_standard,
    cmake_standard,
    cmake_standards,
    configure_command,
    cxx_standard_number,
    is_cmake_file,
    lowest_standard,
    masked_comments,
    std_flag,
)
from codecoremorph.core.errors import ConfigError


@pytest.mark.parametrize(
    ("value", "expected"),
    [("cpp98", "98"), ("cpp03", "03"), ("cpp11", "11"), ("cpp20", "20"), ("c++17", "17"),
     ("23", "23")],
)  # fmt: skip
def test_standard_numbers(value: str, expected: str) -> None:
    assert cxx_standard_number(value) == expected


@pytest.mark.parametrize("value", ["cpp12", "java21", "", "cpp2a", "cpp26"])
def test_bad_standards(value: str) -> None:
    with pytest.raises(ConfigError):
        cxx_standard_number(value)


def test_command_lines() -> None:
    argv = configure_command("/workspace", "/build", standard="11", extra_args=["-DX=1"])
    assert argv[:7] == ["cmake", "-S", "/workspace", "-B", "/build", "-G", "Ninja"]
    assert "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON" in argv
    assert "-DCMAKE_CXX_STANDARD=11" in argv
    assert argv[-1] == "-DX=1"
    assert "-DCMAKE_CXX_STANDARD=11" not in configure_command(
        "/w", "/b", standard=None, extra_args=[]
    )
    assert build_command("/build", ["a", "b"]) == [
        "cmake", "--build", "/build", "--target", "a", "--target", "b", "--", "-k", "0",
    ]  # fmt: skip
    assert is_cmake_file("CMakeLists.txt")
    assert is_cmake_file("cmake/flags.cmake")
    assert not is_cmake_file("src/main.cpp")


def test_bump_rewrites_every_kind_of_setting() -> None:
    text = (
        "cmake_minimum_required(VERSION 3.21)\n"
        "project(X CXX)\n"
        "set(CMAKE_CXX_STANDARD 11)\n"
        "set_target_properties(a PROPERTIES CXX_STANDARD 14)\n"
        "target_compile_features(b PUBLIC cxx_std_11)\n"
        'target_compile_options(c PRIVATE -std=c++0x "-std=gnu++11")\n'
    )
    edit = bump_cmake_standard(text, "20", top_level=True)
    assert edit.replacements == 5
    assert edit.text == (
        "cmake_minimum_required(VERSION 3.21)\n"
        "project(X CXX)\n"
        "set(CMAKE_CXX_STANDARD 20)\n"
        "set_target_properties(a PROPERTIES CXX_STANDARD 20)\n"
        "target_compile_features(b PUBLIC cxx_std_20)\n"
        'target_compile_options(c PRIVATE -std=c++20 "-std=gnu++20")\n'
    )


def test_bump_raises_cpp98_and_cpp03_settings() -> None:
    text = (
        "set(CMAKE_CXX_STANDARD 98)\n"
        "target_compile_features(b PUBLIC cxx_std_98)\n"
        "add_compile_options(-std=c++03 -std=gnu++98)\n"
    )
    edit = bump_cmake_standard(text, "11", top_level=False)
    assert edit.replacements == 4
    assert edit.text == (
        "set(CMAKE_CXX_STANDARD 11)\n"
        "target_compile_features(b PUBLIC cxx_std_11)\n"
        "add_compile_options(-std=c++11 -std=gnu++11)\n"
    )
    assert bump_cmake_standard(edit.text, "23", top_level=False).text.count("23") == 4


def test_bump_never_lowers_a_standard() -> None:
    text = "set(CMAKE_CXX_STANDARD 23)\ntarget_compile_features(x PUBLIC cxx_std_20)\n"
    edit = bump_cmake_standard(text, "20", top_level=True)
    assert (edit.text, edit.replacements) == (text, 0)


def test_bump_adds_a_standard_after_project_when_none_is_set() -> None:
    text = (
        "cmake_minimum_required(VERSION 3.21)\r\nproject(X\r\n  LANGUAGES CXX)\r\n"
        "add_library(x x.cpp)\r\n"
    )
    edit = bump_cmake_standard(text, "20", top_level=True)
    assert edit.replacements == 1
    assert edit.text == (
        "cmake_minimum_required(VERSION 3.21)\r\nproject(X\r\n  LANGUAGES CXX)\r\n\r\n"
        "set(CMAKE_CXX_STANDARD 20)\r\nset(CMAKE_CXX_STANDARD_REQUIRED ON)\r\n"
        "add_library(x x.cpp)\r\n"
    )
    nested = bump_cmake_standard(text, "20", top_level=False)
    assert nested.replacements == 0


def test_supported_standards_are_ordered_and_parse() -> None:
    assert SUPPORTED_STANDARDS[0] == "cpp98"
    assert SUPPORTED_STANDARDS[-1] == "cpp23"
    assert [cxx_standard_number(s) for s in SUPPORTED_STANDARDS] == [
        "98", "03", "11", "14", "17", "20", "23",
    ]  # fmt: skip


def test_cpp03_builds_as_cmake_98() -> None:
    # CMake has no CMAKE_CXX_STANDARD 03; clang treats c++03 as c++98.
    assert cmake_standard("03") == "98"
    assert cmake_standard("20") == "20"
    argv = configure_command("/w", "/b", standard="03", extra_args=[])
    assert "-DCMAKE_CXX_STANDARD=98" in argv
    assert std_flag("17", gnu=True) == "-std=gnu++17"
    assert std_flag("11", gnu=False) == "-std=c++11"


def test_initial_cache_comes_before_the_source_dir() -> None:
    argv = configure_command("/w", "/b", standard="98", extra_args=[], initial_cache="/c/x.cmake")
    assert argv[:5] == ["cmake", "-C", "/c/x.cmake", "-S", "/w"]


# leveldb's CMakeLists.txt, abridged: a comment describes a past compiler flag.
LEVELDB = (
    "project(leveldb VERSION 1.23.0 LANGUAGES C CXX)\n"
    "if(NOT CMAKE_CXX_STANDARD)\n"
    "  # This project requires C++11.\n"
    "  set(CMAKE_CXX_STANDARD 11)\n"
    "endif(NOT CMAKE_CXX_STANDARD)\n"
    "# versions of do not expose fdatasync() in <unistd.h> in standard C mode\n"
    "# (-std=c11), but do expose the function in standard C++ mode (-std=c++11).\n"
    'set(FLAGS "-std=c++11 # not a comment")  #[[ -std=c++11 in a bracket comment ]]\n'
    "#[==[ set(CMAKE_CXX_STANDARD 98) ]==]\n"
)


def test_the_bump_leaves_comments_alone() -> None:
    """Regression: the bump rewrote leveldb's comment about `-std=c++11` to say c++20."""
    edit = bump_cmake_standard(LEVELDB, "20", top_level=True)
    assert edit.replacements == 2  # the set() and the flag inside a quoted argument
    assert "(-std=c++11)." in edit.text
    assert "set(CMAKE_CXX_STANDARD 20)" in edit.text
    assert '"-std=c++20 # not a comment"' in edit.text
    assert "#[[ -std=c++11 in a bracket comment ]]" in edit.text
    assert "#[==[ set(CMAKE_CXX_STANDARD 98) ]==]" in edit.text


def test_a_commented_out_standard_doesnt_count_as_set() -> None:
    text = "project(X CXX)\n# set(CMAKE_CXX_STANDARD 11)\n"
    edit = bump_cmake_standard(text, "17", top_level=True)
    assert edit.replacements == 1
    assert edit.text.count("set(CMAKE_CXX_STANDARD 17)") == 1
    assert "# set(CMAKE_CXX_STANDARD 11)" in edit.text


def test_masking_keeps_offsets_and_line_breaks() -> None:
    text = 'a # one\r\nb "x # y" [=[ # z ]=] #[[\nc ]] d\n'
    masked = masked_comments(text)
    assert len(masked) == len(text)
    assert masked.splitlines() == ["a      ", 'b "x # y" [=[ # z ]=]    ', "     d"]


def test_the_standard_the_build_files_set() -> None:
    assert cmake_standards(LEVELDB) == ["11", "11"]
    assert lowest_standard([LEVELDB]) == "cpp11"
    bumped = bump_cmake_standard(LEVELDB, "20", top_level=True).text
    assert lowest_standard([bumped]) == "cpp20"
    other = "target_compile_features(t PUBLIC cxx_std_17)\nadd_compile_options(-std=gnu++1z)\n"
    assert cmake_standards(other) == ["17", "17"]
    assert lowest_standard([bumped, other]) == "cpp17"
    assert lowest_standard(["project(X)\n# -std=c++11\n"]) is None
    assert lowest_standard(["set(CMAKE_CXX_STANDARD 98)\n"]) == "cpp98"
