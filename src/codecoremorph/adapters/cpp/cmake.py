"""CMake: command lines and the build-file edit of the standard bump (SPEC §11.1)."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from codecoremorph.core.errors import ConfigError

STANDARD_ALIASES = {"0x": "11", "1y": "14", "1z": "17", "2a": "20", "2b": "23", "2c": "26"}

# The standards CodeCoreMorph builds and migrates, oldest first (D-38). C++26 waits for an LLVM
# with full support; the indexer's libclang is pinned to 18 (D-33).
SUPPORTED_STANDARDS = ("cpp98", "cpp03", "cpp11", "cpp14", "cpp17", "cpp20", "cpp23")
PRE_CXX11 = ("98", "03")


def cxx_standard_number(standard: str) -> str:
    """``cpp20`` → ``20``. Raises :class:`ConfigError` for anything else."""
    match = re.fullmatch(r"(?:cpp|c\+\+|cxx)?(\d{2})", standard.strip().lower())
    if match is None or f"cpp{match.group(1)}" not in SUPPORTED_STANDARDS:
        raise ConfigError(
            f"{standard!r} is not a C++ standard CodeCoreMorph supports",
            hint=f"Use one of {', '.join(SUPPORTED_STANDARDS)}.",
        )
    return match.group(1)


def cmake_standard(number: str) -> str:
    """CMake's ``CMAKE_CXX_STANDARD`` value: CMake has no 03; 98 covers C++03 too."""
    return "98" if number == "03" else number


def std_flag(number: str, *, gnu: bool) -> str:
    """The clang ``-std=`` flag for a standard number."""
    return f"-std={'gnu' if gnu else 'c'}++{number}"


def _rank(number: str) -> int:
    value = int(number)
    return value + 100 if value < 50 else value  # 98 and 03 sort before 11


def configure_command(
    source_dir: str,
    build_dir: str,
    *,
    standard: str | None,
    extra_args: list[str],
    build_type: str = "Debug",
    initial_cache: str | None = None,
    fresh: bool = False,
) -> list[str]:
    argv = ["cmake"]
    if fresh:
        # Drop the old cache: packages found for another standard (GoogleTest 1.8.1 for
        # C++98/03) must not survive a standard change.
        argv.append("--fresh")
    if initial_cache is not None:
        argv += ["-C", initial_cache]
    argv += [
        "-S",
        source_dir,
        "-B",
        build_dir,
        "-G",
        "Ninja",
        "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
        f"-DCMAKE_BUILD_TYPE={build_type}",
    ]
    if standard is not None:
        # Only effective when the project doesn't set CMAKE_CXX_STANDARD itself; the standard
        # bump rule edits the build files for that case.
        argv += [
            f"-DCMAKE_CXX_STANDARD={cmake_standard(standard)}",
            "-DCMAKE_CXX_STANDARD_REQUIRED=ON",
        ]
    return argv + list(extra_args)


# C++98/03 builds need the GoogleTest release that still supports them; install-toolchain
# builds it and names its prefix in CODECOREMORPH_CXX98_PREFIX_PATH (D-38). The variable is
# read inside the sandbox, where the toolchain environment applies.
CXX98_INITIAL_CACHE = """\
# Written by CodeCoreMorph for a C++98/03 build: find the GoogleTest release that supports it.
if(DEFINED ENV{CODECOREMORPH_CXX98_PREFIX_PATH})
  file(TO_CMAKE_PATH "$ENV{CODECOREMORPH_CXX98_PREFIX_PATH}" _codecoremorph_prefix)
  set(CMAKE_PREFIX_PATH "${_codecoremorph_prefix}" CACHE PATH "C++98/03 packages")
endif()
"""


# Every configure asks CMake's file API for the codemodel, which names each target's sources
# (D-58). CMake reads query files when it generates, so the initial cache can write the query,
# inside the sandbox, where the build tree lives.
FILE_API_QUERY = """\
# Written by CodeCoreMorph: ask CMake's file API for the codemodel (the targets' sources).
file(MAKE_DIRECTORY "${CMAKE_BINARY_DIR}/.cmake/api/v1/query")
file(TOUCH "${CMAKE_BINARY_DIR}/.cmake/api/v1/query/codemodel-v2")
"""


def initial_cache_script(standard_number: str) -> str:
    """The ``-C`` script of a configure: the file API query, and for C++98/03 the GoogleTest
    release that supports them."""
    if standard_number in PRE_CXX11:
        return FILE_API_QUERY + CXX98_INITIAL_CACHE
    return FILE_API_QUERY


def build_command(build_dir: str, targets: list[str]) -> list[str]:
    argv = ["cmake", "--build", build_dir]
    for target in targets:
        argv += ["--target", target]
    # Keep going after the first failure, so one build reports every error.
    return [*argv, "--", "-k", "0"]


def is_cmake_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name == "CMakeLists.txt" or name.endswith(".cmake")


@dataclass(frozen=True)
class StandardEdit:
    text: str
    replacements: int


