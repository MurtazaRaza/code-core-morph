"""Work-unit formation and ordering (FR-5.1 to FR-5.6, SPEC §7.5.1 steps 4-8).

Rules run in order: the standard bump first, then the manifest's rules. Each rule's units go
leaves first (dependencies before dependents), with ties broken by path, so the same inputs
always give the same plan, byte for byte (FR-5.6).

- ``project`` rules: one build-configuration unit, then the files with sites as fallout units.
  Files that fail to compile under the new standard join the fallout at run time.
- ``api_preserving`` rules: one unit per cluster, split by header/source pair when a cluster
  is too large, and merged with the next clusters while they stay small.
- ``api_changing`` rules: a cluster's files with sites plus the fan-in of the symbols enclosing
  those sites; a unit over the size limit is ``too_large`` and skipped (FR-5.3). A unit takes
  on the sites of the fan-in files no earlier unit covers, together with their header/source
  partners; fan-in files whose sites an earlier unit covers are its ``reference_files``
  (D-42).
- ``port`` rules: one unit per C interface (``port_units: c_header``, D-57), or one unit per
  module, leaves first, behind façades (``port_units: module``, D-58); then, to finish a port
  (D-60), one unit per executable (``executable``), one per test source (``tests``), each
  needing the module units of what it includes, and one last unit that retires the source
  language (``retirement``), needing every other port unit. A C port cuts one unit per
  source file of a library instead, ordered by calls (``translation_unit``, D-68).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Iterable, Sequence
from dataclasses import dataclass, field

from codecoremorph.core.globs import PathMatcher
from codecoremorph.core.ids import unit_id
from codecoremorph.core.models import (
    Boundary,
    FileEntry,
    FileRole,
    Plan,
    PlanEstimate,
    PlanGraph,
    PlanRule,
    PortUnit,
    PortUnits,
    RuleKind,
    RuleSite,
    SkipCondition,
    SymbolKind,
    TargetKind,
    UnitFlag,
    UnitStatus,
    WorkUnit,
)
from codecoremorph.llm.catalog import ModelInfo
from codecoremorph.planner.estimate import FALLBACK_CONTEXT_WINDOW, Estimator
from codecoremorph.planner.graph import condense
from codecoremorph.planner.project import ProjectModel
from codecoremorph.planner.sites import RuleScan

DEFAULT_MAX_UNIT_FILES = 20  # NFR-1.3 sizes incremental re-indexing for units of this size
DEFAULT_MERGE_TOKENS = 40_000  # small clusters merge up to this much context

_ABI_BOUNDARIES = frozenset({Boundary.C_ABI, Boundary.JNI, Boundary.PINVOKE})
_LANGUAGE_NAMES = {"cpp": "C++", "c": "C", "csharp": "C#", "java": "Java", "rust": "Rust"}
_SKIP_FLAGS = {
    SkipCondition.C_COMPATIBLE: UnitFlag.C_COMPATIBLE,
    SkipCondition.PUBLIC_API: UnitFlag.PUBLIC_API,
    SkipCondition.TEST: UnitFlag.TOUCHES_TESTS,
}
SKIP_REASONS = {
    SkipCondition.C_COMPATIBLE: "headers shared with C",
    SkipCondition.PUBLIC_API: "public API files",
    SkipCondition.TEST: "test files",
}


# A module port's names in the target language (D-58): (build target, the module's file stem,
# the module names its package already has) -> (package, module). The target adapter knows the
# language's rules (``LanguageAdapter.port_names``).
PortNamer = Callable[[str, str, Collection[str]], tuple[str, str]]


def plain_port_names(build_target: str, stem: str, taken: Collection[str]) -> tuple[str, str]:
    """Lowercase identifiers, for when no target adapter names them."""

    def ident(text: str) -> str:
        name = re.sub(r"[^a-z0-9_]+", "_", text.lower()).strip("_") or "module"
        return name if name[0].isalpha() else f"m_{name}"

    module = ident(stem)
    base, n = module, 2
    while module in taken:
        module, n = f"{base}_{n}", n + 1
    return ident(build_target), module


@dataclass(frozen=True)
class PlannerSettings:
    token_limit: int  # a unit's context may not exceed this (context window x 0.5)
    max_unit_files: int = DEFAULT_MAX_UNIT_FILES
    merge_tokens: int = DEFAULT_MERGE_TOKENS


@dataclass
class _Draft:
    rule: str
    files: list[str]
    sites: dict[str, int] = field(default_factory=dict[str, int])
    status: UnitStatus = UnitStatus.PENDING
    flags: set[UnitFlag] = field(default_factory=set[UnitFlag])
    note: str | None = None
    sized: bool = True  # build-configuration units are mechanical edits, never too large
    references: list[str] = field(default_factory=list[str])  # fan-in files, sites elsewhere
    port: PortUnit | None = None
    needs: list[_Draft] = field(default_factory=list["_Draft"])  # units that must commit first
    blocking: bool = True  # whether a port's retirement needs it (a skip-list unit doesn't)
    counted: list[str] | None = None  # the files its estimate counts, when not all of them


def skip_matches(condition: SkipCondition, entry: FileEntry) -> bool:
    if condition is SkipCondition.C_COMPATIBLE:
        return entry.c_compatible
    if condition is SkipCondition.PUBLIC_API:
        return entry.public_api
    return entry.role is FileRole.TEST


class _Former:
    def __init__(
        self,
        project: ProjectModel,
        settings: PlannerSettings,
        estimator: Estimator,
        names: PortNamer = plain_port_names,
        port_units: Collection[PortUnits] = (),
        keep_source_api: bool = False,
        mapped_tests: Collection[str] = (),
        new_code: Collection[str] = (),
        language: str = "cpp",
    ):
        self.project = project
        self.settings = settings
        self.estimator = estimator
        self.names = names
        # What the plan's port rules cut (D-60): a module port leaves executables and tests to
        # later rules when the plan has them.
        self.planned_ports = set(port_units)
        self.keep_source_api = keep_source_api
        self.mapped_tests = set(mapped_tests)  # the project's test map already has these
        # A port's new code (its rules' extra_scope: the crates, and the C++ glue headers of a
        # module port's bridge) is no source code to port (D-60).
        self.new_code = PathMatcher(list(new_code)) if new_code else None
        self.language = language  # the source's
        self.module_drafts: dict[str, _Draft] = {}  # file -> the module unit that ports it
        self.port_drafts: list[_Draft] = []

    def is_new_code(self, path: str) -> bool:
        return self.new_code is not None and self.new_code.matches(path)

    # ------------------------------------------------------------------ sizes

    def tokens(self, files: Sequence[str], rule: str) -> int:
        return self.estimator.context_tokens(sum(self.project.chars(f) for f in files), [rule])

    def fits(self, files: Sequence[str], rule: str, *, token_cap: int) -> bool:
        return len(files) <= self.settings.max_unit_files and self.tokens(files, rule) <= token_cap

    # ------------------------------------------------------------------ grouping

    def cluster_groups(self, files: Sequence[str]) -> list[list[str]]:
        """``files`` grouped by cluster, leaves first."""
        groups: dict[int, list[str]] = {}
        for path in files:
            groups.setdefault(self.project.cluster_index(path), []).append(path)
        return [sorted(groups[i]) for i in sorted(groups)]

    def pieces(self, group: list[str]) -> list[list[str]]:
        """A cluster's files in header/source pairs, the smallest pieces a split keeps whole."""
        members = set(group)
        seen: set[str] = set()
        pieces: list[list[str]] = []
        for path in group:
            if path in seen:
                continue
            piece = {path, *(p for p in self.project.partners.get(path, []) if p in members)}
            piece -= seen
            seen |= piece
            pieces.append(sorted(piece))
        return pieces

    def split(self, group: list[str], rule: str) -> list[list[str]]:
        """Split a cluster that is over the size limit; single files over it stay alone (and
        are flagged ``too_large`` later)."""
        limit = self.settings.token_limit
        if self.fits(group, rule, token_cap=limit):
            return [group]
        chunks: list[list[str]] = []
        current: list[str] = []
        for piece in self.pieces(group):
            parts = [piece] if self.fits(piece, rule, token_cap=limit) else [[f] for f in piece]
            for part in parts:
                if current and not self.fits([*current, *part], rule, token_cap=limit):
                    chunks.append(current)
                    current = []
                current = [*current, *part]
        if current:
            chunks.append(current)
        return chunks

    def merge(self, groups: list[list[str]], rule: str) -> list[list[str]]:
        cap = min(self.settings.merge_tokens, self.settings.token_limit)
        merged: list[list[str]] = []
        for group in groups:
            if merged and self.fits([*merged[-1], *group], rule, token_cap=cap):
                merged[-1] = [*merged[-1], *group]
            else:
                merged.append(list(group))
        return merged

    # ------------------------------------------------------------------ units

    def site_counts(self, scan: RuleScan, files: Sequence[str]) -> dict[str, int]:
        return {f: len(scan.sites[f]) for f in sorted(files) if f in scan.sites}

    def preserving_units(self, scan: RuleScan, files: list[str], note: str | None) -> list[_Draft]:
        rule = scan.rule_id
        groups = [chunk for g in self.cluster_groups(files) for chunk in self.split(g, rule)]
        return [
            _Draft(rule=rule, files=sorted(g), sites=self.site_counts(scan, g), note=note)
            for g in self.merge(groups, rule)
        ]

    def changing_units(self, scan: RuleScan, files: list[str], editable: set[str]) -> list[_Draft]:
        rule = scan.rule_id
        drafts: list[_Draft] = []
        # Sites in files an earlier unit already includes (through its fan-in) are that unit's
        # work, so they don't get a unit of their own; a later unit that reaches such a file
        # again has it for its references only.
        covered: set[str] = set()
        for cluster in self.cluster_groups(files):
            group = [f for f in cluster if f not in covered]
            if not group:
                continue
            sites: list[RuleSite] = [s for f in group for s in scan.sites.get(f, [])]
            reached = self.project.fan_in(self.project.symbols_at(sites))
            # The files whose sites this unit takes on: its own, and the fan-in's not yet
            # covered. Each comes with its header/source partner, where the members and
            # signatures its sites change are declared (db_impl.cc's deletes need db_impl.h).
            owned = {*group, *(f for f in reached if f in scan.sites and f not in covered)}
            owned &= editable
            owned |= {p for f in owned for p in self.project.partners.get(f, []) if p in editable}
            unit_files = sorted({*owned, *(f for f in reached if f in editable)})
            references = sorted(f for f in unit_files if f in scan.sites and f not in owned)
            outside = sorted(f for f in reached if f not in editable)
            draft = _Draft(
                rule=rule,
                files=unit_files,
                sites=self.site_counts(scan, sorted(owned)),
                references=references,
            )
            notes: list[str] = []
            if len(unit_files) > len(group):
                notes.append(f"fan-in adds {len(unit_files) - len(group)} files")
            if references:
                notes.append(
                    f"{len(references)} for their references only (their sites are earlier "
                    "units' work)"
                )
            if outside:
                more = f" (+{len(outside) - 3})" if len(outside) > 3 else ""
                shown = ", ".join(outside[:3]) + more
                notes.append(f"symbols are also used where the rule can't edit: {shown}")
                draft.flags.add(UnitFlag.BOUNDARY)
            if not self.fits(unit_files, rule, token_cap=self.settings.token_limit):
                draft.status = UnitStatus.TOO_LARGE
                notes.append(self.size_note(unit_files, rule))
            draft.note = "; ".join(notes) or None
            drafts.append(draft)
            if draft.status is UnitStatus.PENDING:
                covered.update(f for f in owned if f in scan.sites)
        return drafts

    def size_note(self, files: Sequence[str], rule: str) -> str:
        return (
            f"too large: {len(files)} files, about {self.tokens(files, rule)} tokens of context "
            f"(limits: {self.settings.max_unit_files} files, {self.settings.token_limit} tokens)"
        )

    def project_units(self, scan: RuleScan, files: list[str], target: str) -> list[_Draft]:
        drafts: list[_Draft] = []
        if self.project.build_files:
            note = (
                f"raises the standard to {target} in the build files; files that then fail to "
                "compile join the fallout units at run time"
                if scan.automatic
                else "build-configuration changes"
            )
            drafts.append(
                _Draft(
                    rule=scan.rule_id, files=list(self.project.build_files), note=note, sized=False
                )
            )
        fallout_note = (
            "predicted fallout: sites the target standard breaks" if scan.automatic else None
        )
        return drafts + self.preserving_units(scan, files, fallout_note)

    def port_units(self, scan: RuleScan, files: list[str], target: str) -> list[_Draft]:
        """One unit per interface (D-56): the files with sites that define what a set of
        headers declares, with those headers as read-only reference files and the build
        files. Files that share a header, or define what two headers declare, share a unit."""
        sites = [s for f in files for s in scan.sites.get(f, [])]
        headers = self.project.headers_declaring(sites)
        parent: dict[str, str] = {}

        def find(path: str) -> str:
            while parent.setdefault(path, path) != path:
                path = parent[path]
            return path

        for source in files:
            for header in headers.get(source, []):
                parent[find(header)] = find(source)
        groups: dict[str, list[str]] = {}
        for source in files:
            groups.setdefault(find(source), []).append(source)
        drafts: list[_Draft] = []
        for members in sorted(groups.values(), key=lambda m: (self.project.cluster_index(m[0]), m)):
            kept = sorted({h for s in members for h in headers.get(s, [])})
            draft = _Draft(
                rule=scan.rule_id,
                files=sorted({*members, *kept, *self.project.build_files}),
                sites=self.site_counts(scan, members),
                references=kept,
            )
            if kept:
                draft.note = f"ports {', '.join(sorted(members))} to {target} behind " + ", ".join(
                    kept
                )
            else:
                draft.status = UnitStatus.SKIPPED
                draft.note = "skipped: no project header declares what these files define"
            drafts.append(draft)
        return drafts

    def module_units(self, scan: RuleScan, files: list[str], target: str) -> list[_Draft]:
        """One unit per module (D-58): a cluster of the file graph that a library target
        compiles and where the detector finds sites, leaves first. Its files are the cluster's
        (headers included: they become façades), any header-only cluster whose last C++ user it
        ports, and the build files. Executables' clusters stay C++ (Phase 14 ports them), and so
        does a header-only cluster that a test, an executable or an unported module uses. An
        earlier unit that uses such a header is told to stop including it."""
        project = self.project
        with_sites = set(files)
        library_of: dict[str, str] = {}
        executable: set[str] = set()
        for build in sorted(project.targets, key=lambda t: t.name):
            for path in build.files:
                if build.kind is TargetKind.LIBRARY:
                    library_of.setdefault(path, build.name)
                elif build.kind is TargetKind.EXECUTABLE:
                    executable.add(path)
        compiled = {*library_of, *executable}
        modules: list[tuple[int, list[str], str]] = []  # (cluster, files, library)
        header_only: list[tuple[int, list[str]]] = []
        placed: list[tuple[int, _Draft]] = []
        for position, cluster in enumerate(project.graph.clusters):
            members = [
                f
                for f in cluster
                if f in project.files
                and project.files[f].role is not FileRole.TEST
                and not self.is_new_code(f)
            ]
            if not members or not any(f in with_sites for f in members):
                continue
            libraries = sorted({library_of[f] for f in members if f in library_of})
            if libraries:
                modules.append((position, members, libraries[0]))
            elif any(f in compiled for f in members):
                if PortUnits.EXECUTABLE in self.planned_ports:
                    continue  # the executable's own unit ports it
                note = "skipped: an executable's code stays C++ until its tests are ported"
                placed.append((position, self._skipped(scan, members, f"{note} (Phase 14)")))
            else:
                header_only.append((position, members))
        unit_of = {f: i for i, (_, members, _) in enumerate(modules) for f in members}
        attached: dict[int, list[str]] = {}
        dropping: dict[int, list[str]] = {}  # earlier units: header-only files to stop using
        for position, members in header_only:
            users = project.dependents(members)
            ported_users = sorted({unit_of[u] for u in users if u in unit_of})
            kept = sorted(u for u in users if u not in unit_of)
            if kept or not ported_users:
                if self._retired_later(kept):
                    continue  # only tests and programs use it: they go Rust, and it goes
                shown = ", ".join(kept[:3]) + (f" (+{len(kept) - 3})" if len(kept) > 3 else "")
                note = (
                    f"skipped: header-only code that C++ still uses ({shown})"
                    if kept
                    else "skipped: header-only code that no module uses"
                )
                draft = self._skipped(scan, members, note)
                draft.blocking = bool(kept)
                placed.append((position, draft))
                continue
            last = ported_users[-1]
            attached.setdefault(last, []).extend(members)
            for earlier in ported_users[:-1]:
                dropping.setdefault(earlier, []).extend(members)
        taken: dict[str, set[str]] = {}
        units: list[tuple[int, list[str], PortUnit]] = []
        for i, (position, members, library) in enumerate(modules):
            unit_files = sorted({*members, *attached.get(i, [])})
            own_headers = [f for f in members if project.files[f].role is FileRole.HEADER]
            stem = (own_headers or members)[0].rsplit("/", 1)[-1].split(".", 1)[0]
            package, module = self.names(library, stem, taken.setdefault(library, set()))
            taken[library].add(module)
            ported_files = {f for j, (_, m, _) in enumerate(modules) if j != i for f in m}
            callers = sorted(
                {
                    *project.dependents(members),
                    *(f for f in project.dependents(attached.get(i, [])) if f not in ported_files),
                }
                - set(unit_files)
            )
            headers = [f for f in unit_files if project.files[f].role is FileRole.HEADER]
            port = PortUnit(
                build_target=library,
                crate=package,
                module=module,
                callers=callers,
                c_headers=[
                    f for f in headers if Boundary.C_ABI in project.boundaries.get(f, set())
                ],
            )
            units.append((position, unit_files, port))
        unit_ids = {i: f"the unit that ports {modules[i][1][0]}" for i in range(len(modules))}
        for i, (position, unit_files, port) in enumerate(units):
            sources = [f for f in unit_files if f in compiled]
            note = f"ports {', '.join(sources)} to {target} ({port.crate}::{port.module})"
            if attached.get(i):
                note += f" with {', '.join(sorted(attached[i]))}, which only it still uses"
            if dropping.get(i):
                later = next(j for j, files in attached.items() if dropping[i][0] in files)
                note += (
                    f"; its façade stops including {', '.join(sorted(dropping[i]))} "
                    f"({unit_ids[later]} removes it)"
                )
            if port.callers:
                note += f"; {len(port.callers)} C++ files keep using it through its headers"
            draft = _Draft(
                rule=scan.rule_id,
                files=sorted({*unit_files, *self.project.build_files}),
                sites=self.site_counts(scan, unit_files),
                note=note,
                port=port,
            )
            for path in unit_files:
                self.module_drafts.setdefault(path, draft)
            placed.append((position, draft))
        drafts = [d for _, d in sorted(placed, key=lambda pd: pd[0])]
        self.port_drafts += drafts
        return drafts

    def translation_units(self, scan: RuleScan, files: list[str], target: str) -> list[_Draft]:
        """One unit per C source file a library target compiles (D-68), with the headers only
        it includes, ordered leaves first by the calls between the sources rather than by
        includes: old C often has one internal header that declares every module's functions.
        The headers it includes that other files include too stay as they are (read-only
        references) until the retirement; those that declare what it exports are its C
        headers. A call cycle is broken where it costs least (:meth:`_call_order`). A program's
        sources are the executable rule's, and a source no target compiles is skipped."""
        project = self.project
        library_of: dict[str, str] = {}
        archive_of: dict[str, str] = {}
        executable: set[str] = set()
        for build in sorted(project.targets, key=lambda t: t.name):
            if build.kind is TargetKind.LIBRARY:
                archive = next((a for a in build.artifacts if a.endswith(".a")), None)
                if archive is not None:
                    archive_of.setdefault(build.name, archive)
            for path in build.files:
                if build.kind is TargetKind.LIBRARY:
                    library_of.setdefault(path, build.name)
                elif build.kind is TargetKind.EXECUTABLE:
                    executable.add(path)
        sources: list[str] = []
        drafts: list[_Draft] = []
        for path in files:
            if project.files[path].role is not FileRole.SOURCE or self.is_new_code(path):
                continue  # headers stay until the retirement
            if path in library_of:
                sources.append(path)
            elif path in executable:
                if PortUnits.EXECUTABLE not in self.planned_ports:
                    note = "skipped: a program's code stays C until a port_executable rule"
                    drafts.append(self._skipped(scan, [path], note))
            else:
                note = "skipped: no library target of the build compiles it"
                drafts.append(self._skipped(scan, [path], note))
        order = self._call_order(sources)
        later = {path: set(order[i + 1 :]) for i, path in enumerate(order)}
        taken: dict[str, set[str]] = {}
        for source in order:
            library = library_of[source]
            own = self._own_headers(source)
            kept = sorted(self._included(source) - set(own))
            exports = project.c_exports(source)
            declaring = set(project.files_declaring([s.id for s in exports]))
            unit_files = {source, *own}
            callers = sorted(set(project.fan_in([s.id for s in exports])) - unit_files - set(kept))
            stem = source.rsplit("/", 1)[-1].split(".", 1)[0]
            package, module = self.names(library, stem, taken.setdefault(library, set()))
            taken[library].add(module)
            port = PortUnit(
                build_target=library,
                crate=package,
                module=module,
                callers=callers,
                c_headers=[h for h in kept if h in declaring],
                exports=[s.name for s in exports],
                sys_headers=kept,
                archive=archive_of.get(library),
            )
            functions = sum(s.kind is SymbolKind.FUNCTION for s in exports)
            exported = " and ".join(
                f"{n} {word}{'' if n == 1 else 's'}"
                for n, word in ((functions, "function"), (len(exports) - functions, "variable"))
                if n
            )
            note = f"ports {source} to {target} ({package}::{module})"
            if exported:
                note += f", exporting {exported}"
            if own:
                note += f" with {', '.join(own)}, which only it includes"
            if kept:
                note += f"; keeps {', '.join(kept)}"
            still_c = sorted(set(project.calls.get(source, {})) & later[source])
            if still_c:
                they = "it is" if len(still_c) == 1 else "they are"
                note += f"; calls {', '.join(still_c)} while {they} still C (a call cycle)"
            variadic = [s.name for s in project.c_variadic(source)]
            if variadic:
                # Stable Rust defines neither `...` nor `va_list` functions (D-70).
                one = len(variadic) == 1
                note += (
                    f"; {' and '.join(variadic)} stay{'s' if one else ''} C in {source}, over "
                    f"the Rust code: {'it takes' if one else 'they take'} `...` or a `va_list`"
                )
            if callers:
                keep = "C file keeps" if len(callers) == 1 else "C files keep"
                note += f"; {len(callers)} {keep} calling it through its headers"
            draft = _Draft(
                rule=scan.rule_id,
                files=sorted({*unit_files, *kept, *project.build_files}),
                sites=self.site_counts(scan, sorted(unit_files)),
                references=kept,
                note=note,
                port=port,
            )
            for path in unit_files:
                self.module_drafts.setdefault(path, draft)
            drafts.append(draft)
            self.port_drafts.append(draft)
        return drafts

    def _call_order(self, sources: Sequence[str]) -> list[str]:
        """``sources`` leaves first by the calls between them, ties by path. Within a call
        cycle, the source that calls the fewest functions of the others still in C goes first,
        since Rust calls those through ``extern`` declarations until their own units (D-68)."""
        members = set(sources)
        calls = {
            s: {t: n for t, n in self.project.calls.get(s, {}).items() if t in members and t != s}
            for s in sources
        }
        order: list[str] = []
        for cluster in condense(sources, calls).clusters:
            left = set(cluster)
            while left:
                costs = sorted((sum(n for t, n in calls[f].items() if t in left), f) for f in left)
                order.append(costs[0][1])
                left.discard(costs[0][1])
        return order

    def _included(self, source: str) -> set[str]:
        """The project headers ``source`` includes, directly or through other headers."""
        found: set[str] = set()
        todo = [source]
        while todo:
            for path in sorted(self.project.includes.get(todo.pop(), set())):
                entry = self.project.files.get(path)
                if path not in found and entry is not None and entry.role is FileRole.HEADER:
                    found.add(path)
                    todo.append(path)
        return found

    def _own_headers(self, source: str) -> list[str]:
        """The headers only ``source`` includes (directly, or through headers only it includes)
        and that aren't public API: they go with its port (D-68)."""
        includers: dict[str, set[str]] = {}
        for path, targets in self.project.includes.items():
            for header in targets:
                includers.setdefault(header, set()).add(path)
        members = {source}
        changed = True
        while changed:
            changed = False
            for path in sorted(self._included(source) - members):
                entry = self.project.files[path]
                if not entry.public_api and includers.get(path, set()) <= members:
                    members.add(path)
                    changed = True
        return sorted(members - {source})

    # ------------------------------------------------------------------ finishing a port (D-60)

    def _retired_later(self, users: Sequence[str]) -> bool:
        """Whether every C++ file in ``users`` is a test or a program that the plan's later
        port rules take over, so code only they use goes with the retirement."""
        if not users or PortUnits.RETIREMENT not in self.planned_ports:
            return False
        programs = {
            f
            for t in self.project.targets
            if t.kind is TargetKind.EXECUTABLE and not self._test_target(t.name)
            for f in t.files
        }
        for path in users:
            entry = self.project.files.get(path)
            if entry is None:
                return False
            if entry.role is FileRole.TEST and PortUnits.TESTS in self.planned_ports:
                continue
            if path in programs and PortUnits.EXECUTABLE in self.planned_ports:
                continue
            return False
        return True

    def _test_target(self, name: str) -> bool:
        """A test target: an executable whose project files are all tests."""
        target = next((t for t in self.project.targets if t.name == name), None)
        if target is None or target.kind is not TargetKind.EXECUTABLE:
            return False
        known = [self.project.files[f] for f in target.files if f in self.project.files]
        return bool(known) and all(e.role is FileRole.TEST for e in known)

    def _project_libraries(self, names: Collection[str]) -> list[str]:
        """The library targets among ``names`` that compile the project's own code: not a
        vendored library (googletest built from third_party/) nor one of test helpers only."""
        found: list[str] = []
        for target in self.project.targets:
            if target.kind is not TargetKind.LIBRARY or target.name not in names:
                continue
            own = [self.project.files[f] for f in target.files if f in self.project.files]
            if any(e.role is not FileRole.TEST for e in own):
                found.append(target.name)
        return found

    def _crate_of(self, library: str) -> str:
        return self.names(library, "lib", ())[0]

    def _needed_modules(self, files: Iterable[str]) -> list[_Draft]:
        """The module units that port the project files ``files`` include, in plan order."""
        found: list[_Draft] = []
        for path in files:
            for dependency in sorted(self.project.edges.get(path, set())):
                draft = self.module_drafts.get(dependency)
                if draft is not None and draft not in found:
                    found.append(draft)
        return found

    def _closure(self, files: Iterable[str], role: FileRole) -> list[str]:
        """``files`` with the project headers (and files of ``role``) that only they include,
        transitively: a program's own headers, a test's helpers. Ported modules stay out."""
        members = set(files)
        changed = True
        while changed:
            changed = False
            reached = {d for f in members for d in self.project.edges.get(f, set())} - members
            for path in sorted(reached):
                entry = self.project.files.get(path)
                if entry is None or path in self.module_drafts:
                    continue
                if entry.role not in (FileRole.HEADER, role):
                    continue
                if set(self.project.dependents([path])) <= members:
                    members.add(path)
                    changed = True
        return sorted(members)

    def executable_units(self, scan: RuleScan, files: list[str], target: str) -> list[_Draft]:
        """One unit per executable that isn't a test target (D-60): its sources and the
        headers only they use become a Rust program named after it; the CTest entries that
        run it are the unit's tests; it needs the module units of what it includes."""
        project = self.project
        wanted = set(files)
        drafts: list[tuple[int, _Draft]] = []
        taken: set[str] = set()
        for build in sorted(project.targets, key=lambda b: b.name):
            if build.kind is not TargetKind.EXECUTABLE or self._test_target(build.name):
                continue
            sources = [f for f in build.files if f in project.files and f in wanted]
            if not sources:
                continue
            own = self._closure(sources, FileRole.HEADER)
            crate, _ = self.names(build.name, "main", taken)
            taken.add(crate)
            libraries = self._project_libraries(build.dependencies)
            tests = sorted(e.test_id for e in project.tests if e.runs == build.name)
            port = PortUnit(
                build_target=build.name,
                crate=crate,
                module="main",
                bin=build.name,
                dependencies=sorted({self._crate_of(lib) for lib in libraries}),
                tests=tests,
                artifact=next(iter(build.artifacts), None),
            )
            needs = self._needed_modules(own)
            # A Rust program links the library crates, not the C archive: in a C port it needs
            # every translation unit of the libraries it links (D-69).
            needs += [
                d
                for d in self.port_drafts
                if d.port is not None
                and d.port.archive is not None
                and d.port.build_target in libraries
                and d not in needs
            ]
            runs = f"; {len(tests)} CTest entries run it" if tests else ""
            draft = _Draft(
                rule=scan.rule_id,
                files=sorted({*own, *project.build_files}),
                sites=self.site_counts(scan, own),
                note=f"ports {build.name} ({', '.join(sources)}) to a {target} program, "
                f"{crate} (bin {build.name}){runs}",
                port=port,
                needs=needs,
            )
            drafts.append((min(project.cluster_index(f) for f in own), draft))
        placed = [d for _, d in sorted(drafts, key=lambda pd: pd[0])]
        self.port_drafts += placed
        return placed

    def test_units(self, scan: RuleScan, files: list[str], target: str) -> list[_Draft]:
        """One unit per test source a test target compiles (D-60): the file, read-only, is
        translated into an integration test of the crate of the library it tests; its cases
        are the unit's tests, and the unit that translates a test target's last file also maps
        the CTest entries that only run that binary. It needs the module units of what it
        includes."""
        project = self.project
        owner = {
            f: t.name
            for t in sorted(project.targets, key=lambda b: b.name)
            if self._test_target(t.name)
            for f in t.files
        }
        ordered = sorted(
            (f for f in files if f in owner and project.files[f].role is FileRole.TEST),
            key=lambda f: (project.cluster_index(f), f),
        )
        last: dict[str, str] = {}
        for path in ordered:
            last[owner[path]] = path
        taken: dict[str, set[str]] = {}
        drafts: list[_Draft] = []
        for path in ordered:
            test_target = owner[path]
            build = next(t for t in project.targets if t.name == test_target)
            references = self._closure([path], FileRole.TEST)
            modules = self._needed_modules(references)
            libraries = [m.port.build_target for m in modules if m.port is not None]
            libraries += self._project_libraries(build.dependencies)
            if not libraries:
                drafts.append(
                    self._skipped(scan, [path], "skipped: the test uses no library of the project")
                )
                continue
            # The library it uses most is the one it tests.
            library = max(dict.fromkeys(libraries), key=libraries.count)
            crate = self._crate_of(library)
            stem = path.rsplit("/", 1)[-1].split(".", 1)[0]
            _, module = self.names(library, stem, taken.setdefault(crate, set()))
            taken[crate].add(module)
            cases = sorted(s.text for s in scan.sites.get(path, []) if s.text)
            if cases and set(cases) <= self.mapped_tests:
                continue  # translated in an earlier run: the test map has every case
            wrappers = (
                sorted(
                    e.test_id
                    for e in project.tests
                    if e.runs == test_target
                    and e.test_id not in self.mapped_tests
                    and e.test_id not in cases  # a C test's entries are its cases (D-69)
                )
                if last[test_target] == path
                else []
            )
            port = PortUnit(
                build_target=library,
                crate=crate,
                module=module,
                dependencies=sorted({self._crate_of(lib) for lib in libraries} - {crate}),
                tests=cases,
                wrappers=wrappers,
            )
            maps = f"; maps {', '.join(wrappers)} to its cases" if wrappers else ""
            drafts.append(
                _Draft(
                    rule=scan.rule_id,
                    files=references,
                    sites=self.site_counts(scan, [path]),
                    references=references,
                    note=f"translates {path} ({len(cases)} test{'' if len(cases) == 1 else 's'}) "
                    f"to {target}: "
                    f"{crate}/tests/{module}.rs{maps}",
                    port=port,
                    needs=modules,
                )
            )
        self.port_drafts += [d for d in drafts if d.status is UnitStatus.PENDING]
        return drafts

    def retirement_units(self, scan: RuleScan, files: list[str], target: str) -> list[_Draft]:
        """The port's last unit (D-60): it deletes the source language's files and build,
        needing every other port unit. With ``keep_source_api`` the public headers stay, with
        what they include, the sources behind them, the build and the tests that use only
        them."""
        project = self.project
        code = sorted(f for f in files if f in project.files)
        kept: set[str] = set()
        if self.keep_source_api:
            kept = {f for f in code if project.files[f].public_api}
            grew = True
            while grew:
                before = len(kept)
                kept |= {d for f in kept for d in project.edges.get(f, set()) if d in project.files}
                kept |= {
                    f
                    for draft in self.module_drafts.values()
                    if kept & set(draft.files)
                    for f in draft.files
                    if f in project.files
                }
                grew = len(kept) > before

            def uses_kept_only(path: str) -> bool:
                return all(
                    d in kept or project.files[d].role is FileRole.TEST
                    for d in project.edges.get(path, set())
                    if d in project.files
                )

            tests = {
                f for f in code if project.files[f].role is FileRole.TEST and uses_kept_only(f)
            }
            kept |= tests
        retired = [f for f in code if f not in kept]
        build = [] if self.keep_source_api else list(project.build_files)
        if not retired and not build:
            return []
        needs = [d for d in self.port_drafts if d.blocking]
        language = _LANGUAGE_NAMES.get(self.language, self.language)
        system = "CMake" if any(f.endswith("CMakeLists.txt") for f in build) else "Make"
        if self.keep_source_api:
            note = (
                f"retires the {language} the public API doesn't need: deletes {len(retired)} "
                f"files, keeps {len(kept)} for outside {language} consumers"
            )
        else:
            note = (
                f"retires the {language}: deletes {len(retired)} files and the {system} build; "
                f"the {target} workspace then builds alone"
            )
        draft = _Draft(
            rule=scan.rule_id,
            files=sorted({*retired, *build}),
            sites=self.site_counts(scan, retired),
            note=note,
            sized=False,
            needs=needs,
            counted=[],
        )
        return [draft]

    def _skipped(self, scan: RuleScan, files: list[str], note: str) -> _Draft:
        return _Draft(
            rule=scan.rule_id,
            files=files,
            sites=self.site_counts(scan, files),
            status=UnitStatus.SKIPPED,
            note=note,
        )

    def rule_units(
        self, scan: RuleScan, target: str
    ) -> tuple[list[_Draft], list[str], dict[SkipCondition, list[str]]]:
        rule = scan.loaded.rule
        skipped: dict[SkipCondition, list[str]] = {}
        files: list[str] = []
        for path in scan.targets():
            if self.is_new_code(path):
                continue
            entry = self.project.files[path]
            condition = next((c for c in rule.skip if skip_matches(c, entry)), None)
            if condition is None:
                files.append(path)
            else:
                skipped.setdefault(condition, []).append(path)
        if rule.kind is RuleKind.PROJECT:
            drafts = self.project_units(scan, files, target)
        elif rule.module_port:
            drafts = self.module_units(scan, files, rule.target_language or target)
        elif rule.kind is RuleKind.PORT and rule.port_units is PortUnits.EXECUTABLE:
            drafts = self.executable_units(scan, files, rule.target_language or target)
        elif rule.kind is RuleKind.PORT and rule.port_units is PortUnits.TESTS:
            drafts = self.test_units(scan, files, rule.target_language or target)
        elif rule.kind is RuleKind.PORT and rule.port_units is PortUnits.TRANSLATION_UNIT:
            drafts = self.translation_units(scan, files, rule.target_language or target)
        elif rule.retirement:
            drafts = self.retirement_units(scan, files, rule.target_language or target)
        elif rule.kind is RuleKind.PORT:
            drafts = self.port_units(scan, files, rule.target_language or target)
        elif rule.kind is RuleKind.API_CHANGING:
            skipped_files = {f for paths in skipped.values() for f in paths}
            editable = {f for f in scan.eligible if f not in skipped_files}
            drafts = self.changing_units(scan, files, editable)
        else:
            drafts = self.preserving_units(scan, files, None)
        for condition in rule.skip:
            paths = skipped.get(condition)
            if paths and rule.kind is RuleKind.PORT and self._retired_later(paths):
                continue  # tests the port's later rules translate, or retire (test helpers)
            if paths:
                drafts.append(
                    _Draft(
                        rule=scan.rule_id,
                        files=sorted(paths),
                        sites=self.site_counts(scan, paths),
                        status=UnitStatus.SKIPPED,
                        flags={_SKIP_FLAGS[condition]},
                        note=f"skipped: {scan.rule_id} doesn't apply to {SKIP_REASONS[condition]}",
                        blocking=False,
                    )
                )
        for draft in drafts:
            if (
                draft.sized
                and draft.status is UnitStatus.PENDING
                and not self.fits(draft.files, scan.rule_id, token_cap=self.settings.token_limit)
            ):
                draft.status = UnitStatus.TOO_LARGE
                draft.note = self.size_note(draft.files, scan.rule_id)
            self.add_flags(draft)
        return drafts, files, skipped

    def add_flags(self, draft: _Draft) -> None:
        for path in draft.files:
            entry = self.project.files.get(path)
            if entry is not None:
                if entry.public_api:
                    draft.flags.add(UnitFlag.PUBLIC_API)
                if entry.c_compatible:
                    draft.flags.add(UnitFlag.C_COMPATIBLE)
                if entry.role is FileRole.TEST:
                    draft.flags.add(UnitFlag.TOUCHES_TESTS)
            if self.project.boundaries.get(path, set()) & _ABI_BOUNDARIES:
                draft.flags.add(UnitFlag.BOUNDARY)
        if draft.status is UnitStatus.TOO_LARGE:
            draft.flags.add(UnitFlag.TOO_LARGE)


