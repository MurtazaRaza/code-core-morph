"""The C++ side of a C# → C++ port (SPEC §11.9, D-78): what the C++ adapter, as the port's
target, writes into the project, and the C# adapter, as its source, reads back.

The C++ goes under ``cpp/``: a static library ``<name>_cpp`` of the ported types (``cpp/src``),
which the retirement keeps, and during the port a shared library ``<name>_native`` of
``extern "C"`` exports over it (``cpp/bridge``, two files per unit), which the C# façades call
through P/Invoke. A top-level ``CMakeLists.txt`` builds both, with AddressSanitizer and
UndefinedBehaviorSanitizer when ``CODECOREMORPH_SANITIZE`` is on (CodeCoreMorph's builds turn it
on). The runtime CodeCoreMorph ships (``csharp_runtime``) is copied in by the first unit's
scaffold: ``codecoremorph_decimal.hpp`` and ``codecoremorph_exceptions.hpp`` under
``cpp/include``, the bridge's own files under ``cpp/bridge``, and the C# support beside the
façade project's file. The C# declarations of the exports are generated from the bridge headers
at configure (:mod:`codecoremorph.adapters.cpp.pinvoke`).

A program's code is a function of the library, which the C# program calls through the bridge
until the retirement, and a CMake program calls (``cpp/programs``). The translated tests are
GoogleTest programs under ``cpp/tests``, one per C# test file, each a CTest test. The retirement
deletes the C#, its build, the façades' support and the bridge; with ``keep_source_api`` only the
programs and the tests that need them, the library staying a .NET library over the C++.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from importlib import resources
from pathlib import Path, PurePosixPath

from codecoremorph.adapters.cpp.pinvoke import GENERATED_FILE, NAMESPACE
from codecoremorph.core.errors import ConfigError
from codecoremorph.rules.detectors import mask_c_family

CPP_DIR = "cpp"
TOP_CMAKE = "CMakeLists.txt"
CPP_CMAKE = "cpp/CMakeLists.txt"
BRIDGE_DIR = "cpp/bridge"
INCLUDE_DIR = "cpp/include"
SOURCE_DIR = "cpp/src"
PROGRAMS_DIR = "cpp/programs"
TESTS_DIR = "cpp/tests"
TESTS_CMAKE = "cpp/tests/CMakeLists.txt"
SYMBOL_MAP = "cpp/symbol-map.toml"
TEST_MAP = "cpp/test-map.toml"
SANITIZE = "CODECOREMORPH_SANITIZE"
SANITIZER_FLAGS = "-fsanitize=address,undefined"
SUPPORT_FILE = "CodeCoreMorph.Interop.cs"
EXCEPTIONS_FILE = "CcmExceptions.cs"
TOP_MARK = "# Written by CodeCoreMorph for a C# → C++ port (D-78)"
SOURCES_BEGIN = "  # >>> codecoremorph: ported types >>>"
SOURCES_END = "  # <<< codecoremorph: ported types <<<"
BRIDGE_BEGIN = "# >>> codecoremorph: bridge >>>"
BRIDGE_END = "# <<< codecoremorph: bridge <<<"
BRIDGE_SOURCES_BEGIN = "  # >>> codecoremorph: bridge sources >>>"
BRIDGE_SOURCES_END = "  # <<< codecoremorph: bridge sources <<<"
PROGRAMS_BEGIN = "# >>> codecoremorph: programs >>>"
PROGRAMS_END = "# <<< codecoremorph: programs <<<"
TESTS_BEGIN = "# >>> codecoremorph: translated tests >>>"
TESTS_END = "# <<< codecoremorph: translated tests <<<"
SCAFFOLDS = frozenset(
    {"cpp-csharp-type", "cpp-csharp-program", "cpp-csharp-tests", "cpp-csharp-retire"}
)
# What MSBuild and NuGet read beside the projects, which the retirement deletes with them.
MSBUILD_FILES = frozenset(
    {
        "directory.build.props",
        "directory.build.targets",
        "directory.packages.props",
        "directory.build.rsp",
        "global.json",
        "nuget.config",
    }
)
PROJECT_SUFFIXES = (".csproj",)
SOLUTION_SUFFIXES = (".sln", ".slnx")

# The files of csharp_runtime and where the scaffold puts them (the C# one beside the façade
# project's file). The first two stay after the retirement.
RUNTIME_FILES = {
    "codecoremorph_decimal.hpp": f"{INCLUDE_DIR}/codecoremorph_decimal.hpp",
    "codecoremorph_exceptions.hpp": f"{INCLUDE_DIR}/codecoremorph_exceptions.hpp",
    "codecoremorph_bridge.h": f"{BRIDGE_DIR}/codecoremorph_bridge.h",
    "codecoremorph_bridge.hpp": f"{BRIDGE_DIR}/codecoremorph_bridge.hpp",
    "codecoremorph_bridge.cpp": f"{BRIDGE_DIR}/codecoremorph_bridge.cpp",
}
BRIDGE_RUNTIME_HEADER = "codecoremorph_bridge.h"

# The test host runs the C++ under the sanitizers (D-78): the shared ASan runtime preloaded into
# `dotnet test`, CoreCLR keeping its own SIGSEGV handling, no leak report of the runtime's own.
ASAN_OPTIONS = "detect_leaks=0:handle_segv=0:allow_user_segv_handler=1:verify_asan_link_order=0"
UBSAN_OPTIONS = "print_stacktrace=1:halt_on_error=1"


def _snake(text: str) -> str:
    stem = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", text)
    stem = re.sub(r"[^A-Za-z0-9]+", "_", stem).strip("_").lower()
    if not stem or stem[0].isdigit():
        stem = f"cs_{stem}"
    return stem


def library_name(project: str) -> str:
    """The C++ libraries' stem for a C# project: ``OrderBook`` and ``Order.Book`` give
    ``order_book``."""
    stem = PurePosixPath(project.replace("\\", "/")).name
    return _snake(re.sub(r"\.csproj$", "", stem, flags=re.IGNORECASE))


def unit_name(type_name: str, taken: Collection[str]) -> str:
    """The stem of a type unit's C++ and bridge files, from the name of its first type:
    ``Zoo.OrderBook`` gives ``order_book``, ``Zoo.Pen`1`` gives ``pen``. A stem the runtime's
    files have (``codecoremorph_bridge``) or ``taken`` has (another unit's) gets a suffix."""
    stem = _snake(type_name.rsplit(".", 1)[-1].split("`", 1)[0])
    if stem.startswith("codecoremorph"):
        stem = f"{stem}_type"
    base, n = stem, 2
    while stem in taken:
        stem, n = f"{base}_{n}", n + 1
    return stem


