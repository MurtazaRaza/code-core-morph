"""The files of a Make build (D-68): which they are, and the standards their ``-std=`` options
name. No process runs here; :mod:`codecoremorph.adapters.cpp.make` builds."""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from pathlib import Path

from codecoremorph.core.globs import PathMatcher

MAKEFILE_NAMES = frozenset({"Makefile", "makefile", "GNUmakefile"})
# Files a configure step reads: when one changes, configure runs again.
CONFIGURE_INPUTS = frozenset({"configure", "configure.ac", "configure.in", "aclocal.m4"})
TEMPLATE_NAMES = frozenset({"Makefile.am", "Makefile.in"})
_SKIPPED_DIRS = frozenset({".git", ".codecoremorph"})
_STD = re.compile(r"-std=(?:c|gnu)(?P<cxx>\+\+)?(?P<version>\w+)")
_ANSI = re.compile(r"(?<![\w-])-ansi\b")


def is_make_file(path: str) -> bool:
    """A file of a Make build: Makefiles, their templates and includes, and configure's own."""
    name = path.rsplit("/", 1)[-1]
    return (
        name in MAKEFILE_NAMES
        or name in CONFIGURE_INPUTS
        or name in TEMPLATE_NAMES
        or name.endswith((".mk", ".am", ".m4"))
    )


def is_configure_input(path: str) -> bool:
    """A file whose change makes configure run again (and autotools regenerate)."""
    name = path.rsplit("/", 1)[-1]
    return name in CONFIGURE_INPUTS or name.endswith((".am", ".in", ".m4"))


def is_autotools_input(path: str) -> bool:
    """A file ``autoreconf`` reads: ``configure.ac``, a ``Makefile.am``, an m4 file."""
    name = path.rsplit("/", 1)[-1]
    return name in ("configure.ac", "configure.in") or name.endswith((".am", ".m4"))


def make_files(worktree: Path, exclude: PathMatcher) -> list[str]:
    """The Make build's files in the worktree, by POSIX path."""
    found: list[str] = []
    for current, dirs, files in os.walk(worktree):
        dirs[:] = sorted(d for d in dirs if d not in _SKIPPED_DIRS)
        rel_root = Path(current).relative_to(worktree).as_posix()
        for name in sorted(files):
            rel = name if rel_root == "." else f"{rel_root}/{name}"
            if is_make_file(rel) and not exclude.matches(rel):
                found.append(rel)
    return found


def has_make_build(root: Path) -> bool:
    """Whether ``root`` holds a Make build: a Makefile, ``configure`` or autotools' inputs."""
    names = MAKEFILE_NAMES | TEMPLATE_NAMES | {"configure", "configure.ac", "configure.in"}
    return any((root / n).is_file() for n in names)


def standards_in(texts: Iterable[str], *, cxx: bool) -> list[str]:
    """The standards ``-std=`` options in build files name, as ``c99`` or ``cpp11`` (a GNU
    dialect counts as its standard): the C ones, or with ``cxx`` the C++ ones."""
    found: list[str] = []
    for text in texts:
        for m in _STD.finditer(text):
            if bool(m["cxx"]) == cxx:
                found.append(("cpp" if cxx else "c") + m["version"])
        # -ansi is C89, or C++98 for C++ (it says nothing about which the file compiles).
        found += ["cpp98" if cxx else "c89"] * len(_ANSI.findall(text))
    return found
