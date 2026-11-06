"""What ``codecoremorph init`` reads from a C project (FR-1.4, D-68): its build (Make or CMake),
the standard its build files set or one guessed from the code, and its layout."""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

from codecoremorph.adapters.cpp.detect import (
    CODE_EXTENSIONS,
    CXX_ONLY,
    find_vendored_dirs,
    masked_comments,
    test_layout,
)
from codecoremorph.adapters.cpp.makefiles import has_make_build, is_make_file, standards_in
from codecoremorph.core.globs import PathMatcher
from codecoremorph.core.models import ProjectDetection

C_STANDARDS = ("c89", "c99", "c11", "c17", "c23")
# -std= names that are another name of one of C_STANDARDS.
C_ALIASES = {"c90": "c89", "c9x": "c99", "c1x": "c11", "c18": "c17", "c2x": "c23", "cansi": "c89"}
CMAKE_C_STANDARD = re.compile(r"\bC_STANDARD\s+(\d+)")
_PROJECT_NAME = re.compile(r"\bproject\s*\(\s*([A-Za-z_][\w.-]*)", re.IGNORECASE)
_AC_INIT = re.compile(r"\bAC_INIT\s*\(\s*\[?([\w.+-]+)")
_MAX_SCAN_FILES = 200
# Code that needs a newer standard than C89, oldest first: what the guess looks for.
_C99_SIGNS = (
    ("<stdint.h>", re.compile(r"#\s*include\s*<stdint\.h>")),
    ("<stdbool.h>", re.compile(r"#\s*include\s*<stdbool\.h>")),
    ("a declaration in a for loop", re.compile(r"\bfor\s*\(\s*(?:const\s+)?\w+\s+\**\w+\s*=")),
    ("a // comment", re.compile(r"(?m)^[^\"'\n]*?//")),
)
_C11_SIGNS = (
    ("_Static_assert", re.compile(r"\b_Static_assert\b")),
    ("_Generic", re.compile(r"\b_Generic\b")),
    ("_Atomic", re.compile(r"\b_Atomic\b|<stdatomic\.h>")),
)


def normalized(standard: str) -> str:
    """A ``-std=`` name as one of :data:`C_STANDARDS` (``c90`` -> ``c89``)."""
    return C_ALIASES.get(standard, standard)


def _read(root: Path, path: str) -> str | None:
    try:
        return (root / path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def guess_standard(texts: Sequence[tuple[str, str]]) -> tuple[str, list[str]]:
    """The oldest standard the code fits, and what in it needs that standard."""
    found11 = [
        f"{what} ({path})" for path, text in texts for what, p in _C11_SIGNS if p.search(text)
    ]
    if found11:
        return "c11", found11
    found99 = [
        f"{what} ({path})" for path, text in texts for what, p in _C99_SIGNS if p.search(text)
    ]
    if found99:
        return "c99", found99
    return "c89", []


def detect(root: Path, files: Sequence[str]) -> ProjectDetection | None:
    """A C project's starter settings, or None when it has no C code of its own (a project
    with C++ sources is the C++ adapter's)."""
    vendored = find_vendored_dirs(files)
    outside = PathMatcher(vendored)
    code = sorted(
        f for f in files if f.lower().endswith(CODE_EXTENSIONS) and not outside.matches(f)
    )
    if not any(f.endswith(".c") for f in code) or any(f.lower().endswith(CXX_ONLY) for f in code):
        return None
    notes: list[str] = []
    make = has_make_build(root)
    cmake = "CMakeLists.txt" in files
    build_system: str | None = "cmake" if cmake else "make" if make else None
    if build_system is None:
        notes.append(
            "There is no Makefile, configure script or CMakeLists.txt at the repository root; "
            "CodeCoreMorph builds C projects with Make or CMake."
        )
    elif cmake and make:
        notes.append(
            "The project builds with CMake and with Make; this manifest uses CMake. Set "
            "migration.source.build_system to make for the Make build."
        )
    if build_system == "make":
        notes.append("Make builds run in Docker for now (sandbox.mode: docker, D-68).")

    build_texts = [
        (p, t)
        for p in files
        if (is_make_file(p) or p.endswith(("CMakeLists.txt", ".cmake"))) and not outside.matches(p)
        if (t := _read(root, p)) is not None
    ]
    set_by_build = sorted(
        {normalized(s) for s in standards_in([t for _, t in build_texts], cxx=False)}
        | {f"c{m}" for _, t in build_texts for m in CMAKE_C_STANDARD.findall(masked_comments(t))},
        key=lambda s: C_STANDARDS.index(s) if s in C_STANDARDS else len(C_STANDARDS),
    )
    known = [s for s in set_by_build if s in C_STANDARDS]
    if known:
        standard, note, guessed = known[0], "set in the build files", False
    else:
        texts = [(p, t) for p in code[:_MAX_SCAN_FILES] if (t := _read(root, p)) is not None]
        standard, evidence = guess_standard(texts)
        guessed = True
        uses = ", ".join(evidence[:3])
        note = (
            f"guessed: the build files set no standard, and the code uses {uses}"
            if evidence
            else "guessed: the build files set no standard, and the code fits C89"
        )
        notes.append(
            f"migration.source.standard is a guess ({standard}): check it against the "
            "compiler flags the project is built with."
        )

    test_dirs, test_patterns = test_layout(code)
    tests = PathMatcher([*test_dirs, *test_patterns])
    sources = [f for f in code if not tests.matches(f)]
    source_dirs = (
        ["."]
        if any("/" not in f for f in sources)
        else sorted({f.split("/", 1)[0] for f in sources})
    )
    public_api = ["include/**"] if any(f.startswith("include/") for f in code) else []
    if not test_dirs and not test_patterns:
        notes.append(
            "No test sources were found. A Make build's tests run through `make check` (or "
            "`make test`); set build.make.test_targets if they run otherwise."
        )

    name = None
    for path, text in build_texts:
        pattern = _AC_INIT if path.endswith(("configure.ac", "configure.in")) else _PROJECT_NAME
        if path in ("CMakeLists.txt", "configure.ac", "configure.in") and (
            match := pattern.search(masked_comments(text))
        ):
            name = match.group(1)
            break

    return ProjectDetection(
        language="c",
        build_system=build_system,
        name=name,
        standard=standard,
        standard_note=note,
        standard_guessed=guessed,
        target_standard=C_STANDARDS[-1],
        source_dirs=source_dirs,
        test_dirs=[*test_dirs, *test_patterns],
        public_api=public_api,
        exclude=[f"{d}/**" for d in vendored],
        configure_args=[],
        notes=notes,
        code_files=len(code),
    )