def type_names(types: str) -> str:
    """A unit's ``types`` option (symbol IDs, comma-separated) as the stubs' comments name
    them: ``csharp:Zoo.Pen`1,csharp:Zoo.Pens`` gives ``Zoo.Pen, Zoo.Pens``."""
    names = [re.sub(r"`\d+", "", t.strip().removeprefix("csharp:")) for t in types.split(",")]
    return ", ".join(n for n in names if n)


def native_library(name: str) -> str:
    return f"{name}_native"


def cpp_library(name: str) -> str:
    return f"{name}_cpp"


def runtime_text(file_name: str) -> str:
    """A runtime file's text, as CodeCoreMorph ships it."""
    package = resources.files("codecoremorph.adapters.cpp.csharp_runtime")
    return (package / file_name).read_text(encoding="utf-8")


def cxx_standard(standard: str) -> str:
    """``cpp17`` gives ``17``."""
    number = standard.removeprefix("cpp").removeprefix("c++")
    if number not in {"17", "20", "23"}:
        raise ConfigError(
            f"a C# → C++ port targets C++17, 20 or 23, not {standard}",
            hint="Set migration.target.standard to cpp17, cpp20 or cpp23.",
        )
    return number


def top_cmake(name: str, project: str, standard: str) -> str:
    """The top-level CMake file: the C++ under ``cpp/``, sanitizers on request."""
    return (
        f"{TOP_MARK}: the C++ that replaces\n"
        f"# the C# library {project}, under cpp/. During the port the C# façades call it through\n"
        "# the bridge library; afterwards it is the project's build.\n"
        "cmake_minimum_required(VERSION 3.20)\n"
        f"project({name} LANGUAGES C CXX)\n"
        f"set(CMAKE_CXX_STANDARD {cxx_standard(standard)})\n"
        "set(CMAKE_CXX_STANDARD_REQUIRED ON)\n"
        "set(CMAKE_CXX_EXTENSIONS OFF)\n"
        "set(CMAKE_POSITION_INDEPENDENT_CODE ON)\n"
        "set(CMAKE_C_VISIBILITY_PRESET hidden)\n"
        "set(CMAKE_CXX_VISIBILITY_PRESET hidden)\n"
        "set(CMAKE_VISIBILITY_INLINES_HIDDEN ON)\n"
        "set(CMAKE_EXPORT_COMPILE_COMMANDS ON)\n"
        "set(CMAKE_LIBRARY_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/lib)\n"
        "set(CMAKE_ARCHIVE_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/lib)\n"
        f'option({SANITIZE} "Build with AddressSanitizer and UndefinedBehaviorSanitizer" OFF)\n'
        f"if({SANITIZE})\n"
        f"  add_compile_options({SANITIZER_FLAGS} -fno-sanitize-recover=undefined\n"
        "                      -fno-omit-frame-pointer)\n"
        f"  add_link_options({SANITIZER_FLAGS})\n"
        "endif()\n"
        "enable_testing()\n"
        "add_subdirectory(cpp)\n"
    )


def _listed(items: Iterable[str]) -> str:
    return "".join(f"  {s}\n" for s in sorted(set(items)))


