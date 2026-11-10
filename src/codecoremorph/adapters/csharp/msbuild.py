"""MSBuild project files as text: the C# standards they set, raising them
(``msbuild-lang-version``), the sources each project compiles, and the code-running constructs
the restore step guards against (SPEC §11.3, D-65).

Edits are made on the text, never by rewriting the XML, so a project keeps its layout,
comments and line endings. Comments are blanked out before anything is read.
"""

from __future__ import annotations

import os
import posixpath
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from codecoremorph.core.globs import PathMatcher, glob_to_regex
from codecoremorph.patching.textfile import BinaryFileError, TextFile

STANDARDS = (
    "csharp7.3",
    "csharp8",
    "csharp9",
    "csharp10",
    "csharp11",
    "csharp12",
    "csharp13",
    "csharp14",
)
# The .NET release each standard goes with: the one whose default language version it is.
STANDARD_DOTNET = {
    "csharp8": 3,
    "csharp9": 5,
    "csharp10": 6,
    "csharp11": 7,
    "csharp12": 8,
    "csharp13": 9,
    "csharp14": 10,
}
PROJECT_SUFFIXES = (".csproj",)
SOLUTION_SUFFIXES = (".sln", ".slnx")
PROPS_FILES = ("Directory.Build.props", "Directory.Build.targets", "Directory.Packages.props")
# Files outside the projects that MSBuild, NuGet or the SDK read: build files of the index, and
# what the restore step's fingerprint covers.
SETTINGS_FILES = ("global.json", "nuget.config", "Directory.Build.rsp")
OUTPUT_DIRS = ("bin", "obj")

_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_LANG_VERSION = re.compile(r"(<LangVersion>)\s*([^<]*?)\s*(</LangVersion>)", re.IGNORECASE)
_TFM_ELEMENT = re.compile(
    r"(<(TargetFrameworks?)>)\s*([^<]*?)\s*(</\2>)",
    re.IGNORECASE,
)
_TFM = re.compile(
    r"^(?:(?P<core>netcoreapp)(?P<cmaj>\d+)\.(?P<cmin>\d+)"
    r"|net(?P<nmaj>\d+)\.(?P<nmin>\d+)(?P<os>-[\w.]+)?"
    r"|(?P<std>netstandard)(?P<smaj>\d+)\.(?P<smin>\d+)"
    r"|(?P<fx>net)(?P<fx_digits>[1-4]\d{1,2}))$",
    re.IGNORECASE,
)
_SDK_PROJECT = re.compile(r"<Project\b[^>]*\bSdk\s*=|<Sdk\s+Name\s*=|<Import\b[^>]*\bSdk\s*=", re.I)
_ENABLE_DEFAULT = re.compile(
    r"<Enable(?:Default)?(?:Compile)?Items>\s*false\s*</Enable(?:Default)?(?:Compile)?Items>",
    re.IGNORECASE,
)
_COMPILE = re.compile(
    r"<Compile\s+(?P<attr>Include|Remove)\s*=\s*\"(?P<value>[^\"]*)\"", re.IGNORECASE
)
_PROJECT_REFERENCE = re.compile(
    r"<ProjectReference\s+Include\s*=\s*\"(?P<value>[^\"]*)\"", re.IGNORECASE
)
# A shared project's items, which the projects that import it compile (D-82).
_SHARED_IMPORT = re.compile(
    r"<Import\s+Project\s*=\s*\"(?P<value>[^\"]*\.projitems)\"", re.IGNORECASE
)
_THIS_DIRECTORY = re.compile(r"\$\(MSBuildThisFileDirectory\)", re.IGNORECASE)
_PROPERTY = re.compile(
    r"<(?P<name>[A-Za-z_][\w.-]*)(?:\s[^>]*)?>\s*(?P<value>[^<]*?)\s*</(?P=name)>"
)
_PROPERTY_REF = re.compile(r"\$\((?P<name>[A-Za-z_][\w.-]*)\)")
_SOLUTION_PROJECT = re.compile(r'^Project\("\{[^}]+\}"\)\s*=\s*"[^"]*",\s*"(?P<path>[^"]+)"', re.M)
_SLNX_PROJECT = re.compile(r"<Project\s+Path\s*=\s*\"(?P<path>[^\"]+)\"", re.IGNORECASE)
_TEST_SDK = re.compile(
    r"<PackageReference\s+Include\s*=\s*\"(?:Microsoft\.NET\.Test\.Sdk|MSTest\.Sdk)\"|"
    r"<IsTestProject>\s*true\s*</IsTestProject>|Sdk\s*=\s*\"MSTest\.Sdk",
    re.IGNORECASE,
)
_SKIPPED_DIRS = frozenset({".git", ".codecoremorph", ".vs", ".idea", "node_modules"})

