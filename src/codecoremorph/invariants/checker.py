"""Runs the safety invariants on a verified attempt (SPEC §7.10, FR-4.4).

One :class:`InvariantChecker` follows one unit: :meth:`begin` records what the invariants
compare against (the snapshot, and the interface symbols the unit may change with the files
that use them), and :meth:`check` runs after an attempt builds and passes its tests. Failed
errors fail the attempt with feedback; warnings and approval requests travel with the unit to
the report and the unit gate.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field, replace
from pathlib import Path

from pydantic import Field

from codecoremorph.adapters.base import AdapterContext, AdapterNotSupportedError, LanguageAdapter
from codecoremorph.core.models import (
    BuildResult,
    CoreModel,
    InvariantResult,
    LoadedRule,
    RuleKind,
    Symbol,
    TestAssertions,
    TestBaseline,
    TestReport,
    WorkUnit,
)
from codecoremorph.invariants.checks import (
    ApiChange,
    InvariantOutcome,
    PathTest,
    api_changes,
    check_api,
    check_assertions,
    check_build_commands,
    check_diff_size,
    check_path_scope,
    check_suppressions,
    check_tests_kept,
    check_unit_scope,
    check_warnings,
    is_api_symbol,
    patch_lines,
)
from codecoremorph.parity.baseline import warnings_by_file
from codecoremorph.store.index import IndexStore
from codecoremorph.vcs.worktree import Worktree

log = logging.getLogger(__name__)

API_SNAPSHOT_FILE = "api-before.json"


class ApiSnapshot(CoreModel):
    """The interface symbols a unit may change, as they were before it, and the files that
    used each one (I-6)."""

    symbols: list[Symbol] = Field(default_factory=list[Symbol])
    referencing: dict[str, list[str]] = Field(
        default_factory=dict[str, list[str]], description="Symbol ID -> files referencing it"
    )
    project_wide: bool = Field(
        default=False, description="A project rule's unit: every in-scope file is in it"
    )


@dataclass(frozen=True)
class InvariantSettings:
    max_diff_lines: int = 800
    new_warnings: str = "warn"  # off | warn | fail (guardrails.new_warnings)
    gate_api_changes: bool = False  # approvals.unit covers api_changes
    gate_test_changes: bool = False  # approvals.unit covers test_changes (I-4)
    test_file_edits: str = "review"  # deny | review | allow (guardrails.test_file_edits)
    characterization_dir: str | None = None  # its tests' assertions always need approval


@dataclass
class _TestsSeen:
    """What the changed files' tests looked like before the unit and now."""

    markers: tuple[list[str], list[str]] = field(default_factory=lambda: ([], []))
    assertions: tuple[list[TestAssertions], list[TestAssertions]] | None = None