def cpp_cmake(name: str, project: str, sources: Iterable[str], bridge: Iterable[str]) -> str:
    """``cpp/CMakeLists.txt``: the library of the ported types, the bridge over it, and the
    tests once there are some."""
    lib, native = cpp_library(name), native_library(name)
    return (
        f"# The C++ the types of the C# library {project} move into (CodeCoreMorph, D-78): the\n"
        f"# library {lib}, which stays, and during the port the bridge {native}, which the C#\n"
        "# façades call.\n"
        f"add_library({lib} STATIC\n"
        f"{SOURCES_BEGIN}\n"
        f"{_listed(sources)}"
        f"{SOURCES_END}\n"
        ")\n"
        f"target_include_directories({lib} PUBLIC include src)\n"
        f"target_compile_options({lib} PRIVATE -Wall -Wextra)\n"
        f"{BRIDGE_BEGIN}\n"
        f"add_library({native} SHARED\n"
        f"{BRIDGE_SOURCES_BEGIN}\n"
        f"{_listed(bridge)}"
        f"{BRIDGE_SOURCES_END}\n"
        ")\n"
        f"target_include_directories({native} PRIVATE bridge)\n"
        f"target_link_libraries({native} PRIVATE {lib})\n"
        f"target_compile_options({native} PRIVATE -Wall -Wextra)\n"
        "# The .NET test host preloads the shared ASan runtime (D-78).\n"
        f"if({SANITIZE})\n"
        f"  target_link_options({native} PRIVATE -shared-libasan)\n"
        "endif()\n"
        f"{BRIDGE_END}\n"
        "if(EXISTS ${CMAKE_CURRENT_SOURCE_DIR}/tests/CMakeLists.txt)\n"
        "  add_subdirectory(tests)\n"
        "endif()\n"
    )


def _marked(begin: str, end: str) -> re.Pattern[str]:
    return re.compile(rf"(?ms)^{re.escape(begin)}[ \t]*\r?\n(.*?)^{re.escape(end)}[ \t]*$")


_SOURCES = _marked(SOURCES_BEGIN, SOURCES_END)
_BRIDGE_SOURCES = _marked(BRIDGE_SOURCES_BEGIN, BRIDGE_SOURCES_END)
_NATIVE = re.compile(r"add_library\(\s*([A-Za-z_][A-Za-z0-9_]*)_native\s+SHARED")


def listed(text: str, *, bridge: bool = False) -> list[str]:
    match = (_BRIDGE_SOURCES if bridge else _SOURCES).search(text)
    if match is None:
        return []
    return [line.strip() for line in match.group(1).splitlines() if line.strip()]


def with_listed(text: str, source: str, *, bridge: bool = False) -> str | None:
    """``cpp/CMakeLists.txt`` compiling ``source`` (relative to ``cpp/``) too, in the library or
    the bridge; None when that marked list is gone."""
    pattern = _BRIDGE_SOURCES if bridge else _SOURCES
    match = pattern.search(text)
    if match is None:
        return None
    sources = listed(text, bridge=bridge)
    if source in sources:
        return text
    return text[: match.start(1)] + _listed([*sources, source]) + text[match.end(1) :]


def native_name(cpp_cmake_text: str) -> str | None:
    """The bridge library ``cpp/CMakeLists.txt`` builds (``shop_native``), if any."""
    match = _NATIVE.search(cpp_cmake_text)
    return f"{match.group(1)}_native" if match else None


_PROGRAMS = _marked(PROGRAMS_BEGIN, PROGRAMS_END)
_PROGRAM = re.compile(r"^add_executable\(\s*(\S+)\s+programs/([A-Za-z_]\w*)\.cpp\s*\)", re.M)
_BRIDGE_BLOCK = re.compile(
    rf"(?ms)^{re.escape(BRIDGE_BEGIN)}[ \t]*\r?\n.*?^{re.escape(BRIDGE_END)}[ \t]*(?:\r?\n|\Z)"
)


def programs(cpp_cmake_text: str) -> dict[str, str]:
    """The programs ``cpp/CMakeLists.txt`` builds: name → the stem of its ``cpp/programs``
    file."""
    match = _PROGRAMS.search(cpp_cmake_text)
    if match is None:
        return {}
    return {m.group(1): m.group(2) for m in _PROGRAM.finditer(match.group(1))}


def with_program(text: str, name: str, program: str, stem: str) -> str:
    """``cpp/CMakeLists.txt`` building the program ``program`` from ``programs/<stem>.cpp``,
    linked with the library, in its marked list (added at the end when it has none)."""
    found = programs(text)
    if found.get(program) == stem:
        return text
    found[program] = stem
    lines = "".join(
        f"add_executable({p} programs/{s}.cpp)\n"
        f"target_link_libraries({p} PRIVATE {cpp_library(name)})\n"
        f"target_compile_options({p} PRIVATE -Wall -Wextra)\n"
        for p, s in sorted(found.items())
    )
    match = _PROGRAMS.search(text)
    if match is not None:
        return text[: match.start(1)] + lines + text[match.end(1) :]
    head = text if text.endswith("\n") or not text else text + "\n"
    return (
        f"{head}# The programs (CodeCoreMorph, D-78): each runs its main in the library.\n"
        f"{PROGRAMS_BEGIN}\n{lines}{PROGRAMS_END}\n"
    )


def without_bridge(cpp_cmake_text: str) -> str:
    """``cpp/CMakeLists.txt`` without the bridge library (the retirement)."""
    return _BRIDGE_BLOCK.sub("", cpp_cmake_text)


