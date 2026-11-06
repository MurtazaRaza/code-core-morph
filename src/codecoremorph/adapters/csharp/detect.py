"""What ``codecoremorph init`` reads from a C# project (FR-1.4): the MSBuild projects, the C#
standard they set, the test frameworks and the layout. It reads the files git tracks and runs
nothing.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from codecoremorph.adapters.csharp.msbuild import (
    SOLUTION_SUFFIXES,
    STANDARDS,
    inherited_properties,
    is_project,
    project_info,
    standard_index,
)
from codecoremorph.core.models import ProjectDetection
from codecoremorph.patching.textfile import BinaryFileError, TextFile

SUGGESTED_TARGET = "csharp14"
_VENDORED = frozenset(
    {"third_party", "thirdparty", "third-party", "vendor", "external", "packages"}
)
_FRAMEWORKS = (
    (re.compile(r"Include\s*=\s*\"NUnit\"", re.I), "NUnit"),
    (re.compile(r"Include\s*=\s*\"xunit(?:\.v3)?\"", re.I), "xUnit"),
    (
        re.compile(r"Include\s*=\s*\"MSTest(?:\.TestFramework)?\"|Sdk\s*=\s*\"MSTest\.Sdk", re.I),
        "MSTest",
    ),
)


def _text(root: Path, rel: str) -> str | None:
    try:
        return TextFile.decode((root / rel).read_bytes()).text
    except (OSError, BinaryFileError):
        return None


def _vendored(path: str) -> bool:
    return any(part.lower() in _VENDORED for part in PurePosixPath(path).parts[:-1])


def detect(root: Path, files: list[str]) -> ProjectDetection | None:
    code = [f for f in files if f.lower().endswith(".cs") and not _vendored(f)]
    projects = [f for f in files if is_project(PurePosixPath(f).name) and not _vendored(f)]
    if not code and not projects:
        return None
    notes: list[str] = []
    if not projects:
        notes.append(
            "No .csproj: CodeCoreMorph builds C# with MSBuild projects (SDK-style or older)."
        )
    infos = [
        (p, project_info(t, inherited_properties(root, p)))
        for p in projects
        if (t := _text(root, p)) is not None
    ]
    main = [i.standard for _, i in infos if not i.test_project and i.standard]
    every = [i.standard for _, i in infos if i.standard]
    found = main or every
    standard = min(found, key=standard_index) if found else None
    guessed = standard is None
    frameworks_seen = sorted({t for _, i in infos for t in i.frameworks})
    if standard is None:
        standard = STANDARDS[0]
        note = "no project names a target framework or language version this adapter knows"
    else:
        langs = sorted({i.lang_version for _, i in infos if i.lang_version})
        note = "the projects target " + (", ".join(frameworks_seen) or "no framework")
        if langs:
            note += "; LangVersion " + ", ".join(langs)
    old_style = [p for p, i in infos if not i.sdk_style]
    if old_style:
        notes.append(
            f"{len(old_style)} project(s) are not SDK-style ({old_style[0]}); they build with "
            "the .NET SDK only if their references resolve without Visual Studio."
        )
    if any(
        t.lower().startswith("net4") or re.fullmatch(r"net[1-4]\d+", t.lower())
        for t in frameworks_seen
    ):
        notes.append(
            "Projects target the .NET Framework, whose tests run on Windows only; the bump "
            "leaves the framework and raises the language version."
        )
    solutions = [f for f in files if f.lower().endswith(SOLUTION_SUFFIXES) and "/" not in f]
    if len(solutions) > 1:
        notes.append(
            f"{len(solutions)} solutions at the top: CodeCoreMorph builds a solution of its own "
            "with every project."
        )
    texts = [t for p in projects if (t := _text(root, p)) is not None]
    tests = [name for pattern, name in _FRAMEWORKS if any(pattern.search(t) for t in texts)]
    if tests:
        notes.append("Tests: " + ", ".join(tests) + ", run by dotnet test.")
    else:
        notes.append(
            "No test framework found in the projects: the run's parity rests on its tests."
        )
    test_dirs = sorted(
        {
            PurePosixPath(p).parent.as_posix() + "/**"
            for p, i in infos
            if i.test_project and PurePosixPath(p).parent.as_posix() != "."
        }
    )
    target = SUGGESTED_TARGET
    name = PurePosixPath(solutions[0]).stem if len(solutions) == 1 else None
    if name is None and len(projects) == 1:
        name = PurePosixPath(projects[0]).stem
    return ProjectDetection(
        language="csharp",
        build_system="dotnet" if projects else None,
        name=name,
        standard=standard,
        standard_note=note,
        standard_guessed=guessed,
        target_standard=target,
        source_dirs=["."],
        test_dirs=test_dirs or ["**/*Tests", "**/tests"],
        exclude=sorted({f"{p}/**" for f in files if _vendored(f) for p in _vendored_root(f)}),
        notes=notes,
        code_files=len(code),
    )


def _vendored_root(path: str) -> list[str]:
    parts = PurePosixPath(path).parts
    for i, part in enumerate(parts[:-1]):
        if part.lower() in _VENDORED:
            return ["/".join(parts[: i + 1])]
    return []
