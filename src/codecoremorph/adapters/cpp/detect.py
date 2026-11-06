"""What ``codecoremorph init`` reads from a C++ project (FR-1.4): the build system, the current
standard, the layout and the CMake arguments a run needs.

It reads the files git tracks and runs nothing. The standard comes from the CMake files when
they set one (as the build does, D-42); otherwise it is guessed from the code: the newest
standard whose features the code uses, ignoring keywords the project defines for itself (a
C++98 header that defines its own ``nullptr``).
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from codecoremorph.adapters.cpp.cmake import (
    SUPPORTED_STANDARDS,
    cmake_standards,
    is_cmake_file,
    masked_comments,
)
from codecoremorph.adapters.cpp.indexing import HEADER_EXTENSIONS, SOURCE_EXTENSIONS
from codecoremorph.core.globs import PathMatcher
from codecoremorph.core.models import ConfigureArg, ProjectDetection
from codecoremorph.patching.textfile import BinaryFileError, TextFile
from codecoremorph.rules.detectors import mask_c_family

CODE_EXTENSIONS = (*SOURCE_EXTENSIONS, *HEADER_EXTENSIONS)
CXX_ONLY = tuple(e for e in CODE_EXTENSIONS if e not in (".c", ".h"))
SUGGESTED_TARGET = "cpp20"

# Directory names that hold someone else's code. A run never edits them (project.exclude).
_THIRD_PARTY = frozenset(
    {
        "third_party",
        "thirdparty",
        "third-party",
        "3rdparty",
        "3rd_party",
        "external",
        "externals",
        "extern",
        "vendor",
        "vendored",
        "deps",
        "_deps",
    }
)
_TEST_DIRS = frozenset(
    {"test", "tests", "testing", "unittest", "unittests", "unit_tests", "unit-tests", "gtest"}
)
# Test files beside the code (leveldb's db/db_test.cc): name suffixes, case-sensitive.
_TEST_SUFFIX = re.compile(r"(?<=.)(?:_test|_tests|_unittest|_unittests|-test|Test|Tests)$")
_PROJECT_NAME = re.compile(r"^[ \t]*project\s*\(\s*([A-Za-z0-9_.+-]+)", re.I | re.M)
_TEST_OPTION = re.compile(
    r"\boption\s*\(\s*(\w*test\w*)\s+\"[^\"]*\"\s+(?:OFF|FALSE|NO|0)\s*\)", re.I
)
_TESTING = re.compile(r"\b(?:enable_testing\s*\(|include\s*\(\s*CTest\b)", re.I)
_MINIMUM = re.compile(r"\bcmake_minimum_required\s*\(\s*VERSION\s+(\d+)\.(\d+)", re.I)
_POLICY_MINIMUM = (3, 5)  # CMake 4 refuses to configure projects that ask for older

_MAX_SCAN_FILES = 5000
_MAX_SCAN_BYTES = 1 << 20


@dataclass(frozen=True)
class _Marker:
    standard: str
    what: str
    pattern: re.Pattern[str]
    keyword: str | None = None  # a project that defines this word itself doesn't count


def _m(standard: str, what: str, pattern: str, keyword: str | None = None) -> _Marker:
    return _Marker(standard, what, re.compile(pattern), keyword)


# Features of each standard that older code can't contain. Matched on code with its comments,
# strings and character literals blanked out.
_MARKERS: tuple[_Marker, ...] = (
    _m("cpp11", "nullptr", r"\bnullptr\b", "nullptr"),
    _m("cpp11", "constexpr", r"\bconstexpr\b", "constexpr"),
    _m("cpp11", "noexcept", r"\bnoexcept\b", "noexcept"),
    _m("cpp11", "static_assert", r"\bstatic_assert\s*\(", "static_assert"),
    _m("cpp11", "decltype", r"\bdecltype\s*\(", "decltype"),
    _m("cpp11", "override", r"\)\s*(?:const\s*)?(?:noexcept\s*)?override\b", "override"),
    _m("cpp11", "enum class", r"\benum\s+(?:class|struct)\s+[A-Za-z_]"),
    _m("cpp11", "alias declarations", r"\busing\s+[A-Za-z_]\w*\s*=(?!=)"),
    _m("cpp11", "= default / = delete", r"\)\s*(?:const\s*)?=\s*(?:default|delete)\s*;"),
    _m(
        "cpp11",
        "auto type deduction",
        r"\bauto\s*[&*]{0,2}\s*(?!(?:int|long|short|char|unsigned|signed|float|double|bool"
        r"|const|volatile)\b)[A-Za-z_]\w*\s*(?:=|\{|:(?!:))",
    ),
    _m(
        "cpp11",
        "the C++11 library",
        r"\bstd::(?:unique_ptr|shared_ptr|weak_ptr|make_shared|move|forward|thread|mutex"
        r"|lock_guard|unique_lock|condition_variable|atomic|function|array|tuple"
        r"|unordered_(?:map|set|multimap|multiset)|chrono|to_string|stoi|stol|stoul|stod"
        r"|nullptr_t|initializer_list|enable_if|is_same|declval|hash)\b",
    ),
    _m(
        "cpp11",
        "a C++11 header",
        r"#\s*include\s*<(?:thread|mutex|atomic|chrono|unordered_map|unordered_set|array|tuple"
        r"|type_traits|random|regex|condition_variable|future|initializer_list|cstdint"
        r"|system_error|forward_list|cinttypes)>",
    ),
    _m("cpp14", "std::make_unique", r"\bstd::make_unique\b"),
    _m(
        "cpp14",
        "C++14 type-trait aliases",
        r"\bstd::(?:enable_if|conditional|decay|remove_\w+|add_\w+|common_type|underlying_type"
        r"|result_of|make_signed|make_unsigned|aligned_storage)_t\b",
    ),
    _m("cpp14", "decltype(auto)", r"\bdecltype\s*\(\s*auto\s*\)"),
    _m("cpp14", "[[deprecated]]", r"\[\[\s*deprecated\b"),
    _m("cpp14", "binary literals", r"(?<![\w.])0[bB][01]+\b"),
    _m("cpp14", "generic lambdas", r"\]\s*\(\s*(?:const\s+)?auto\s*[&*]{0,2}\s*[A-Za-z_]"),
    _m(
        "cpp14",
        "the C++14 library",
        r"\bstd::(?:exchange|integer_sequence|index_sequence|make_index_sequence|quoted"
        r"|shared_timed_mutex)\b",
    ),
    _m("cpp17", "if constexpr", r"\bif\s+constexpr\b"),
    _m("cpp17", "structured bindings", r"\bauto\s*&{0,2}\s*\[\s*[A-Za-z_]\w*\s*,"),
    _m("cpp17", "nested namespace definitions", r"\bnamespace\s+[A-Za-z_]\w*::[A-Za-z_]"),
    _m("cpp17", "C++17 attributes", r"\[\[\s*(?:nodiscard|maybe_unused|fallthrough)\b"),
    _m("cpp17", "template <auto>", r"\btemplate\s*<\s*auto\b"),
    _m("cpp17", "C++17 type-trait variables", r"\bstd::\w+_v\b"),
    _m(
        "cpp17",
        "the C++17 library",
        r"\bstd::(?:optional|nullopt|variant|visit|string_view|any|byte|filesystem|invoke"
        r"|apply|clamp|gcd|lcm|scoped_lock|shared_mutex|in_place|as_const|void_t"
        r"|conjunction|disjunction|negation|from_chars|to_chars|launder)\b",
    ),
    _m(
        "cpp17",
        "a C++17 header",
        r"#\s*include\s*<(?:optional|variant|string_view|any|filesystem|charconv|execution"
        r"|memory_resource)>",
    ),
    _m("cpp20", "concepts", r"\bconcept\s+[A-Za-z_]\w*\s*=", "concept"),
    _m("cpp20", "requires-clauses", r"(?<![\w.>])requires\s+[A-Za-z_][\w:]*\s*<"),
    _m("cpp20", "coroutines", r"\bco_(?:await|yield|return)\b"),
    _m("cpp20", "operator<=>", r"<=>"),
    _m("cpp20", "consteval / constinit", r"\bconst(?:eval|init)\b"),
    _m("cpp20", "C++20 attributes", r"\[\[\s*(?:likely|unlikely|no_unique_address)\b"),
    _m(
        "cpp20",
        "the C++20 library",
        r"\bstd::(?:span|ranges|views|format|jthread|stop_token|source_location|bit_cast"
        r"|to_array|counting_semaphore|binary_semaphore|latch|barrier|erase_if|ssize|midpoint"
        r"|lerp|endian|remove_cvref|type_identity|is_constant_evaluated)\b",
    ),
    _m(
        "cpp20",
        "a C++20 header",
        r"#\s*include\s*<(?:concepts|ranges|format|span|compare|coroutine|bit|numbers"
        r"|source_location|version|semaphore|latch|barrier|stop_token|syncstream)>",
    ),
    _m(
        "cpp23",
        "the C++23 library",
        r"\bstd::(?:expected|print|println|mdspan|generator|stacktrace|unreachable"
        r"|to_underlying|byteswap|move_only_function|flat_map|flat_set|out_ptr|inout_ptr"
        r"|spanstream)\b",
    ),
    _m(
        "cpp23",
        "a C++23 header",
        r"#\s*include\s*<(?:expected|print|generator|stacktrace|mdspan|flat_map|flat_set"
        r"|spanstream)>",
    ),
    _m("cpp23", "if consteval", r"\bif\s*!?\s*consteval\b"),
    _m("cpp23", "[[assume]]", r"\[\[\s*assume\b"),
    _m("cpp23", "explicit object parameters", r"\(\s*this\s+(?:const\s+)?(?:auto|[A-Z]\w*)\b"),
)


def _rank(standard: str) -> int:
    return SUPPORTED_STANDARDS.index(standard)


def _defines(code: str, word: str) -> bool:
    """Whether ``code`` defines ``word`` itself: a macro, a typedef, a class or an object."""
    w = re.escape(word)
    pattern = (
        rf"#\s*(?:define|ifndef|ifdef|undef)\s+{w}\b"
        rf"|\btypedef\b[^;{{}}]*\b{w}\s*;"
        rf"|\}}\s*{w}\s*[=;]"
        rf"|\b(?:class|struct|enum|union)\s+{w}\b"
    )
    return re.search(pattern, code) is not None


@dataclass(frozen=True)
class _Evidence:
    standard: str
    what: str
    where: str


def guess_standard(files: Iterable[tuple[str, str]]) -> tuple[str, list[_Evidence]]:
    """The oldest standard that has every feature the code uses (``cpp98`` when it uses no
    newer one), and the uses that set it. ``files`` holds (path, text) pairs."""
    masked = [(path, mask_c_family(text)) for path, text in files]
    defined = {
        marker.keyword
        for marker in _MARKERS
        if marker.keyword is not None and any(_defines(code, marker.keyword) for _, code in masked)
    }
    found: dict[str, _Evidence] = {}
    for marker in _MARKERS:
        if marker.keyword in defined:
            continue
        for path, code in masked:
            match = marker.pattern.search(code)
            if match is not None:
                line = code.count("\n", 0, match.start()) + 1
                found[marker.what] = _Evidence(marker.standard, marker.what, f"{path}:{line}")
                break
    if not found:
        return "cpp98", []
    newest = max((e.standard for e in found.values()), key=_rank)
    return newest, sorted(
        (e for e in found.values() if e.standard == newest), key=lambda e: e.where
    )


def _read(root: Path, path: str) -> str | None:
    try:
        data = (root / path).read_bytes()[:_MAX_SCAN_BYTES]
        return TextFile.decode(data).text
    except (OSError, BinaryFileError):
        return None


def find_vendored_dirs(files: Sequence[str]) -> list[str]:
    dirs: set[str] = set()
    for path in files:
        parts = path.split("/")[:-1]
        for depth, part in enumerate(parts, start=1):
            if part.lower() in _THIRD_PARTY:
                dirs.add("/".join(parts[:depth]))
                break
    return sorted(d for d in dirs if not any(d.startswith(f"{o}/") for o in dirs))


def test_layout(code: Sequence[str]) -> tuple[list[str], list[str]]:
    """Test directories, and name patterns for test files that live beside the code."""
    dirs: set[str] = set()
    for path in code:
        parts = path.split("/")[:-1]
        for depth, part in enumerate(parts, start=1):
            if part.lower() in _TEST_DIRS:
                dirs.add("/".join(parts[:depth]))
                break
    in_dirs = PathMatcher(dirs)
    patterns: set[str] = set()
    for path in code:
        stem, dot, extension = posixpath.basename(path).rpartition(".")
        if not dot or in_dirs.matches(path):
            continue
        if stem.startswith("test_"):
            patterns.add(f"test_*.{extension}")
        elif (suffix := _TEST_SUFFIX.search(stem)) is not None:
            patterns.add(f"*{suffix.group(0)}.{extension}")
    return sorted(dirs), sorted(patterns)


def _cmake_texts(root: Path, files: Sequence[str]) -> list[tuple[str, str]]:
    texts: list[tuple[str, str]] = []
    for path in files:
        if is_cmake_file(path):
            text = _read(root, path)
            if text is not None:
                texts.append((path, text))
    return texts


def _configure_args(own: list[tuple[str, str]], every: list[tuple[str, str]]) -> list[ConfigureArg]:
    args: list[ConfigureArg] = []
    for path, text in own:
        for match in _TEST_OPTION.finditer(masked_comments(text)):
            args.append(
                ConfigureArg(
                    arg=f"-D{match.group(1)}=ON",
                    reason=f"{path} builds the tests only when {match.group(1)} is ON",
                )
            )
    old = [
        (path, f"{m.group(1)}.{m.group(2)}")
        for path, text in every
        if (m := _MINIMUM.search(masked_comments(text))) is not None
        and (int(m.group(1)), int(m.group(2))) < _POLICY_MINIMUM
    ]
    if old:
        path, version = old[0]
        more = f" (and {len(old) - 1} more)" if len(old) > 1 else ""
        args.append(
            ConfigureArg(
                arg="-DCMAKE_POLICY_VERSION_MINIMUM=3.5",
                reason=f"CMake 4 refuses files that ask for CMake older than 3.5: "
                f"{path} asks for {version}{more}",
            )
        )
    return args


def _cmake_standard(own: list[tuple[str, str]]) -> tuple[str, str] | None:
    """The oldest standard the project's CMake files set, and the file that sets it."""
    found: list[tuple[str, str]] = []
    for path, text in own:
        for number in cmake_standards(text):
            standard = f"cpp{number}"
            if standard in SUPPORTED_STANDARDS:
                found.append((standard, path))
    if not found:
        return None
    return min(found, key=lambda item: (_rank(item[0]), item[1]))