def tests_cmake(name: str, test_programs: Iterable[str]) -> str:
    """``cpp/tests/CMakeLists.txt``: a GoogleTest program per translated C# test file, each a
    CTest test, its cases named in GoogleTest's report (``gtest:Suite.Name``)."""
    listed = "".join(f"codecoremorph_gtest({p})\n" for p in sorted(set(test_programs)))
    return (
        "# The tests translated from C# (CodeCoreMorph, D-78): a GoogleTest program per C# test\n"
        "# file, each a CTest test; GoogleTest's report names each case (gtest:Suite.Name).\n"
        "find_package(GTest REQUIRED)\n"
        "if(TARGET GTest::gtest_main)\n"
        "  set(_ccm_gtest GTest::gtest_main)\n"
        "else()\n"
        "  set(_ccm_gtest GTest::Main)\n"
        "endif()\n"
        "function(codecoremorph_gtest program)\n"
        "  add_executable(${program} ${program}.cpp)\n"
        f"  target_link_libraries(${{program}} PRIVATE {cpp_library(name)} ${{_ccm_gtest}})\n"
        "  target_compile_options(${program} PRIVATE -Wall -Wextra)\n"
        "  add_test(NAME ${program} COMMAND ${program})\n"
        "endfunction()\n"
        "\n"
        f"{TESTS_BEGIN}\n"
        f"{listed}"
        f"{TESTS_END}\n"
    )


_TEST_PROGRAM = re.compile(r"^\s*codecoremorph_gtest\(\s*([A-Za-z_]\w*)\s*\)", re.M)


def test_programs(tests_cmake_text: str) -> list[str]:
    """The GoogleTest programs ``cpp/tests/CMakeLists.txt`` builds."""
    return sorted({m.group(1) for m in _TEST_PROGRAM.finditer(tests_cmake_text)})


_SLN_PROJECT = re.compile(
    r'(?ms)^Project\("[^"]*"\)\s*=\s*"[^"]*"\s*,\s*"(?P<path>[^"]*)"\s*,\s*"(?P<guid>\{[^}]*\})"'
    r"[^\n]*\n.*?^EndProject[ \t]*(?:\r?\n|\Z)"
)
_SLNX_PROJECT = re.compile(
    r'(?ms)^[ \t]*<Project\s+Path="(?P<path>[^"]*)"[^>]*?(?:/>|>.*?</Project>)[ \t]*(?:\r?\n)?'
)


def _from(directory: str, path: str) -> str:
    return posixpath.normpath(posixpath.join(directory, path.replace("\\", "/")))


def without_projects(solution: str, text: str, removed: Collection[str]) -> str:
    """A solution's text (``solution`` is its worktree path) without the projects ``removed``
    (worktree paths), their configurations and nesting included."""
    directory = posixpath.dirname(solution)
    gone = set(removed)
    if solution.lower().endswith(".slnx"):
        return _SLNX_PROJECT.sub(
            lambda m: "" if _from(directory, m.group("path")) in gone else m.group(0), text
        )
    guids: set[str] = set()

    def drop(match: re.Match[str]) -> str:
        if _from(directory, match.group("path")) not in gone:
            return match.group(0)
        guids.add(match.group("guid").upper())
        return ""

    text = _SLN_PROJECT.sub(drop, text)
    if not guids:
        return text
    return "".join(
        line for line in text.splitlines(keepends=True) if not any(g in line.upper() for g in guids)
    )


def is_port(worktree: Path) -> bool:
    """Whether the project has a C# → C++ port's top-level CMake file."""
    top = worktree / TOP_CMAKE
    if not top.is_file():
        return False
    return TOP_MARK in top.read_bytes().decode("utf-8", errors="replace")


def bridge_headers(worktree: Path) -> list[str]:
    """The units' bridge headers (worktree paths), the runtime's own left out."""
    directory = worktree / BRIDGE_DIR
    if not directory.is_dir():
        return []
    return sorted(
        f"{BRIDGE_DIR}/{p.name}"
        for p in directory.glob("*.h")
        if p.is_file() and p.name != BRIDGE_RUNTIME_HEADER
    )


def facade_projects(worktree: Path) -> list[str]:
    """The directories (worktree paths) of the C# projects that hold façades: those with the
    support file."""
    found: list[str] = []
    for support in worktree.rglob(SUPPORT_FILE):
        rel = support.relative_to(worktree)
        if any(part in {"bin", "obj", "build", ".git"} for part in rel.parts[:-1]):
            continue
        found.append(rel.parent.as_posix())
    return sorted(found)


def initial_declarations(library: str) -> str:
    """The generated file before the bridge exports anything: the library's name, which the
    support file needs to compile."""
    return (
        "// <auto-generated>\n"
        "// Written by CodeCoreMorph from the bridge headers (cpp/bridge/*.h) when the C++ is\n"
        "// configured; do not edit. C# 7.3 and netstandard2.0.\n"
        "// </auto-generated>\n"
        f"namespace {NAMESPACE}\n"
        "{\n"
        "    internal static partial class CcmNative\n"
        "    {\n"
        f'        internal const string Library = "{library}";\n'
        "    }\n"
        "}\n"
    )


