"""The C# side of a port from C#, the same for every target (SPEC §11.9, §11.10, D-77, D-84):
names from C# projects and types, the files a façade project gets (the support file and the
generated declarations), the façade projects, and what the retirement deletes of the C#.

The bridge's C ABI (``codecoremorph_bridge.h``) and the C# support (``CodeCoreMorph.Interop.cs``)
ship here (``port_runtime``): a port to C++ (``adapters/cpp/csharpports.py``) and one to Rust
(``adapters/rust/csharpports.py``) write the same C#, so the façades the C# side gets don't
depend on the target.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Collection, Mapping, Sequence
from importlib import resources
from pathlib import Path, PurePosixPath

from codecoremorph.core.errors import ConfigError

SUPPORT_FILE = "CodeCoreMorph.Interop.cs"
# The C# declarations of the bridge, generated beside the support (adapters/cpp/pinvoke.py).
GENERATED_FILE = "CodeCoreMorph.Native.g.cs"
NAMESPACE = "CodeCoreMorph.Interop"
EXCEPTIONS_FILE = "CcmExceptions.cs"
BRIDGE_RUNTIME_HEADER = "codecoremorph_bridge.h"
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
# The files a façade project's C# keeps beside the façades while the port lasts.
FACADE_FILES = (SUPPORT_FILE, GENERATED_FILE, EXCEPTIONS_FILE)


def snake(text: str) -> str:
    """``OrderBook`` gives ``order_book``, ``HTTPClient`` ``http_client``; never a leading
    digit."""
    stem = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", text)
    stem = re.sub(r"[^A-Za-z0-9]+", "_", stem).strip("_").lower()
    if not stem or stem[0].isdigit():
        stem = f"cs_{stem}"
    return stem


def library_name(project: str) -> str:
    """The stem of the target's libraries and exports for a C# project: ``OrderBook`` and
    ``Order.Book`` give ``order_book``."""
    stem = PurePosixPath(project.replace("\\", "/")).name
    return snake(re.sub(r"\.csproj$", "", stem, flags=re.IGNORECASE))


def unit_name(type_name: str, taken: Collection[str], reserved: Collection[str] = ()) -> str:
    """The stem of a type unit's files, from the name of its first type: ``Zoo.OrderBook``
    gives ``order_book``, ``Zoo.Pen`1`` gives ``pen``. A stem the runtime's files have
    (``codecoremorph_bridge``) gets ``_type``, one in ``reserved`` (the target's keywords)
    ``_mod``, and one ``taken`` has (another unit's) a number."""
    stem = snake(type_name.rsplit(".", 1)[-1].split("`", 1)[0])
    if stem.startswith("codecoremorph"):
        stem = f"{stem}_type"
    if stem in reserved:
        stem = f"{stem}_mod"
    base, n = stem, 2
    while stem in taken:
        stem, n = f"{base}_{n}", n + 1
    return stem


def type_names(types: str) -> str:
    """A unit's ``types`` option (symbol IDs, comma-separated) as the stubs' comments name
    them: ``csharp:Zoo.Pen`1,csharp:Zoo.Pens`` gives ``Zoo.Pen, Zoo.Pens``."""
    names = [re.sub(r"`\d+", "", t.strip().removeprefix("csharp:")) for t in types.split(",")]
    return ", ".join(n for n in names if n)


def runtime_text(file_name: str) -> str:
    """A file of the C# side's runtime (the C ABI header, the support file), as CodeCoreMorph
    ships it."""
    package = resources.files("codecoremorph.adapters.csharp.port_runtime")
    return (package / file_name).read_text(encoding="utf-8")


def has_runtime_file(file_name: str) -> bool:
    package = resources.files("codecoremorph.adapters.csharp.port_runtime")
    return (package / file_name).is_file()


class Writer:
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

    def delete_tree(self, rel: str) -> None:
        """Delete every file under the directory ``rel``."""
        directory = self.worktree / rel
        if directory.is_dir():
            for path in sorted(p for p in directory.rglob("*") if p.is_file()):
                self.delete(path.relative_to(self.worktree).as_posix())

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


def identifier_option(options: Mapping[str, str], name: str, what: str, port: str) -> str:
    """A scaffold option that must be a C identifier in lower case; ``port`` names the port in
    the error (``C# → C++``)."""
    value = options.get(name, "")
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", value):
        raise ConfigError(
            f"the {port} scaffold needs {what} (its {name} option, a C identifier), got {value!r}",
            hint="The planner sets it from the unit's port; re-plan the run.",
        )
    return value


def facade_project(worktree: Path, options: Mapping[str, str], port: str) -> str:
    """The façade project's file (the scaffold's ``build_target`` option)."""
    project = options.get("build_target", "").replace("\\", "/")
    if not project.lower().endswith(".csproj") or not (worktree / project).is_file():
        raise ConfigError(
            f"the {port} scaffold needs the façade project's file, got {project!r}",
            hint="The planner sets it from the unit's types; re-plan the run.",
        )
    return project


def project_dir_prefix(project: str) -> str:
    """``src/Shop/Shop.csproj`` gives ``src/Shop/``; a project at the root gives ``""``."""
    folder = PurePosixPath(project).parent.as_posix()
    return "" if folder == "." else f"{folder}/"


def initial_declarations(library: str, headers: str = "cpp/bridge/*.h", target: str = "C++") -> str:
    """The generated file before the bridge exports anything: the library's name, which the
    support file needs to compile. ``headers`` and ``target`` say where its declarations will
    come from."""
    return (
        "// <auto-generated>\n"
        f"// Written by CodeCoreMorph from the bridge headers ({headers}) when the {target} is\n"
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


def write_facade_support(
    out: Writer, project: str, library: str, headers: str = "cpp/bridge/*.h", target: str = "C++"
) -> None:
    """The support file and the generated declarations beside a façade project's file, once."""
    prefix = project_dir_prefix(project)
    out.create(f"{prefix}{SUPPORT_FILE}", runtime_text(SUPPORT_FILE))
    out.create(f"{prefix}{GENERATED_FILE}", initial_declarations(library, headers, target))


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


def retire_csharp(out: Writer, files: Sequence[str], *, keep: bool) -> None:
    """The C# half of a retirement: the unit's C# files and projects, its solutions and MSBuild
    files, and the façades' support files. With ``keep`` (``keep_source_api``) the unit names
    only the programs and the tests that need them: the solutions drop those projects, the
    MSBuild files stay, and so does the support of the façade projects left."""
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
        for file_name in FACADE_FILES:
            out.delete(f"{prefix}{file_name}")