class InvariantChecker:
    """The invariants of one unit's attempts. Not thread-safe."""

    def __init__(
        self,
        adapter: LanguageAdapter,
        settings: InvariantSettings,
        *,
        excluded: PathTest,
        warnings_before: Mapping[str, int],
        store_dir: Path,
    ):
        """``excluded`` matches paths no change may touch (I-1); ``warnings_before`` holds the
        distinct warnings per file before the unit (I-10); ``store_dir`` keeps the unit's
        interface snapshot, so a host unit's later commands find it."""
        self.adapter = adapter
        self.settings = settings
        self.excluded = excluded
        self.warnings_before = dict(warnings_before)
        self.store_dir = store_dir
        self.snapshot: str | None = None
        self.warnings_after: dict[str, int] = {}

    # ------------------------------------------------------------------ the unit

    def begin(
        self,
        ctx: AdapterContext,
        worktree: Worktree,
        unit: WorkUnit,
        index_path: Path | None,
        *,
        project_rule: bool,
    ) -> None:
        """Record the snapshot and, when the unit holds an interface file (public API or a
        language boundary), that interface and who uses it."""
        self.snapshot = worktree.head()
        snapshot_file = self.store_dir / API_SNAPSHOT_FILE
        snapshot_file.unlink(missing_ok=True)
        if index_path is None or not index_path.is_file():
            return
        files = None if project_rule else set(unit.files)
        if files is not None:
            with IndexStore.open(index_path) as store:
                interface = {e.path for e in store.files() if e.public_api}
                interface |= set(store.boundary_files())
            if not files & interface:
                return
        index = self.adapter.index(ctx)  # the index must describe the snapshot
        with IndexStore.open(index.path) as store:
            symbols = [
                s
                for s in store.symbols()
                if is_api_symbol(s) and (files is None or s.file in files)
            ]
            referencing = (
                {} if files is None else {s.id: store.files_referencing([s.id]) for s in symbols}
            )
        snapshot = ApiSnapshot(symbols=symbols, referencing=referencing, project_wide=files is None)
        self.store_dir.mkdir(parents=True, exist_ok=True)
        snapshot_file.write_text(snapshot.model_dump_json(), encoding="utf-8")

    def _api_before(self) -> ApiSnapshot | None:
        path = self.store_dir / API_SNAPSHOT_FILE
        return ApiSnapshot.model_validate_json(path.read_bytes()) if path.is_file() else None

    # ------------------------------------------------------------------ an attempt

    def check(
        self,
        *,
        ctx: AdapterContext,
        worktree: Worktree,
        unit: WorkUnit,
        rules: Sequence[LoadedRule],
        allowed: PathTest,
        baseline: TestBaseline,
        built: BuildResult,
        report: TestReport,
        agent_since: str | None,
    ) -> InvariantOutcome:
        """The invariants of the attempt now in the worktree (it built and passed its tests).

        ``agent_since`` is the tree after the codemods: I-8 measures only the agent's lines,
        because codemods are deterministic and their changes are reviewed as a whole."""
        snapshot = self.snapshot or worktree.head()
        diff = worktree.diff(snapshot)
        changed = [f.path for f in diff.files]
        outcome = InvariantOutcome()
        outcome.results.append(check_path_scope(changed, excluded=self.excluded))
        outcome.results.append(check_unit_scope(changed, allowed=allowed))
        tests = self._tests(ctx, worktree, snapshot, changed)
        outcome.results.append(
            check_tests_kept(
                baseline, report, markers_before=tests.markers[0], markers_after=tests.markers[1]
            )
        )
        if tests.assertions is not None:
            protected = self.settings.characterization_dir
            outcome.results.append(
                check_assertions(
                    *tests.assertions,
                    deny_edits=self.settings.test_file_edits == "deny",
                    gated=self.settings.gate_test_changes,
                    protected=(lambda p: p.startswith(protected.rstrip("/") + "/"))
                    if protected
                    else (lambda _p: False),
                )
            )
        outcome.results.append(self._api(ctx, unit, rules, changed))
        allow = any(r.rule.allow_suppressions for r in rules)
        lines = patch_lines(diff.patch)
        outcome.results.append(check_suppressions(lines, allowed=allow))
        outcome.results.append(check_build_commands(lines))
        agent = worktree.diff(agent_since).files if agent_since else diff.files
        size = sum((f.added_lines or 0) + (f.removed_lines or 0) for f in agent)
        outcome.results.append(check_diff_size(size, limit=self.settings.max_diff_lines))
        self.warnings_after = warnings_by_file(built)
        if self.settings.new_warnings != "off":
            # Only changed files count: the build recompiled them, so their warnings show.
            outcome.results.append(
                check_warnings(
                    self.warnings_before,
                    self.warnings_after,
                    files=changed,
                    fail=self.settings.new_warnings == "fail",
                )
            )
        return outcome

    def _tests(
        self, ctx: AdapterContext, worktree: Worktree, snapshot: str, changed: list[str]
    ) -> _TestsSeen:
        """Disable markers (I-3) and test assertions (I-4) in the changed files, before (from
        the snapshot) and now. Either is empty when the adapter can't find them."""
        present = [p for p in changed if (worktree.path / p).is_file()]
        old = Path(tempfile.mkdtemp(prefix="codecoremorph-tests-"))
        try:
            existed: list[str] = []
            for path in changed:
                data = worktree.show(snapshot, path)
                if data is None:
                    continue
                target = old.joinpath(*path.split("/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                existed.append(path)
            old_ctx = replace(ctx, worktree=old)
            seen = _TestsSeen()
            with suppress(AdapterNotSupportedError):
                seen.markers = (
                    self.adapter.detect_disabled_tests(old_ctx, existed),
                    self.adapter.detect_disabled_tests(ctx, present),
                )
            with suppress(AdapterNotSupportedError):
                seen.assertions = (
                    self.adapter.extract_assertions(old_ctx, existed),
                    self.adapter.extract_assertions(ctx, present),
                )
        finally:
            shutil.rmtree(old, ignore_errors=True)
        return seen

    def _api(
        self,
        ctx: AdapterContext,
        unit: WorkUnit,
        rules: Sequence[LoadedRule],
        changed: list[str],
    ) -> InvariantResult:
        snapshot = self._api_before()
        touched = {s.file for s in snapshot.symbols} & set(changed) if snapshot else set[str]()
        if snapshot is None or not touched:
            return check_api([], api_changing=False, covered=lambda _c: True, gated=False)
        index = self.adapter.index(ctx)  # brings the touched files up to date
        with IndexStore.open(index.path) as store:
            now = [s for s in store.symbols() if is_api_symbol(s)]
        changes = api_changes(snapshot.symbols, now, files=touched)
        api_changing = any(r.rule.kind is RuleKind.API_CHANGING for r in rules)
        unit_files = set(unit.files)

        def covered(change: ApiChange) -> bool:
            if snapshot.project_wide:
                return True
            users = {f for sid in change.symbol_ids for f in snapshot.referencing.get(sid, [])}
            return users <= unit_files

        return check_api(
            changes,
            api_changing=api_changing or snapshot.project_wide,
            covered=covered,
            gated=self.settings.gate_api_changes,
        )