def test_environment(lib_dir: str, asan_runtime: str | None) -> dict[str, str]:
    """What ``dotnet test`` needs to load the bridge built with the sanitizers: the library's
    directory on the loader's path, and the shared ASan runtime preloaded (D-78)."""
    env = {"LD_LIBRARY_PATH": lib_dir}
    if asan_runtime:
        env |= {
            "LD_PRELOAD": asan_runtime,
            "ASAN_OPTIONS": ASAN_OPTIONS,
            "UBSAN_OPTIONS": UBSAN_OPTIONS,
        }
    return env


# --------------------------------------------------------------------------- the checks

# clang-tidy's bug-finding checks, and the casts between pointer types that only the bridge may
# make (its handles and contexts); and the compiler's warnings with the build's flags (-Wall
# -Wextra), which "-*" would turn off too.
BRIDGE_ONLY_CHECKS = frozenset(
    {"cppcoreguidelines-pro-type-reinterpret-cast", "cppcoreguidelines-pro-type-cstyle-cast"}
)
TIDY_CHECKS = ",".join(
    [
        "-*",
        "clang-diagnostic-*",
        "bugprone-*",
        "clang-analyzer-*",
        "-bugprone-easily-swappable-parameters",
        *sorted(BRIDGE_ONLY_CHECKS),
    ]
)
CPP_EXTENSIONS = (".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx", ".h")
_LINTED_DIRS = (SOURCE_DIR, BRIDGE_DIR, PROGRAMS_DIR, TESTS_DIR)


def is_runtime(rel: str) -> bool:
    """Whether ``rel`` is a file of the runtime CodeCoreMorph ships, which no unit edits."""
    return rel in RUNTIME_FILES.values()


def in_bridge(rel: str) -> bool:
    return rel.startswith(f"{BRIDGE_DIR}/")


def port_sources(worktree: Path) -> list[str]:
    """The port's own C++ (worktree paths): the library, the bridge, the programs and the tests,
    the runtime left out."""
    found: list[str] = []
    for directory in _LINTED_DIRS:
        root = worktree / directory
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            rel = path.relative_to(worktree).as_posix()
            if path.is_file() and rel.endswith(CPP_EXTENSIONS) and not is_runtime(rel):
                found.append(rel)
    return sorted(found)


def clang_tidy_argv(build_dir: str, files: Sequence[str], cpp_dir: str) -> list[str]:
    """clang-tidy over the port's C++ (sandbox paths), with the build's flags and the headers
    under ``cpp_dir`` (the sandbox's ``cpp/``) reported too."""
    header_filter = f"^{re.escape(cpp_dir)}/(src|bridge|programs|tests)/"
    return [
        "clang-tidy",
        "-p",
        build_dir,
        "--quiet",
        f"--checks={TIDY_CHECKS}",
        f"--header-filter={header_filter}",
        *files,
    ]


_NEW = re.compile(r"\bnew\b(?=\s*(?:\(|::|[A-Za-z_]))")
_DELETE = re.compile(r"\bdelete\b\s*(?:\[\s*\]\s*)?(?=[A-Za-z_(*:])")
# A new whose result a smart pointer takes at once: std::shared_ptr<T>(new T(...)), a member's
# reset(new T), std::unique_ptr<T> p{new T} (a private constructor's way to make_shared).
_OWNED = re.compile(
    r"(?:\b(?:shared_ptr|unique_ptr)\s*<[^;{}()]*>\s*(?:[A-Za-z_]\w*\s*)?[({]|\breset\s*\()\s*$"
)


def raw_ownership(text: str) -> list[tuple[int, str]]:
    """``(line, what)`` of each ``new`` and ``delete`` expression in C++ ``text`` whose result
    no smart pointer takes at once: an owning raw pointer, which outside the bridge a port
    doesn't have (C++'s owners are ``std::unique_ptr`` and ``std::shared_ptr``)."""
    masked = mask_c_family(text)
    found: list[tuple[int, str]] = []
    for match in _NEW.finditer(masked):
        before = masked[max(0, match.start() - 200) : match.start()]
        if re.search(r"\boperator\s*$", before):
            continue  # an operator new declared or called
        if _OWNED.search(before):
            continue
        line = masked.count("\n", 0, match.start()) + 1
        found.append((line, "a new whose result no smart pointer takes"))
    for match in _DELETE.finditer(masked):
        if re.search(r"(?:\boperator|=)\s*$", masked[max(0, match.start() - 40) : match.start()]):
            continue  # operator delete, or a deleted function (= delete)
        line = masked.count("\n", 0, match.start()) + 1
        found.append((line, "a delete of an owning raw pointer"))
    return sorted(found)


# --------------------------------------------------------------------------- scaffolds