# MSBuild constructs that run code or reach the network while MSBuild evaluates the projects or
# runs restore (D-65): the restore step refuses ones the run's first configure didn't see, and
# I-12 refuses new ones in an attempt's edits (invariants/checks.py has its own copy).
CODE_CONSTRUCTS: dict[str, re.Pattern[str]] = {
    "an Exec task": re.compile(r"<Exec\b[^>]*>", re.IGNORECASE),
    "an inline task (UsingTask)": re.compile(r"<UsingTask\b[^>]*>", re.IGNORECASE),
    "a DownloadFile task": re.compile(r"<DownloadFile\b[^>]*>", re.IGNORECASE),
    "a target": re.compile(r"<Target\b[^>]*>", re.IGNORECASE),
    "an import": re.compile(r"<Import\b[^>]*>", re.IGNORECASE),
    "an MSBuild SDK": re.compile(
        r"<Sdk\b[^>]*>|\bSdk\s*=\s*\"(?!Microsoft\.NET\.Sdk(?:\.\w+)*\")[^\"]*\"", re.IGNORECASE
    ),
    "an analyzer": re.compile(r"<Analyzer\b[^>]*>", re.IGNORECASE),
    "a restore source": re.compile(
        r"<Restore(?:Additional)?(?:Project)?Sources>[^<]*</Restore(?:Additional)?(?:Project)?"
        r"Sources>",
        re.IGNORECASE,
    ),
}
_NUGET_SOURCE = re.compile(r"<add\s+key\s*=\s*\"[^\"]*\"\s+value\s*=\s*\"[^\"]*\"[^>]*>", re.I)


def standard_index(standard: str) -> int:
    return STANDARDS.index(standard)


def standard_text(standard: str) -> str:
    """How a project writes a standard's language version: ``7.3``, ``12.0``."""
    number = standard.removeprefix("csharp")
    return number if "." in number else f"{number}.0"


def uncommented(text: str) -> str:
    """``text`` with XML comments blanked out (same length, line breaks kept)."""
    return _COMMENT.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


def standard_for_lang_version(value: str) -> str | None:
    """The standard a ``LangVersion`` names: numbers (``7.3``, ``12``, ``12.0``; older ones
    build as ``csharp7.3``). None for a keyword (``latest``, ``preview``, ``default``), which
    goes by the target framework."""
    text = value.strip().lower()
    if text in ("iso-1", "iso-2"):
        return STANDARDS[0]
    match = re.fullmatch(r"(\d+)(?:\.(\d+))?", text)
    if match is None:
        return None
    major, minor = int(match.group(1)), int(match.group(2) or 0)
    if (major, minor) <= (7, 3):
        return STANDARDS[0]
    for standard in STANDARDS[1:]:
        if int(standard.removeprefix("csharp")) >= major:
            return standard
    return STANDARDS[-1]