_SET_STANDARD = re.compile(r"(set\s*\(\s*CMAKE_CXX_STANDARD\s+\"?)(\d+)(\"?\s*\))", re.IGNORECASE)
_PROPERTY = re.compile(r"(\bCXX_STANDARD\s+\"?)(\d+)\b")
_FEATURE = re.compile(r"\bcxx_std_(\d+)\b")
_FLAG = re.compile(r"([-/]std[:=](?:c|gnu)\+\+)(0x|1[14yz7]|11|2[03ab]|2c|98|03)\b")
_PROJECT = re.compile(r"^[ \t]*project[ \t]*\(.*?\)", re.IGNORECASE | re.MULTILINE | re.DOTALL)
_BRACKET_OPEN = re.compile(r"\[(=*)\[")


def _bracket_end(text: str, match: re.Match[str]) -> int:
    close = f"]{match.group(1)}]"
    end = text.find(close, match.end())
    return len(text) if end < 0 else end + len(close)


def masked_comments(text: str) -> str:
    """``text`` with its CMake comments (``# ...`` and ``#[[ ... ]]``) blanked out, keeping
    every offset and line break, so patterns match only code. Quoted and bracket arguments
    are code: a ``#`` inside them starts no comment."""
    chars = list(text)
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
            i += 1
        elif c == "#":
            bracket = _BRACKET_OPEN.match(text, i + 1)
            if bracket is not None:
                end = _bracket_end(text, bracket)
            else:
                newline = text.find("\n", i)
                end = n if newline < 0 else newline
            for j in range(i, end):
                if chars[j] not in "\r\n":
                    chars[j] = " "
            i = end
        elif c == "[" and (bracket := _BRACKET_OPEN.match(text, i)) is not None:
            i = _bracket_end(text, bracket)
        else:
            i += 1
    return "".join(chars)


def _sub_code(pattern: re.Pattern[str], replace: Callable[[re.Match[str]], str], text: str) -> str:
    """``pattern.sub`` on the code of a CMake file; comments stay as they are."""
    masked = masked_comments(text)
    parts: list[str] = []
    pos = 0
    for match in pattern.finditer(masked):
        if text[match.start() : match.end()] != match.group(0):
            continue  # the match runs through a comment
        parts += [text[pos : match.start()], replace(match)]
        pos = match.end()
    parts.append(text[pos:])
    return "".join(parts)


def cmake_standards(text: str) -> list[str]:
    """The C++ standard numbers a CMake file sets (``["11", "17"]``), comments ignored:
    ``CMAKE_CXX_STANDARD``, ``CXX_STANDARD`` properties, ``cxx_std_NN`` and ``-std=``."""
    code = masked_comments(text)
    found = [m.group(2) for m in _SET_STANDARD.finditer(code)]
    found += [m.group(2) for m in _PROPERTY.finditer(code)]
    found += [m.group(1) for m in _FEATURE.finditer(code)]
    found += [m.group(2) for m in _FLAG.finditer(code)]
    numbers = [STANDARD_ALIASES.get(n, n) for n in found]
    return [n for n in numbers if f"cpp{n}" in SUPPORTED_STANDARDS]


def lowest_standard(texts: Iterable[str]) -> str | None:
    """The oldest standard any of the build files sets, as ``cpp11``; None when none sets one.

    After the standard bump every setting is at the target, so this is the target; before it,
    it is the oldest standard the project builds any code with."""
    numbers = [n for text in texts for n in cmake_standards(text)]
    if not numbers:
        return None
    return f"cpp{min(numbers, key=_rank)}"


def bump_cmake_standard(text: str, target: str, *, top_level: bool) -> StandardEdit:
    """Raise every C++ standard setting in a CMake file to ``target`` (``"20"``).

    Settings already at or above the target are left alone, and so are comments: a comment
    that mentions ``-std=c++11`` describes the past, not the build. In the top-level file of a
    project that sets no standard at all, ``set(CMAKE_CXX_STANDARD ...)`` is added after
    ``project()``.
    """
    count = 0
    target_rank = _rank(target)

    def bump(old: str) -> bool:
        normalized = STANDARD_ALIASES.get(old, old)
        return _rank(normalized) < target_rank

    def replace_number(match: re.Match[str]) -> str:
        nonlocal count
        if not bump(match.group(2)):
            return match.group(0)
        count += 1
        return f"{match.group(1)}{target}{match.group(3) if match.lastindex == 3 else ''}"

    def replace_feature(match: re.Match[str]) -> str:
        nonlocal count
        if not bump(match.group(1)):
            return match.group(0)
        count += 1
        return f"cxx_std_{target}"

    def replace_flag(match: re.Match[str]) -> str:
        nonlocal count
        if not bump(match.group(2)):
            return match.group(0)
        count += 1
        return f"{match.group(1)}{target}"

    code = masked_comments(text)
    sets_standard = bool(
        _SET_STANDARD.search(code) or _PROPERTY.search(code) or _FEATURE.search(code)
    )
    new = _sub_code(_SET_STANDARD, replace_number, text)
    new = _sub_code(_PROPERTY, replace_number, new)
    new = _sub_code(_FEATURE, replace_feature, new)
    new = _sub_code(_FLAG, replace_flag, new)
    if top_level and not sets_standard:
        project = _PROJECT.search(masked_comments(new))
        if project is not None:
            newline = "\r\n" if "\r\n" in new else "\n"
            insert = (
                f"{newline}{newline}set(CMAKE_CXX_STANDARD {target}){newline}"
                f"set(CMAKE_CXX_STANDARD_REQUIRED ON)"
            )
            new = new[: project.end()] + insert + new[project.end() :]
            count += 1
    return StandardEdit(new, count)