class _Writer:
    """Writes files under the worktree with LF endings, remembering what it changed."""

    def __init__(self, worktree: Path):
        self.worktree = worktree
        self.changed: list[str] = []

    def read(self, rel: str) -> str | None:
        path = self.worktree / rel
        if not path.is_file():
            return None
        return path.read_bytes().decode("utf-8", errors="replace")

    def write(self, rel: str, text: str) -> None:
        path = self.worktree / rel
        data = text.encode("utf-8")
        if path.is_file() and path.read_bytes() == data:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self.changed.append(rel)

    def create(self, rel: str, text: str) -> None:
        if self.read(rel) is None:
            self.write(rel, text)

    def delete(self, rel: str) -> None:
        path = self.worktree / rel
        if path.is_file():
            path.unlink()
            self.changed.append(rel)

    def prune(self) -> None:
        """Remove the directories the deletions left empty, deepest first."""
        directories = {
            parent
            for rel in self.changed
            if not (self.worktree / rel).exists()
            for parent in PurePosixPath(rel).parents
            if parent.as_posix() != "."
        }
        for directory in sorted(directories, key=lambda d: len(d.parts), reverse=True):
            path = self.worktree / directory.as_posix()
            if path.is_dir() and not any(path.iterdir()):
                path.rmdir()


def _identifier(options: Mapping[str, str], name: str, what: str) -> str:
    value = options.get(name, "")
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", value):
        raise ConfigError(
            f"the C# → C++ scaffold needs {what} (its {name} option, a C identifier), got "
            f"{value!r}",
            hint="The planner sets it from the unit's port; re-plan the run.",
        )
    return value


def _project(worktree: Path, options: Mapping[str, str]) -> str:
    project = options.get("build_target", "").replace("\\", "/")
    if not project.lower().endswith(".csproj") or not (worktree / project).is_file():
        raise ConfigError(
            f"the C# → C++ scaffold needs the façade project's file, got {project!r}",
            hint="The planner sets it from the unit's types; re-plan the run.",
        )
    return project


def header_stub(name: str, unit: str, types: str) -> str:
    guard = f"{name}_{unit}_hpp".upper()
    return (
        f"// The C++ of {types}, ported from C# (CodeCoreMorph, D-78).\n"
        f"#ifndef {guard}\n#define {guard}\n\n#endif\n"
    )


def source_stub(unit: str, types: str) -> str:
    return f'// The C++ of {types}, ported from C# (CodeCoreMorph, D-78).\n#include "{unit}.hpp"\n'


def bridge_header_stub(name: str, unit: str, types: str) -> str:
    guard = f"{name}_bridge_{unit}_h".upper()
    return (
        f"/* The bridge of {types}: the exports its C# façade calls (CodeCoreMorph, D-78). */\n"
        f"#ifndef {guard}\n#define {guard}\n\n"
        f'#include "{BRIDGE_RUNTIME_HEADER}"\n\n'
        '#ifdef __cplusplus\nextern "C" {\n#endif\n\n'
        "#ifdef __cplusplus\n}\n#endif\n\n#endif\n"
    )


def bridge_source_stub(unit: str, types: str) -> str:
    return (
        f"// The bridge of {types} (CodeCoreMorph, D-78).\n"
        f'#include "{unit}.h"\n\n'
        '#include "codecoremorph_bridge.hpp"\n'
        f'#include "{unit}.hpp"\n'
    )


def _base(out: _Writer, name: str, project: str, standard: str) -> str:
    """The CMake files and the runtime, written once; ``cpp/CMakeLists.txt``'s text."""
    out.create(TOP_CMAKE, top_cmake(name, project, standard))
    out.create(CPP_CMAKE, cpp_cmake(name, project, [], ["bridge/codecoremorph_bridge.cpp"]))
    for file_name, rel in RUNTIME_FILES.items():
        out.create(rel, runtime_text(file_name))
    folder = PurePosixPath(project).parent.as_posix()
    prefix = "" if folder == "." else f"{folder}/"
    out.create(f"{prefix}{SUPPORT_FILE}", runtime_text(SUPPORT_FILE))
    out.create(f"{prefix}{GENERATED_FILE}", initial_declarations(native_library(name)))
    out.create(SYMBOL_MAP, SYMBOL_MAP_TEXT)
    return out.read(CPP_CMAKE) or ""


def _add(out: _Writer, cmake: str, source: str, *, bridge: bool) -> str:
    updated = with_listed(cmake, source, bridge=bridge)
    if updated is None:
        raise ConfigError(
            f"{CPP_CMAKE} lost its marked list of the "
            f"{'bridge sources' if bridge else 'ported types'}",
            hint="Put the list back (the scaffold's markers), or add the source by hand.",
        )
    if updated != cmake:
        out.write(CPP_CMAKE, updated)
    return updated


def _type(out: _Writer, options: Mapping[str, str], standard: str) -> None:
    """A unit of types: the CMake files and runtime (the first unit), the unit's C++ and bridge
    files, and its sources in the libraries. The options are the unit's port's: ``crate`` (the
    C++ libraries' stem), ``module`` (the unit's files' stem), ``build_target`` (the façade
    project's file) and ``types`` (the C# types it ports)."""
    name = _identifier(options, "crate", "the C++ libraries' name")
    unit = _identifier(options, "module", "the unit's name")
    project = _project(out.worktree, options)
    types = type_names(options.get("types", "")) or unit
    cmake = _base(out, name, project, standard)
    out.create(f"{SOURCE_DIR}/{unit}.hpp", header_stub(name, unit, types))
    out.create(f"{SOURCE_DIR}/{unit}.cpp", source_stub(unit, types))
    out.create(f"{BRIDGE_DIR}/{unit}.h", bridge_header_stub(name, unit, types))
    out.create(f"{BRIDGE_DIR}/{unit}.cpp", bridge_source_stub(unit, types))
    cmake = _add(out, cmake, f"src/{unit}.cpp", bridge=False)
    _add(out, cmake, f"bridge/{unit}.cpp", bridge=True)


