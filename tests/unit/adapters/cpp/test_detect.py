"""What `codecoremorph init` reads from a C++ project (FR-1.4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from codecoremorph.adapters.cpp.adapter import CppAdapter
from codecoremorph.adapters.cpp.detect import detect, guess_standard
from codecoremorph.core.models import ProjectDetection

CMAKE = "cmake_minimum_required(VERSION 3.21)\nproject(Demo CXX)\nenable_testing()\n"


def write(root: Path, files: dict[str, str]) -> list[str]:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return sorted(files)


def detected(root: Path, files: dict[str, str]) -> ProjectDetection | None:
    return detect(root, write(root, files))


# --------------------------------------------------------------------------- the standard


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("int f() { return 0; }\n", "cpp98"),
        ("int* p = nullptr;\n", "cpp11"),
        ("std::unique_ptr<int> p;\n", "cpp11"),
        ("for (auto& x : items) {}\n", "cpp11"),
        ("using Index = int;\n", "cpp11"),
        ("auto p = std::make_unique<int>(1);\n", "cpp14"),
        ("auto [a, b] = pair;\n", "cpp17"),
        ("if constexpr (N > 0) {}\n", "cpp17"),
        ("bool same = std::is_same_v<int, long>;\n", "cpp17"),
        ("#include <optional>\n", "cpp17"),
        ("template <class T> concept Small = sizeof(T) < 8;\n", "cpp20"),
        ("auto c = a <=> b;\n", "cpp20"),
        ("std::expected<int, Error> parse();\n", "cpp23"),
    ],
)
def test_guess_takes_the_newest_feature_used(code: str, expected: str) -> None:
    assert guess_standard([("src/a.cpp", code)])[0] == expected


@pytest.mark.parametrize(
    "code",
    [
        "// std::optional<int> would be nicer\nint f();\n",  # comments don't count
        'const char* s = "nullptr and std::make_unique";\n',  # nor strings
        "auto int written = 0;\n",  # C++98's auto storage class
        "std::size_t n = std::vector<int>().size();\n",  # std::size_t isn't std::size
        "bool requires(int level) const;\n",  # a member named requires (C++17 code)
        "for (std::vector<int>::iterator it = v.begin(); it != v.end(); ++it) {}\n",
        "std::bind1st(std::less<int>(), 1);\n",
    ],
)
def test_guess_ignores_what_older_code_may_contain(code: str) -> None:
    assert guess_standard([("src/a.cpp", code)])[0] == "cpp98"


def test_guess_ignores_keywords_the_project_defines() -> None:
    # The cpp98-legacy fixture's own nullptr, defined in one header and used elsewhere.
    compat = (
        "#ifndef nullptr\nconst class nullptr_t_ {\npublic:\n"
        "  template <class T> operator T*() const { return 0; }\n} nullptr = {};\n#endif\n"
    )
    standard, evidence = guess_standard(
        [("include/compat.h", compat), ("src/a.cpp", "if (p != nullptr) {}\n")]
    )
    assert (standard, evidence) == ("cpp98", [])


def test_guess_says_where_the_feature_is() -> None:
    standard, evidence = guess_standard(
        [("src/a.cpp", "int a;\n\nauto p = std::make_unique<int>(1);\n")]
    )
    assert standard == "cpp14"
    assert [(e.what, e.where) for e in evidence] == [("std::make_unique", "src/a.cpp:3")]


def test_the_cmake_files_win_over_the_code(tmp_path: Path) -> None:
    d = detected(
        tmp_path,
        {
            "CMakeLists.txt": CMAKE + "set(CMAKE_CXX_STANDARD 14)\nadd_subdirectory(tests)\n",
            "tests/CMakeLists.txt": "target_compile_features(t PRIVATE cxx_std_11)\n",
            "src/a.cpp": "auto [a, b] = f();\n",
        },
    )
    assert d is not None
    assert (d.standard, d.standard_guessed) == ("cpp11", False)
    assert d.standard_note == "set in tests/CMakeLists.txt"
    assert d.target_standard == "cpp20"


def test_a_guessed_standard_is_flagged(tmp_path: Path) -> None:
    d = detected(tmp_path, {"CMakeLists.txt": CMAKE, "src/a.cpp": "int* p = nullptr;\n"})
    assert d is not None
    assert (d.standard, d.standard_guessed) == ("cpp11", True)
    assert d.standard_note == (
        "guessed: the build files set no standard, and the code uses nullptr (src/a.cpp:1)"
    )
    assert any("is a guess (cpp11)" in note for note in d.notes)


def test_vendored_code_is_neither_guessed_from_nor_migrated(tmp_path: Path) -> None:
    d = detected(
        tmp_path,
        {
            "CMakeLists.txt": CMAKE,
            "src/a.cpp": "int f();\n",
            "third_party/json/json.hpp": "#include <optional>\n",
            "src/vendor/lib.h": "auto [a, b] = g();\n",
        },
    )
    assert d is not None
    assert d.standard == "cpp98"
    assert d.exclude == ["src/vendor/**", "third_party/**"]
    assert d.source_dirs == ["src"]
    assert d.code_files == 1


@pytest.mark.parametrize(("source", "target"), [("cpp11", "cpp20"), ("cpp20", "cpp23")])
def test_suggested_target(tmp_path: Path, source: str, target: str) -> None:
    number = source.removeprefix("cpp")
    d = detected(
        tmp_path,
        {"CMakeLists.txt": CMAKE + f"set(CMAKE_CXX_STANDARD {number})\n", "a.cpp": ""},
    )
    assert d is not None
    assert d.target_standard == target


# --------------------------------------------------------------------------- the layout


def test_a_conventional_layout(tmp_path: Path) -> None:
    d = detected(
        tmp_path,
        {
            "CMakeLists.txt": CMAKE,
            "include/demo/a.h": "",
            "src/a.cpp": "",
            "app/main.cpp": "",
            "tests/CMakeLists.txt": "",
            "tests/a_test.cpp": "",
            "docs/notes.md": "",
        },
    )
    assert d is not None
    assert d.name == "Demo"
    assert d.build_system == "cmake"
    assert d.source_dirs == ["app", "include", "src"]
    assert d.test_dirs == ["tests"]
    assert d.public_api == ["include/**"]
    assert d.exclude == []
    assert d.notes == ["migration.source.standard is a guess (cpp98): check it against the "
                       "compiler flags the project is built with."]  # fmt: skip


def test_tests_beside_the_code_become_patterns(tmp_path: Path) -> None:
    d = detected(
        tmp_path,
        {
            "CMakeLists.txt": CMAKE,
            "db/db.cc": "",
            "db/db_test.cc": "",
            "util/hash_unittest.cc": "",
            "util/test_env.cpp": "",
            "util/ArenaTest.cpp": "",
            "issues/issue178_test.cc": "",
            "util/latest.cc": "",  # not a test: the suffix must be "_test", "Test", ...
        },
    )
    assert d is not None
    assert d.test_dirs == ["*Test.cpp", "*_test.cc", "*_unittest.cc", "test_*.cpp"]
    assert d.source_dirs == ["db", "util"]


def test_sources_at_the_root(tmp_path: Path) -> None:
    d = detected(tmp_path, {"CMakeLists.txt": CMAKE, "main.cpp": "", "lib/a.cpp": ""})
    assert d is not None
    assert d.source_dirs == ["."]


def test_no_tests_is_a_note(tmp_path: Path) -> None:
    d = detected(tmp_path, {"CMakeLists.txt": CMAKE, "src/a.cpp": ""})
    assert d is not None
    assert d.test_dirs == []
    assert any(note.startswith("No tests were found.") for note in d.notes)


def test_tests_without_ctest_are_a_note(tmp_path: Path) -> None:
    d = detected(
        tmp_path,
        {"CMakeLists.txt": "project(Demo CXX)\n", "src/a.cpp": "", "tests/a_test.cpp": ""},
    )
    assert d is not None
    assert any("enable_testing()" in note for note in d.notes)


def test_no_cmake_means_no_build_system(tmp_path: Path) -> None:
    d = detected(tmp_path, {"Makefile": "all:\n", "src/a.cpp": ""})
    assert d is not None
    assert d.build_system is None
    assert any("no CMakeLists.txt" in note for note in d.notes)


@pytest.mark.parametrize(
    "files",
    [
        {"README.md": "", "setup.py": ""},  # no C or C++
        {"CMakeLists.txt": "project(C C)\n", "src/a.c": "", "src/a.h": ""},  # a C project
        {"CMakeLists.txt": CMAKE, "third_party/lib/a.cpp": ""},  # only vendored code
    ],
)
def test_not_a_cpp_project(tmp_path: Path, files: dict[str, str]) -> None:
    assert detected(tmp_path, files) is None


# --------------------------------------------------------------------------- configure


def test_test_options_that_default_off_are_turned_on(tmp_path: Path) -> None:
    d = detected(
        tmp_path,
        {
            "CMakeLists.txt": CMAKE + 'option(DEMO_BUILD_TESTS "Build the tests" OFF)\n'
            'option(DEMO_BUILD_DOCS "Build the docs" OFF)\n'
            '# option(DEMO_OLD_TESTS "commented out" OFF)\n',
            "src/a.cpp": "",
        },
    )
    assert d is not None
    assert [a.arg for a in d.configure_args] == ["-DDEMO_BUILD_TESTS=ON"]
    assert d.configure_args[0].reason == (
        "CMakeLists.txt builds the tests only when DEMO_BUILD_TESTS is ON"
    )


def test_old_cmake_minimums_get_the_policy_flag(tmp_path: Path) -> None:
    # leveldb's vendored GoogleTest asks for CMake 2.8.8, which CMake 4 refuses.
    d = detected(
        tmp_path,
        {
            "CMakeLists.txt": CMAKE,
            "src/a.cpp": "",
            "third_party/googletest/CMakeLists.txt": "cmake_minimum_required(VERSION 2.8.8)\n",
            "third_party/googletest/src/gtest.cc": "",
        },
    )
    assert d is not None
    assert [a.arg for a in d.configure_args] == ["-DCMAKE_POLICY_VERSION_MINIMUM=3.5"]
    assert "third_party/googletest/CMakeLists.txt asks for 2.8" in d.configure_args[0].reason


def test_the_adapter_method(tmp_path: Path) -> None:
    files = write(tmp_path, {"CMakeLists.txt": CMAKE, "src/a.cpp": ""})
    d = CppAdapter().detect_project(tmp_path, files)
    assert d is not None
    assert d.language == "cpp"