def framework_standard(tfm: str) -> str | None:
    """The default language version of a target framework: ``net6.0`` is ``csharp10``,
    ``netstandard2.0`` and the .NET Framework ``csharp7.3``. None for a name it doesn't know."""
    match = _TFM.match(tfm.strip())
    if match is None:
        return None
    if match.group("core"):
        return "csharp8" if int(match.group("cmaj")) >= 3 else STANDARDS[0]
    if match.group("std"):
        major, minor = int(match.group("smaj")), int(match.group("smin"))
        return "csharp8" if (major, minor) >= (2, 1) else STANDARDS[0]
    if match.group("fx"):
        return STANDARDS[0]
    major = int(match.group("nmaj"))
    if major < 5:  # net4.8 is no TFM; treat a stray one as the .NET Framework
        return STANDARDS[0]
    for standard, dotnet in STANDARD_DOTNET.items():
        if dotnet >= major:
            return standard
    return STANDARDS[-1]


def _dotnet_major(tfm: str) -> int | None:
    """The .NET (Core) major of ``netcoreapp3.1`` or ``net8.0``; None for others."""
    match = _TFM.match(tfm.strip())
    if match is None:
        return None
    if match.group("core"):
        return int(match.group("cmaj"))
    if match.group("nmaj") is not None and int(match.group("nmaj")) >= 5:
        return int(match.group("nmaj"))
    return None


def _lowest(standards: Iterable[str]) -> str | None:
    found = [s for s in standards if s in STANDARDS]
    return min(found, key=standard_index) if found else None


def properties(text: str) -> dict[str, str]:
    """Every simple property the file sets (the last wins, as in MSBuild), conditions aside."""
    found: dict[str, str] = {}
    for block in re.finditer(r"<PropertyGroup\b[^>]*>(.*?)</PropertyGroup>", text, re.S | re.I):
        for match in _PROPERTY.finditer(block.group(1)):
            found[match.group("name")] = match.group("value")
    return found


def _resolve(value: str, props: dict[str, str], depth: int = 0) -> str:
    if depth > 8:
        return value
    return _PROPERTY_REF.sub(
        lambda m: _resolve(props.get(m.group("name"), m.group(0)), props, depth + 1), value
    )


@dataclass(frozen=True)
class ProjectInfo:
    """What a project file says about the C# it builds."""

    frameworks: list[str] = field(default_factory=list[str])
    lang_version: str | None = None
    sdk_style: bool = True
    test_project: bool = False
    executable: bool = False  # OutputType Exe or WinExe: a program
    # CheckForOverflowUnderflow: integer arithmetic outside `unchecked` throws on overflow,
    # which a port keeps (D-82).
    checked_arithmetic: bool = False

    @property
    def standard(self) -> str | None:
        """The project's standard: its numeric ``LangVersion``, else its frameworks' lowest
        default."""
        if self.lang_version is not None:
            named = standard_for_lang_version(self.lang_version)
            if named is not None:
                return named
        return _lowest(s for t in self.frameworks if (s := framework_standard(t)))


def project_info(text: str, inherited: dict[str, str] | None = None) -> ProjectInfo:
    """``text``'s frameworks and language version, with the properties of the
    ``Directory.Build.props`` above it (``inherited``) where the project sets none."""
    clean = uncommented(text)
    props = {**(inherited or {}), **properties(clean)}
    frameworks_text = props.get("TargetFrameworks") or props.get("TargetFramework") or ""
    frameworks = [t.strip() for t in _resolve(frameworks_text, props).split(";") if t.strip()]
    lang = props.get("LangVersion")
    output = _resolve(props.get("OutputType") or "", props).strip().lower()
    return ProjectInfo(
        frameworks=[t for t in frameworks if "$(" not in t],
        lang_version=_resolve(lang, props) if lang else None,
        sdk_style=_SDK_PROJECT.search(clean) is not None,
        test_project=_TEST_SDK.search(clean) is not None,
        executable=output in ("exe", "winexe"),
        checked_arithmetic=_resolve(props.get("CheckForOverflowUnderflow") or "", props)
        .strip()
        .lower()
        == "true",
    )


@dataclass(frozen=True)
class LevelEdit:
    text: str
    replacements: int


