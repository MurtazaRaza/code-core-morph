"""What a port's retirement leaves that still builds the code it deleted (D-89): the build files
of builds the run didn't use, and the CI configurations, which every port's retirement deletes
or lists for review (C and C++ to Rust, Rust to C, C# to C++ and to Rust)."""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

_BUILD_FILE = re.compile(
    r"(?:^|/)(?:GNUmakefile|[Mm]akefile(?:[.-][^/]*)?|[^/]+\.(?:mk|mak|cmake|vcxproj|vcproj|dsp)"
    r"|CMakeLists\.txt|meson\.build|configure\.(?:ac|in)|SConstruct|build\.ninja)$"
)
_CI_FILE = re.compile(
    r"(?:^|/)(?:\.github/workflows/[^/]+\.ya?ml|\.travis\.yml|\.?appveyor\.yml|\.gitlab-ci\.yml"
    r"|azure-pipelines\.yml|\.circleci/config\.yml|Jenkinsfile|\.cirrus\.yml)$"
)
# The commands that build each source language, as a CI configuration runs them.
RUNS_C_BUILD = re.compile(
    r"(?<![\w-])(?:c?make|\./configure|autoreconf|gcc|g\+\+|clang(?:\+\+)?|[Mm][Ss][Bb]uild"
    r"|meson|ninja|scons)(?![\w-])"
)
RUNS_CARGO = re.compile(r"(?<![\w-])(?:cargo|rustc|rustup)(?![\w-])")
RUNS_DOTNET = re.compile(r"(?<![\w-])(?:dotnet|[Mm][Ss][Bb]uild|nuget|vstest\.console)(?![\w-])")


def is_build_file(path: str) -> bool:
    """Whether ``path`` is a build file of some build system (a Makefile and its variants, a
    CMake file, a Visual Studio project, meson, autoconf's input, SCons, ninja)."""
    return _BUILD_FILE.search(path) is not None


def is_ci_file(path: str) -> bool:
    """Whether ``path`` is a CI configuration (GitHub Actions, Travis, AppVeyor, GitLab, Azure,
    CircleCI, Jenkins, Cirrus)."""
    return _CI_FILE.search(path) is not None


def worktree_files(worktree: Path, skip: tuple[str, ...] = ()) -> Iterator[tuple[str, Path]]:
    """The worktree's files outside ``.git/`` and the ``skip`` prefixes, sorted, as (worktree
    path, path)."""
    for path in sorted(worktree.rglob("*")):
        rel = path.relative_to(worktree).as_posix()
        if rel.startswith((".git/", *skip)) or not path.is_file():
            continue
        yield rel, path


def read_text(path: Path) -> str | None:
    """A file's text, or None when it isn't UTF-8 text (a binary file names no source)."""
    try:
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None