def _suggested_target(source: str) -> str:
    if _rank(source) < _rank(SUGGESTED_TARGET):
        return SUGGESTED_TARGET
    return SUPPORTED_STANDARDS[-1]


def detect(root: Path, files: Sequence[str]) -> ProjectDetection | None:
    """A C++ project's starter settings, or None when it has no C++ code of its own."""
    vendored_dirs = find_vendored_dirs(files)
    vendored = PathMatcher(vendored_dirs)
    code = sorted(
        f for f in files if f.lower().endswith(CODE_EXTENSIONS) and not vendored.matches(f)
    )
    if not any(f.lower().endswith(CXX_ONLY) for f in code):
        return None  # no C++: a C project (.c and .h), or only vendored code
    cmake = _cmake_texts(root, files)
    own_cmake = [(p, t) for p, t in cmake if not vendored.matches(p)]
    notes: list[str] = []

    build_system: str | None = "cmake"
    if "CMakeLists.txt" not in files:
        build_system = None
        notes.append(
            "There is no CMakeLists.txt at the repository root; CodeCoreMorph builds C++ "
            "projects with CMake only."
        )

    set_by_cmake = _cmake_standard(own_cmake)
    if set_by_cmake is not None:
        standard, where = set_by_cmake
        note = f"set in {where}"
        guessed = False
    else:
        texts = [
            (path, text)
            for path in code[:_MAX_SCAN_FILES]
            if (text := _read(root, path)) is not None
        ]
        standard, evidence = guess_standard(texts)
        guessed = True
        if evidence:
            uses = ", ".join(f"{e.what} ({e.where})" for e in evidence[:3])
            note = f"guessed: the build files set no standard, and the code uses {uses}"
        else:
            note = "guessed: the build files set no standard, and the code uses nothing newer"
        notes.append(
            f"migration.source.standard is a guess ({standard}): check it against the "
            "compiler flags the project is built with."
        )

    test_dirs, test_patterns = test_layout(code)
    tests = PathMatcher([*test_dirs, *test_patterns])
    sources = [f for f in code if not tests.matches(f)]
    if any("/" not in f for f in sources):
        source_dirs = ["."]
    else:
        source_dirs = sorted({f.split("/", 1)[0] for f in sources})
    public_api = (
        ["include/**"]
        if any(f.startswith("include/") and f.lower().endswith(HEADER_EXTENSIONS) for f in code)
        else []
    )
    if not test_dirs and not test_patterns:
        notes.append(
            "No tests were found. A run needs passing tests, or characterization tests "
            "(parity.characterization_tests) for the code no test reaches."
        )
    elif build_system is not None and not any(
        _TESTING.search(masked_comments(t)) for _, t in own_cmake
    ):
        notes.append(
            "No CMake file calls enable_testing() or include(CTest); CodeCoreMorph runs the "
            "tests through CTest, so it may find none."
        )

    name = None
    top = next((t for p, t in own_cmake if p == "CMakeLists.txt"), None)
    if top is not None and (match := _PROJECT_NAME.search(masked_comments(top))) is not None:
        name = match.group(1)

    return ProjectDetection(
        language="cpp",
        build_system=build_system,
        name=name,
        standard=standard,
        standard_note=note,
        standard_guessed=guessed,
        target_standard=_suggested_target(standard),
        source_dirs=source_dirs,
        test_dirs=[*test_dirs, *test_patterns],
        public_api=public_api,
        exclude=[f"{d}/**" for d in vendored_dirs],
        configure_args=_configure_args(own_cmake, cmake),
        notes=notes,
        code_files=len(code),
    )