def bump_project(text: str, target: str, inherited_lang: str | None = None) -> LevelEdit:
    """``text`` raised to ``target`` (``msbuild-lang-version``): ``net``/``netcoreapp``
    frameworks older than the target's .NET become it (an OS suffix kept), and ``LangVersion``
    becomes the target's number where it is a lower number, or is added beside the framework
    where the frameworks' default is older (``netstandard2.0`` keeps its framework) and the
    ``Directory.Build.props`` above sets no ``LangVersion`` as new (``inherited_lang``). A
    keyword ``LangVersion`` (``latest``) is left alone."""
    clean = uncommented(text)
    dotnet = STANDARD_DOTNET.get(target)
    replacements = 0
    pieces: list[str] = []
    position = 0
    new_frameworks: list[str] = []
    framework_end: int | None = None
    for match in _TFM_ELEMENT.finditer(clean):
        frameworks = [t.strip() for t in match.group(3).split(";") if t.strip()]
        changed: list[str] = []
        for tfm in frameworks:
            major = _dotnet_major(tfm)
            if dotnet is not None and major is not None and major < dotnet:
                suffix = _TFM.match(tfm)
                os_part = suffix.group("os") if suffix and suffix.group("os") else ""
                tfm = f"net{dotnet}.0{os_part}"
            if tfm not in changed:
                changed.append(tfm)
        new_frameworks += changed
        if framework_end is None:
            framework_end = match.end()
        value = ";".join(changed)
        if value != match.group(3):
            pieces.append(text[position : match.start(3)])
            pieces.append(value)
            position = match.end(3)
            replacements += 1
    pieces.append(text[position:])
    text = "".join(pieces)
    clean = uncommented(text)

    lang = _LANG_VERSION.search(clean)
    target_text = standard_text(target)
    if lang is not None:
        named = standard_for_lang_version(lang.group(2))
        if named is not None and standard_index(named) < standard_index(target):
            text = text[: lang.start(2)] + target_text + text[lang.end(2) :]
            replacements += 1
        return LevelEdit(text, replacements)
    defaults = [s for t in new_frameworks if (s := framework_standard(t))]
    lowest = _lowest(defaults)
    inherited = standard_for_lang_version(inherited_lang) if inherited_lang else None
    if inherited is not None and standard_index(inherited) >= standard_index(target):
        lowest = None  # the props above already set the target's version
    if (
        framework_end is not None
        and lowest is not None
        and standard_index(lowest) < standard_index(target)
    ):
        clean = uncommented(text)
        element = next(iter(_TFM_ELEMENT.finditer(clean)))
        line_start = text.rfind("\n", 0, element.start()) + 1
        indent = re.match(r"[ \t]*", text[line_start:]).group(0)  # type: ignore[union-attr]
        newline = "\r\n" if "\r\n" in text else "\n"
        insert = f"{newline}{indent}<LangVersion>{target_text}</LangVersion>"
        text = text[: element.end()] + insert + text[element.end() :]
        replacements += 1
    return LevelEdit(text, replacements)


def project_references(text: str) -> list[str]:
    """The ``ProjectReference`` paths, as written (backslashes made slashes)."""
    return [
        m.group("value").replace("\\", "/") for m in _PROJECT_REFERENCE.finditer(uncommented(text))
    ]


def solution_projects(text: str, *, slnx: bool) -> list[str]:
    """The project paths a solution lists (C# projects only), as written."""
    pattern = _SLNX_PROJECT if slnx else _SOLUTION_PROJECT
    return [
        m.group("path").replace("\\", "/")
        for m in pattern.finditer(text)
        if m.group("path").lower().endswith(PROJECT_SUFFIXES)
    ]


