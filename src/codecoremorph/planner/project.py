"""The project as the planner sees it: editable files, their dependency graph condensed into
clusters, and the symbol queries for fan-in and boundary flags (SPEC §7.2, §7.5.1 steps 1-2).

The file graph has an edge from a file to each file it depends on:

- its includes or imports (``FileDependency``);
- its header/source partner, both ways, so a pair always lands in one cluster;
- the files defining symbols it references (definitions only), unless the defining file already
  depends on it through the first two. Includes and pairs are the project's real structure; a
  reference against them (a header using a type it only forward-declares) would only fuse
  unrelated files into one large cycle.

A header pairs with a source of the same stem that includes it, or that sits in the same
directory (``include/geo/vector.h`` and ``src/vector.cpp``).
"""

from __future__ import annotations

import posixpath
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from codecoremorph.core.globs import PathMatcher
from codecoremorph.core.models import (
    Boundary,
    DependencyGraph,
    FileEntry,
    FileRole,
    GraphFile,
    RuleSite,
    Symbol,
    SymbolKind,
)
from codecoremorph.patching.textfile import BinaryFileError, TextFile
from codecoremorph.planner.graph import ClusterGraph, condense
from codecoremorph.store.index import IndexStore

CODE_ROLES = frozenset({FileRole.SOURCE, FileRole.HEADER, FileRole.TEST})


def _stem(path: str) -> str:
    name = posixpath.basename(path)
    return name.split(".", 1)[0].lower()


class _DirMatcher:
    """``project.source_dirs`` / ``test_dirs``: directories, where ``.`` means everything."""

    def __init__(self, dirs: Iterable[str]):
        dirs = list(dirs)
        self.everything = "." in dirs
        self.matcher = PathMatcher(d for d in dirs if d != ".")

    def matches(self, path: str) -> bool:
        return self.everything or self.matcher.matches(path)


def pair_headers(
    files: Mapping[str, FileEntry], dependencies: Mapping[str, set[str]]
) -> dict[str, list[str]]:
    """Header/source partners, both directions: ``{"src/a.cpp": ["include/a.h"], ...}``."""
    headers: dict[str, list[str]] = {}
    for path, entry in files.items():
        if entry.role is FileRole.HEADER:
            headers.setdefault(_stem(path), []).append(path)
    partners: dict[str, set[str]] = {}
    for path, entry in sorted(files.items()):
        if entry.role is FileRole.HEADER:
            continue
        directory = posixpath.dirname(path)
        for header in headers.get(_stem(path), []):
            if header in dependencies.get(path, set()) or posixpath.dirname(header) == directory:
                partners.setdefault(path, set()).add(header)
                partners.setdefault(header, set()).add(path)
    return {path: sorted(others) for path, others in sorted(partners.items())}


@dataclass
class ProjectModel:
    """Built once per plan from the symbol index; read-only afterwards."""

    worktree: Path
    index_path: Path
    files: dict[str, FileEntry]  # editable, in-scope code files
    build_files: list[str]
    edges: dict[str, set[str]]
    partners: dict[str, list[str]]
    graph: ClusterGraph
    boundaries: dict[str, set[Boundary]]
    uncompiled: list[str] = field(default_factory=list[str])  # in scope, never compiled
    _chars: dict[str, int] = field(default_factory=dict[str, int], repr=False)

    @classmethod
    def load(
        cls,
        index_path: Path,
        worktree: Path,
        *,
        source_dirs: Iterable[str] = (".",),
        test_dirs: Iterable[str] = (),
    ) -> ProjectModel:
        in_scope = _DirMatcher([*source_dirs, *test_dirs])
        with IndexStore.open(index_path) as store:
            entries = store.files()
            files = {
                e.path: e for e in entries if e.role in CODE_ROLES and in_scope.matches(e.path)
            }
            build_files = sorted(e.path for e in entries if e.role is FileRole.BUILD)
            edges: dict[str, set[str]] = {path: set() for path in files}
            for dep in store.dependencies():
                if dep.from_file in files and dep.to_file in files and dep.from_file != dep.to_file:
                    edges[dep.from_file].add(dep.to_file)
            references = [(a, b) for a, b in store.reference_edges() if a in files and b in files]
            boundaries = {p: b for p, b in store.boundary_files().items() if p in files}
            meta = store.meta()
            uncompiled = sorted(p for p in (meta.uncompiled if meta else []) if in_scope.matches(p))
        partners = pair_headers(files, edges)
        for path, others in partners.items():
            edges[path].update(others)
        structure = condense(files, edges)
        closure = structure.closure()
        for source, target in references:
            a, b = structure.cluster_of(source), structure.cluster_of(target)
            if a != b and not closure[b] >> a & 1:
                edges[source].add(target)
        return cls(
            worktree=worktree,
            index_path=index_path,
            files=files,
            build_files=build_files,
            edges=edges,
            partners=partners,
            graph=condense(files, edges),
            boundaries=boundaries,
            uncompiled=uncompiled,
        )

    @property
    def edge_count(self) -> int:
        return sum(len(targets) for targets in self.edges.values())

    def dependency_graph(self) -> DependencyGraph:
        """The file graph with each file's cluster, for ``graph.json`` (FR-12.3)."""
        return DependencyGraph(
            files=[GraphFile(path=p, cluster=self.graph.cluster_of(p)) for p in sorted(self.files)],
            edges=sorted((source, target) for source, ts in self.edges.items() for target in ts),
        )

    def chars(self, path: str) -> int:
        """The file's length in characters, for token estimates."""
        if path not in self._chars:
            try:
                self._chars[path] = len(TextFile.decode((self.worktree / path).read_bytes()).text)
            except (OSError, BinaryFileError):
                self._chars[path] = 0
        return self._chars[path]

    def text(self, path: str) -> str | None:
        try:
            return TextFile.decode((self.worktree / path).read_bytes()).text
        except (OSError, BinaryFileError):
            return None

    def cluster_index(self, path: str) -> int:
        return self.graph.cluster_of(path)

    # ------------------------------------------------------------------ symbols

    def symbols_at(self, sites: Iterable[RuleSite]) -> list[str]:
        """The innermost symbol enclosing each site (namespaces never count): the symbols an
        API-changing rule may change there."""
        by_file: dict[str, list[RuleSite]] = {}
        for site in sites:
            by_file.setdefault(site.file, []).append(site)
        found: set[str] = set()
        with IndexStore.open(self.index_path) as store:
            for path, file_sites in sorted(by_file.items()):
                symbols = [
                    s for s in store.symbols(file=path) if s.kind is not SymbolKind.NAMESPACE
                ]
                for site in file_sites:
                    innermost = _innermost(symbols, site.line, site.column)
                    if innermost is not None:
                        found.add(innermost.id)
        return sorted(found)

    def fan_in(self, symbol_ids: Iterable[str]) -> list[str]:
        """Every file that declares, defines or references one of ``symbol_ids``."""
        ids = list(symbol_ids)
        if not ids:
            return []
        with IndexStore.open(self.index_path) as store:
            return sorted({*store.files_declaring(ids), *store.files_referencing(ids)})


def _innermost(symbols: list[Symbol], line: int, column: int) -> Symbol | None:
    best: Symbol | None = None
    best_size: tuple[int, int] | None = None
    for symbol in symbols:
        r = symbol.range
        if (r.start_line, r.start_column) <= (line, column) <= (r.end_line, r.end_column):
            size = (r.end_line - r.start_line, r.end_column - r.start_column)
            if best_size is None or size < best_size:
                best, best_size = symbol, size
    return best