def program_header_stub(name: str, stem: str, program: str) -> str:
    guard = f"{name}_{stem}_main_hpp".upper()
    return (
        f"// The program {program}, ported from C# (CodeCoreMorph, D-78). {stem}_main is its\n"
        "// Main, given the arguments without the program's name, returning the exit code. Its\n"
        "// other code goes in a namespace from its C# one.\n"
        f"#ifndef {guard}\n#define {guard}\n\n"
        "#include <string>\n#include <vector>\n\n"
        f"int {stem}_main(const std::vector<std::string>& args);\n\n"
        "#endif\n"
    )


def program_source_stub(stem: str, program: str) -> str:
    return (
        f"// The program {program}, ported from C# (CodeCoreMorph, D-78).\n"
        f'#include "{stem}_main.hpp"\n'
    )


def program_entry(stem: str, program: str) -> str:
    return (
        f"// The program {program} (CodeCoreMorph, D-78): {stem}_main, in the library, is its\n"
        "// main.\n"
        "#include <string>\n#include <vector>\n\n"
        f'#include "{stem}_main.hpp"\n\n'
        "int main(int argc, char** argv)\n{\n"
        f"    return {stem}_main(std::vector<std::string>(argv + 1, argv + argc));\n"
        "}\n"
    )


def program_export(name: str, stem: str) -> str:
    """The export a C# program's ``Main`` calls."""
    return f"{name}_{stem}_main"


def program_bridge_header(name: str, stem: str, program: str) -> str:
    guard = f"{name}_bridge_{stem}_h".upper()
    export = program_export(name, stem)
    return (
        f"/* The bridge of the program {program}: the export its C# Main calls (CodeCoreMorph,\n"
        " * D-78). */\n"
        f"#ifndef {guard}\n#define {guard}\n\n"
        f'#include "{BRIDGE_RUNTIME_HEADER}"\n\n'
        '#ifdef __cplusplus\nextern "C" {\n#endif\n\n'
        "/* Runs the program with its arguments, its exit code in *result. */\n"
        f"CCM_EXPORT ccm_status {export}(const char **args_data, int32_t args_count,\n"
        f"    {' ' * len(export)}int32_t *result);\n\n"
        "#ifdef __cplusplus\n}\n#endif\n\n#endif\n"
    )


def program_bridge_source(name: str, stem: str, program: str) -> str:
    export = program_export(name, stem)
    return (
        f"// The bridge of the program {program} (CodeCoreMorph, D-78).\n"
        f'#include "{stem}.h"\n\n'
        "#include <iostream>\n#include <string>\n#include <vector>\n\n"
        '#include "codecoremorph_bridge.hpp"\n'
        f'#include "{stem}_main.hpp"\n\n'
        f"ccm_status {export}(const char **args_data, int32_t args_count, int32_t *result)\n"
        "{\n"
        "    const ccm_status status = ccm::bridge::guard([&] {\n"
        "        std::vector<std::string> args;\n"
        "        for (int32_t i = 0; i < args_count; ++i) {\n"
        "            args.emplace_back(args_data[i]);\n"
        "        }\n"
        f"        *result = {stem}_main(args);\n"
        "    });\n"
        "    // The .NET host exits without flushing C++'s streams.\n"
        "    std::cout.flush();\n"
        "    std::cerr.flush();\n"
        "    return status;\n"
        "}\n"
    )


def _program(out: _Writer, options: Mapping[str, str], standard: str) -> None:
    """A program: its code a function of the library, ``<stem>_main``, which the C# program's
    ``Main`` calls through the bridge until the retirement, and a CMake program that calls it.
    The options are the unit's port's: ``crate``, ``module`` (the stem), ``build_target`` (the
    program's project, which gets the façades' support) and ``bin`` (the program's name)."""
    name = _identifier(options, "crate", "the C++ libraries' name")
    stem = _identifier(options, "module", "the program's stem")
    project = _project(out.worktree, options)
    program = options.get("bin") or stem
    if not re.fullmatch(r"[A-Za-z0-9_.+-]+", program):
        raise ConfigError(
            f"the C# → C++ program scaffold needs a CMake target name (its bin option), got "
            f"{program!r}",
            hint="The planner sets it from the program's project; re-plan the run.",
        )
    cmake = _base(out, name, project, standard)
    out.create(f"{SOURCE_DIR}/{stem}_main.hpp", program_header_stub(name, stem, program))
    out.create(f"{SOURCE_DIR}/{stem}_main.cpp", program_source_stub(stem, program))
    out.create(f"{BRIDGE_DIR}/{stem}.h", program_bridge_header(name, stem, program))
    out.create(f"{BRIDGE_DIR}/{stem}.cpp", program_bridge_source(name, stem, program))
    out.create(f"{PROGRAMS_DIR}/{stem}.cpp", program_entry(stem, program))
    cmake = _add(out, cmake, f"src/{stem}_main.cpp", bridge=False)
    cmake = _add(out, cmake, f"bridge/{stem}.cpp", bridge=True)
    updated = with_program(cmake, name, program, stem)
    if updated != cmake:
        out.write(CPP_CMAKE, updated)