def code_constructs(rel: str, text: str) -> list[str]:
    """The code-running constructs and package sources in an MSBuild, NuGet or response file,
    each as it reads (the restore step's guard compares them by text)."""
    name = PurePosixPath(rel).name.lower()
    clean = uncommented(text)
    found: list[str] = []
    if name == "nuget.config":
        found += [
            f"a package source in {rel}: {' '.join(m.group(0).split())}"
            for m in _NUGET_SOURCE.finditer(clean)
        ]
    elif name.endswith(".rsp"):
        if clean.strip():
            found.append(f"MSBuild switches in {rel}: {' '.join(clean.split())[:200]}")
    elif name == "global.json":
        if re.search(r"\"msbuild-sdks\"", clean):
            found.append(f"MSBuild SDKs in {rel}")
    else:
        for what, pattern in CODE_CONSTRUCTS.items():
            found += [
                f"{what} in {rel}: {' '.join(m.group(0).split())[:200]}"
                for m in pattern.finditer(clean)
            ]
    return found


# ---------------------------------------------------------------------------- the layout


def _text(path: Path) -> str | None:
    try:
        return TextFile.decode(path.read_bytes()).text
    except (OSError, BinaryFileError):
        return None


def is_project(name: str) -> bool:
    return name.lower().endswith(PROJECT_SUFFIXES)


@dataclass
class Layout:
    """The worktree's MSBuild picture: project files, the files they compile, the C# files no
    project compiles, and the other build files (solutions, ``Directory.Build.*``,
    ``global.json``, ``nuget.config``)."""

    projects: list[str] = field(default_factory=list[str])
    sources: dict[str, list[str]] = field(default_factory=dict[str, list[str]])  # project -> files
    uncompiled: list[str] = field(default_factory=list[str])
    build_files: list[str] = field(default_factory=list[str])
    solutions: list[str] = field(default_factory=list[str])
    references: dict[str, list[str]] = field(default_factory=dict[str, list[str]])

    @property
    def compiled(self) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for files in self.sources.values():
            for f in files:
                if f not in seen:
                    seen.add(f)
                    out.append(f)
        return sorted(out)


def _walk(worktree: Path, exclude: PathMatcher) -> tuple[list[str], list[str]]:
    """``(files, cs)``: every file kept (outputs beside a project, excluded paths and VCS
    directories left out), and the C# files among them."""
    files: list[str] = []
    for current, dirs, names in os.walk(worktree):
        here = Path(current)
        rel_dir = here.relative_to(worktree).as_posix()
        is_project_dir = any(is_project(n) for n in names)
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in _SKIPPED_DIRS
            and not (is_project_dir and d.lower() in OUTPUT_DIRS)
            and not exclude.matches(d if rel_dir == "." else f"{rel_dir}/{d}")
        )
        for name in sorted(names):
            rel = name if rel_dir == "." else f"{rel_dir}/{name}"
            if not exclude.matches(rel):
                files.append(rel)
    return files, [f for f in files if f.lower().endswith(".cs")]


def _join(base: str, value: str) -> str:
    joined = posixpath.normpath(posixpath.join(base, value.replace("\\", "/")))
    return "" if joined == "." else joined


def _glob_matcher(base: str, patterns: list[str]) -> re.Pattern[str] | None:
    regexes: list[str] = []
    for pattern in patterns:
        for piece in pattern.split(";"):
            piece = piece.strip()
            if not piece or "$(" in piece or "@(" in piece:
                continue
            joined = _join(base, piece)
            if joined.startswith("../"):
                continue
            regexes.append(glob_to_regex("/" + joined).pattern)  # anchored at the worktree
    return re.compile("|".join(f"(?:{r})" for r in regexes)) if regexes else None


def inherited_properties(worktree: Path, project: str) -> dict[str, str]:
    """The properties of the ``Directory.Build.props`` MSBuild imports for ``project``: the
    nearest one above it."""
    directory = PurePosixPath(project).parent
    while True:
        candidate = worktree / directory / "Directory.Build.props"
        if candidate.is_file():
            return properties(uncommented(_text(candidate) or ""))
        if str(directory) in ("", "."):
            return {}
        directory = directory.parent


