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
    BuildTarget,
    DependencyGraph,
    FileEntry,
    FileRole,
    GenericUse,
    GraphFile,
    NativeLibraryProbe,
    PackageEntry,
    RuleSite,
    Symbol,
    SymbolKind,
    TestEntry,
    TestMethod,
    TypeEntry,
    Visibility,
)
from codecoremorph.patching.textfile import BinaryFileError, TextFile
from codecoremorph.planner.graph import ClusterGraph, condense
from codecoremorph.store.index import IndexStore, takes_variable_arguments

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
    targets: list[BuildTarget] = field(default_factory=list[BuildTarget])  # the build's (D-58)
    tests: list[TestEntry] = field(default_factory=list[TestEntry])  # the build's tests (D-60)
    # The includes alone, and for each file the files defining what it uses, with how many
    # distinct symbols: the calls between C translation units (D-68).
    includes: dict[str, set[str]] = field(default_factory=dict[str, set[str]])
    calls: dict[str, dict[str, int]] = field(default_factory=dict[str, dict[str, int]])
    # A C# source's types, the uses of its generics and its test methods (D-78).
    types: list[TypeEntry] = field(default_factory=list[TypeEntry])
    generic_uses: list[GenericUse] = field(default_factory=list[GenericUse])
    test_methods: list[TestMethod] = field(default_factory=list[TestMethod])
    # A C# port's source's (D-82): the packages its projects restored, and what the toolchain
    # has of the native libraries it calls.
    packages: list[PackageEntry] = field(default_factory=list[PackageEntry])
    native_probes: list[NativeLibraryProbe] = field(default_factory=list[NativeLibraryProbe])
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
            calls: dict[str, dict[str, int]] = {}
            for a, b, n in store.reference_counts():
                if a in files and b in files:
                    calls.setdefault(a, {})[b] = n
            boundaries = {p: b for p, b in store.boundary_files().items() if p in files}
            meta = store.meta()
            uncompiled = sorted(p for p in (meta.uncompiled if meta else []) if in_scope.matches(p))
            targets = store.build_targets()
            tests = store.test_entries()
            types = store.port_types()
            generic_uses = store.generic_uses()
            test_methods = store.test_methods()
            packages = store.packages()
            native_probes = store.native_probes()
        includes = {path: set(targets) for path, targets in edges.items()}
        partners = pair_headers(files, edges)
        for path, others in partners.items():
            edges[path].update(others)
        structure = condense(files, edges)
        closure = structure.closure()
        for source, used in sorted(calls.items()):
            for target in sorted(used):
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
            targets=targets,
            tests=tests,
            includes=includes,
            calls=calls,
            types=types,
            generic_uses=generic_uses,
            test_methods=test_methods,
            packages=packages,
            native_probes=native_probes,
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

    def dependents(self, paths: Iterable[str]) -> list[str]:
        """The files outside ``paths`` that depend on one of them directly."""
        inside = set(paths)
        return sorted(
            source
            for source, targets in self.edges.items()
            if source not in inside and targets & inside
        )

    def chars(self, path: str) -> int:
        """The file's length in characters, for token estimates."""
        if path not in self._chars:
            try:
                self._chars[path] = len(TextFile.decode((self.worktree / path).read_bytes()).text)
            except (OSError, BinaryFileError):
                self._chars[path] = 0
        return self._chars[path]

    def lines(self, path: str) -> int:
        """The file's lines, for time estimates."""
        text = self.text(path)
        return 0 if text is None else len(text.splitlines())

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

    def headers_declaring(self, sites: Iterable[RuleSite]) -> dict[str, list[str]]:
        """For each site's file, the in-scope headers that declare the symbols enclosing its
        sites: the interface a port of that file keeps (D-56)."""
        by_file: dict[str, list[RuleSite]] = {}
        for site in sites:
            by_file.setdefault(site.file, []).append(site)
        found: dict[str, list[str]] = {}
        with IndexStore.open(self.index_path) as store:
            for path, file_sites in sorted(by_file.items()):
                ids = self.symbols_at(file_sites)
                headers = {
                    f
                    for f in store.files_declaring(ids)
                    if f != path and f in self.files and self.files[f].role is FileRole.HEADER
                }
                found[path] = sorted(headers)
        return found

    def c_exports(self, path: str) -> list[Symbol]:
        """The functions and variables with C linkage that ``path`` defines and another file
        declares or uses: what the port of a C translation unit exports (D-68), except what
        stays C (:meth:`c_variadic`)."""
        return [s for s in self._c_interface(path) if not takes_variable_arguments(s)]

    def c_variadic(self, path: str) -> list[Symbol]:
        """The functions of ``path``'s C interface that take ``...`` or a ``va_list``: stable
        Rust can define neither, so they stay in the C file, over the Rust code (D-70)."""
        return [s for s in self._c_interface(path) if takes_variable_arguments(s)]

    def _c_interface(self, path: str) -> list[Symbol]:
        with IndexStore.open(self.index_path) as store:
            return [
                s
                for s in store.used_elsewhere(path)
                if s.kind in (SymbolKind.FUNCTION, SymbolKind.VARIABLE)
                and s.boundary is Boundary.C_ABI
            ]

    def public_items(self, path: str) -> list[Symbol]:
        """The public items ``path`` defines, modules left out: what a port of a module from a
        language without headers keeps as its façade's API (a Rust module, D-75)."""
        with IndexStore.open(self.index_path) as store:
            return [
                s
                for s in store.symbols(file=path)
                if s.kind is not SymbolKind.NAMESPACE
                and s.visibility is Visibility.PUBLIC
                and s.is_definition
            ]

    def call_sites(self, symbol_id: str) -> list[tuple[str, int]]:
        """Where ``symbol_id`` is used, ``(file, line)``: a generic item's instantiations are
        read there, since the index records no type arguments (D-75)."""
        with IndexStore.open(self.index_path) as store:
            return sorted({(r.from_file, r.line) for r in store.references_to(symbol_id)})

    def symbol_references(self) -> list[tuple[str, str, str]]:
        """``(from_file, from_symbol, to_symbol)`` for each reference made inside a symbol of
        an in-scope file: the uses between a C# library's types (D-78)."""
        with IndexStore.open(self.index_path) as store:
            return [r for r in store.symbol_references() if r[0] in self.files]

    def definitions(self, symbol_ids: Iterable[str]) -> dict[str, Symbol]:
        """The definition of each of ``symbol_ids`` the index has, by ID: their visibility and
        signatures."""
        wanted = set(symbol_ids)
        if not wanted:
            return {}
        found: dict[str, Symbol] = {}
        with IndexStore.open(self.index_path) as store:
            for symbol in store.symbols():
                if symbol.id in wanted and symbol.is_definition:
                    found.setdefault(symbol.id, symbol)
        return found

    def files_declaring(self, symbol_ids: Iterable[str]) -> list[str]:
        """The files that declare or define one of ``symbol_ids``."""
        ids = list(symbol_ids)
        if not ids:
            return []
        with IndexStore.open(self.index_path) as store:
            return store.files_declaring(ids)

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