def build_plan(
    project: ProjectModel,
    scans: Sequence[RuleScan],
    *,
    estimator: Estimator,
    editor: ModelInfo,
    settings: PlannerSettings,
    language: str,
    source: str,
    target: str,
    target_language: str | None = None,
    base_commit: str | None = None,
    warnings: Sequence[str] = (),
    port_names: PortNamer | None = None,
    keep_source_api: bool = False,
    mapped_tests: Collection[str] = (),
) -> Plan:
    """The ordered plan for ``scans`` (the standard bump first, then the manifest's rules).
    ``port_names`` names a module port's packages and modules (the target adapter's);
    ``keep_source_api`` keeps the public API a port's retirement would remove, and
    ``mapped_tests`` are the tests the project's test map already pairs (D-60)."""
    port_rules = [s.loaded.rule for s in scans if s.loaded.rule.kind is RuleKind.PORT]
    former = _Former(
        project,
        settings,
        estimator,
        port_names or plain_port_names,
        port_units=[r.port_units for r in port_rules if r.port_units is not None],
        keep_source_api=keep_source_api,
        mapped_tests=mapped_tests,
        new_code=sorted({g for r in port_rules for g in r.extra_scope}),
        language=language,
    )
    drafts: list[_Draft] = []
    plan_rules: list[PlanRule] = []
    notes = list(warnings)
    if editor.context_window is None:
        notes.append(
            f"the context window of {editor.name} is unknown, so units are sized for "
            f"{FALLBACK_CONTEXT_WINDOW} tokens; set num_ctx for Ollama models"
        )
    if editor.prices is None and not editor.local:
        notes.append(
            f"no prices are known for {editor.name}, so costs aren't estimated; set "
            f"llm.providers.{editor.provider}.prices"
        )
    for scan in scans:
        rule = scan.loaded.rule
        rule_drafts, files, skipped = former.rule_units(scan, target)
        drafts += rule_drafts
        if rule.natural_language_only:
            notes.append(
                f"{rule.id} has only LLM guidance, so it applies to all {len(files)} in-scope "
                f"{rule.language} files; expect a higher cost (FR-9.3)"
            )
        elif scan.has_detector and not scan.sites and rule.kind is not RuleKind.PROJECT:
            notes.append(f"{rule.id}: the detector found no sites, so the rule has no units")
        plan_rules.append(
            PlanRule(
                id=rule.id,
                kind=rule.kind,
                automatic=scan.automatic,
                sites=scan.site_count,
                files=len(files),
                skipped_files=sum(len(p) for p in skipped.values()),
                units=len(rule_drafts),
            )
        )

    rules_by_id = {scan.rule_id: scan.loaded.rule for scan in scans}
    ids = {id(draft): unit_id(seq) for seq, draft in enumerate(drafts, start=1)}
    units: list[WorkUnit] = []
    for seq, draft in enumerate(drafts, start=1):
        estimate = None
        if draft.status in (UnitStatus.PENDING, UnitStatus.TOO_LARGE):
            counted = draft.files if draft.counted is None else draft.counted
            chars = sum(project.chars(f) for f in counted)
            lines = sum(project.lines(f) for f in counted)
            estimate = estimator.unit(chars, [rules_by_id[draft.rule]], lines)
        units.append(
            WorkUnit(
                id=unit_id(seq),
                seq=seq,
                rules=[draft.rule],
                files=draft.files,
                reference_files=draft.references,
                rule_sites=draft.sites,
                flags=sorted(draft.flags),
                status=draft.status,
                estimate=estimate,
                note=draft.note,
                port=draft.port,
                needs=sorted(ids[id(n)] for n in draft.needs if id(n) in ids),
            )
        )
    pending = [u.estimate for u in units if u.status is UnitStatus.PENDING and u.estimate]
    cost = None if estimator.prices is None else round(sum(e.cost_usd or 0.0 for e in pending), 2)
    graph = project.graph
    return Plan(
        language=language,
        source_standard=source,
        target_standard=target,
        target_language=target_language,
        base_commit=base_commit,
        rules=plan_rules,
        graph=PlanGraph(
            files=len(project.files),
            edges=project.edge_count,
            clusters=len(graph.clusters),
            cyclic_clusters=len(graph.cyclic),
            largest_cluster=max((len(c) for c in graph.clusters), default=0),
        ),
        units=units,
        estimate=PlanEstimate(
            model=editor.name,
            context_window=editor.context_window,
            unit_token_limit=settings.token_limit,
            tokens=sum(e.tokens for e in pending),
            cost_usd=cost,
        ),
        warnings=notes,
    )