def layout(worktree: Path, exclude: PathMatcher) -> Layout:
    files, cs = _walk(worktree, exclude)
    result = Layout()
    for rel in files:
        name = PurePosixPath(rel).name
        if is_project(name):
            result.projects.append(rel)
        elif name.lower().endswith(SOLUTION_SUFFIXES):
            result.solutions.append(rel)
            result.build_files.append(rel)
        elif name in PROPS_FILES or name.lower() in (s.lower() for s in SETTINGS_FILES):
            result.build_files.append(rel)
    for project in result.projects:
        text = _text(worktree / project) or ""
        clean = uncommented(text)
        base = PurePosixPath(project).parent.as_posix()
        base = "" if base == "." else base
        info = project_info(text, inherited_properties(worktree, project))
        includes = [
            m.group("value")
            for m in _COMPILE.finditer(clean)
            if m.group("attr").lower() == "include"
        ]
        removes = [
            m.group("value")
            for m in _COMPILE.finditer(clean)
            if m.group("attr").lower() == "remove"
        ]
        defaults = info.sdk_style and _ENABLE_DEFAULT.search(clean) is None
        prefix = f"{base}/" if base else ""
        chosen: list[str] = []
        if defaults:
            outputs = (f"{prefix}bin/", f"{prefix}obj/")
            chosen = [f for f in cs if f.startswith(prefix) and not f.startswith(outputs)]
        included = _glob_matcher(base, includes)
        if included is not None:
            chosen += [f for f in cs if included.fullmatch(f) and f not in chosen]
        chosen += [f for f in shared_items(worktree, base, clean, cs) if f not in chosen]
        removed = _glob_matcher(base, removes)
        if removed is not None:
            chosen = [f for f in chosen if not removed.fullmatch(f)]
        result.sources[project] = sorted(chosen)
        result.references[project] = [
            j for r in project_references(text) if not (j := _join(base, r)).startswith("../")
        ]
    compiled = set(result.compiled)
    result.uncompiled = [f for f in cs if f not in compiled]
    return result


def shared_items(worktree: Path, base: str, project_text: str, cs: Iterable[str]) -> list[str]:
    """The C# files (of ``cs``) of the shared projects (``.projitems``) a project imports,
    which it compiles as its own: ``$(MSBuildThisFileDirectory)`` is the shared project's
    directory."""
    found: list[str] = []
    cs = list(cs)
    for match in _SHARED_IMPORT.finditer(project_text):
        items = _join(base, match.group("value"))
        if items.startswith("../"):
            continue
        text = uncommented(_text(worktree / items) or "")
        directory = PurePosixPath(items).parent.as_posix()
        directory = "" if directory == "." else directory
        includes = [
            _THIS_DIRECTORY.sub("", m.group("value"))
            for m in _COMPILE.finditer(text)
            if m.group("attr").lower() == "include"
        ]
        matcher = _glob_matcher(directory, includes)
        if matcher is None:
            continue
        found += [path for path in cs if matcher.fullmatch(path) and path not in found]
    return sorted(found)


def entry_point(worktree: Path, lay: Layout) -> str | None:
    """The solution or project ``dotnet`` builds: the top-level solution, else the top-level
    project; None when there is neither (or more than one of a kind), and the adapter writes a
    solution of its own."""
    top_solutions = [s for s in lay.solutions if "/" not in s]
    if len(top_solutions) == 1:
        return top_solutions[0]
    if len(top_solutions) > 1:
        stems = {PurePosixPath(s).stem for s in top_solutions}
        if len(stems) == 1:  # X.sln and X.slnx: the classic one
            return next(s for s in top_solutions if s.lower().endswith(".sln"))
        return None
    top_projects = [p for p in lay.projects if "/" not in p]
    if len(top_projects) == 1:
        return top_projects[0]
    return None


def lowest_standard(worktree: Path, lay: Layout) -> str | None:
    """The projects' lowest standard; test projects count only when nothing else does."""
    infos = [
        (project_info(_text(worktree / p) or "", inherited_properties(worktree, p)), p)
        for p in lay.projects
    ]
    main = [i.standard for i, _ in infos if not i.test_project and i.standard]
    if main:
        return _lowest(main)
    return _lowest(i.standard for i, _ in infos if i.standard)