def tests_stub(source: str | None) -> str:
    what = source or "a C# test file (the unit's note names it)"
    return (
        f"// The tests of {what}, translated from C# (CodeCoreMorph, D-78): a GoogleTest test\n"
        f"// per C# test case, each mapped in {TEST_MAP}.\n"
        "#include <gtest/gtest.h>\n"
    )


def _tests(out: _Writer, options: Mapping[str, str], files: Sequence[str]) -> None:
    """A C# test file's GoogleTest program ``cpp/tests/<module>.cpp`` and its CTest test, and
    the test map. The C++ the tests use is there already: the type units wrote it."""
    name = _identifier(options, "crate", "the C++ libraries' name")
    program = _identifier(options, "module", "the test program's name")
    if out.read(CPP_CMAKE) is None:
        raise ConfigError(
            "a C# test file is translated to GoogleTest after the types it tests are C++, and "
            f"{CPP_CMAKE} isn't there",
            hint="Plan the port's type units first (port_csharp_type_to_cpp).",
        )
    # The C# files are the unit's read-only references, which a run doesn't pass to codemods.
    source = next((f for f in files if f.lower().endswith(".cs")), None)
    out.create(TEST_MAP, TEST_MAP_TEXT)
    listed = test_programs(out.read(TESTS_CMAKE) or "")
    out.write(TESTS_CMAKE, tests_cmake(name, [*listed, program]))
    out.create(f"{TESTS_DIR}/{program}.cpp", tests_stub(source))


def _retire(out: _Writer, options: Mapping[str, str], files: Sequence[str]) -> None:
    """Delete the C#: the unit's C# files and projects, its solutions and MSBuild files, the
    façades' support and the bridge. With ``keep`` (``keep_source_api``) the unit names only the
    programs and the tests that need them: the solutions drop them, and the rest stays."""
    keep = options.get("keep") == "true"
    removed: list[str] = []
    for rel in files:
        lower = rel.lower()
        if lower.endswith(PROJECT_SUFFIXES):
            removed.append(rel)
            out.delete(rel)
        elif lower.endswith(".cs"):
            out.delete(rel)
    for rel in files:
        lower = rel.lower()
        if lower.endswith(SOLUTION_SUFFIXES):
            text = out.read(rel)
            if not keep:
                out.delete(rel)
            elif text is not None:
                out.write(rel, without_projects(rel, text, removed))
        elif posixpath.basename(lower) in MSBUILD_FILES and not keep:
            out.delete(rel)
    gone = {posixpath.dirname(p) or "." for p in removed}
    for directory in facade_projects(out.worktree):
        if keep and directory not in gone:
            continue
        prefix = "" if directory == "." else f"{directory}/"
        for file_name in (SUPPORT_FILE, GENERATED_FILE, EXCEPTIONS_FILE):
            out.delete(f"{prefix}{file_name}")
    if not keep:
        bridge = out.worktree / BRIDGE_DIR
        if bridge.is_dir():
            for path in sorted(p for p in bridge.rglob("*") if p.is_file()):
                out.delete(path.relative_to(out.worktree).as_posix())
        cmake = out.read(CPP_CMAKE)
        if cmake is not None:
            out.write(CPP_CMAKE, without_bridge(cmake))
    out.prune()


def scaffold(
    worktree: Path, tool: str, options: Mapping[str, str], files: Sequence[str], standard: str
) -> list[str]:
    """Run one of the C# → C++ scaffolds; the worktree paths it changed."""
    out = _Writer(worktree)
    if tool == "cpp-csharp-type":
        _type(out, options, standard)
    elif tool == "cpp-csharp-program":
        _program(out, options, standard)
    elif tool == "cpp-csharp-tests":
        _tests(out, options, files)
    elif tool == "cpp-csharp-retire":
        _retire(out, options, files)
    else:
        raise ConfigError(f"no C# → C++ scaffold {tool!r}", hint=f"Available: {sorted(SCAFFOLDS)}")
    return out.changed


SYMBOL_MAP_TEXT = """\
# The C# symbols this port moved to C++, and the C++ that took them over (CodeCoreMorph, D-78).
# One entry per public member of a ported type:
#
#   [[symbol]]
#   csharp = "csharp:Shop.Cart.Add(Shop.Item,System.Int32)"
#   cpp = "shop::Cart::add"        # or a list of names
#   note = "..."                   # optional: what C++ couldn't keep, or why cpp is empty
"""

TEST_MAP_TEXT = """\
# The C# tests this port translated to C++, and the GoogleTest tests that took them over
# (CodeCoreMorph, D-78). One entry per C# test:
#
#   [[test]]
#   csharp = "dotnet:Shop.Tests.CartTests#AddTakesFromStock"
#   cpp = "gtest:CartTests.AddTakesFromStock"
#   note = "..."                   # optional
"""
