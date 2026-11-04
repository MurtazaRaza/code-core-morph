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
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from codecoremorph.core.ids import unit_id
from codecoremorph.core.models import (
    Boundary,
    FileEntry,
    FileRole,
    Plan,
    PlanEstimate,
    PlanGraph,
    PlanRule,
    RuleKind,
    RuleSite,
    SkipCondition,
    UnitFlag,
    UnitStatus,
    WorkUnit,
)
from codecoremorph.llm.catalog import ModelInfo
from codecoremorph.planner.estimate import FALLBACK_CONTEXT_WINDOW, Estimator
from codecoremorph.planner.project import ProjectModel
from codecoremorph.planner.sites import RuleScan

DEFAULT_MAX_UNIT_FILES = 20  # NFR-1.3 sizes incremental re-indexing for units of this size
DEFAULT_MERGE_TOKENS = 40_000  # small clusters merge up to this much context

_ABI_BOUNDARIES = frozenset({Boundary.C_ABI, Boundary.JNI, Boundary.PINVOKE})
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


def skip_matches(condition: SkipCondition, entry: FileEntry) -> bool:
    if condition is SkipCondition.C_COMPATIBLE:
        return entry.c_compatible
    if condition is SkipCondition.PUBLIC_API:
        return entry.public_api
    return entry.role is FileRole.TEST


class _Former:
    def __init__(self, project: ProjectModel, settings: PlannerSettings, estimator: Estimator):
        self.project = project
        self.settings = settings
        self.estimator = estimator

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

    def rule_units(
        self, scan: RuleScan, target: str
    ) -> tuple[list[_Draft], list[str], dict[SkipCondition, list[str]]]:
        rule = scan.loaded.rule
        skipped: dict[SkipCondition, list[str]] = {}
        files: list[str] = []
        for path in scan.targets():
            entry = self.project.files[path]
            condition = next((c for c in rule.skip if skip_matches(c, entry)), None)
            if condition is None:
                files.append(path)
            else:
                skipped.setdefault(condition, []).append(path)
        if rule.kind is RuleKind.PROJECT:
            drafts = self.project_units(scan, files, target)
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
            if paths:
                drafts.append(
                    _Draft(
                        rule=scan.rule_id,
                        files=sorted(paths),
                        sites=self.site_counts(scan, paths),
                        status=UnitStatus.SKIPPED,
                        flags={_SKIP_FLAGS[condition]},
                        note=f"skipped: {scan.rule_id} doesn't apply to {SKIP_REASONS[condition]}",
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
) -> Plan:
    """The ordered plan for ``scans`` (the standard bump first, then the manifest's rules)."""
    former = _Former(project, settings, estimator)
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
    units: list[WorkUnit] = []
    for seq, draft in enumerate(drafts, start=1):
        estimate = None
        if draft.status in (UnitStatus.PENDING, UnitStatus.TOO_LARGE):
            chars = sum(project.chars(f) for f in draft.files)
            estimate = estimator.unit(chars, [rules_by_id[draft.rule]])
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
