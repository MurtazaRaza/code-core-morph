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
from codecoremorph.core.globs import PathMatcher
from codecoremorph.core.models import (
    BuildResult,
    CoreModel,
    InvariantResult,
    LoadedRule,
    RuleKind,
    Severity,
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
    api_symbols,
    check_api,
    check_assertions,
    check_build_commands,
    check_diff_size,
    check_path_scope,
    check_suppressions,
    check_tests_kept,
    check_unit_scope,
    check_warnings,
    diff_size,
    patch_lines,
    still_declared,
)
from codecoremorph.invariants.test_map import (
    TestPair,
    by_source,
    gone_but_mapped,
    read_test_map,
    weaker_translations,
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


@dataclass(frozen=True)
class PortSide:
    """A port's target adapter and what the invariants read of its test map (D-60)."""

    adapter: LanguageAdapter
    ctx: AdapterContext
    test_map: str | None  # worktree-relative, None when the adapter keeps none
    source_language: str
    target_language: str


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
        port: PortSide | None = None,
    ):
        """``excluded`` matches paths no change may touch (I-1); ``warnings_before`` holds the
        distinct warnings per file before the unit (I-10); ``store_dir`` keeps the unit's
        interface snapshot, so a host unit's later commands find it. A ``port``'s test map
        accounts for translated tests (I-3, I-4), and its adapter reads the new tests'
        assertions (D-60)."""
        self.adapter = adapter
        self.port = port
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
            symbols = [s for s in api_symbols(store.symbols()) if files is None or s.file in files]
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
        pairs = self._pairs(worktree.path, None)
        outcome.results.append(
            check_tests_kept(
                baseline,
                report,
                markers_before=tests.markers[0],
                markers_after=tests.markers[1],
                accounted=lambda test, seen: not gone_but_mapped([test], seen, pairs),
            )
        )
        protected_dir = self.settings.characterization_dir

        def protected(path: str) -> bool:
            return protected_dir is not None and path.startswith(protected_dir.rstrip("/") + "/")

        if tests.assertions is not None:
            outcome.results.append(
                check_assertions(
                    *tests.assertions,
                    deny_edits=self.settings.test_file_edits == "deny",
                    gated=self.settings.gate_test_changes,
                    protected=protected,
                    translated=set(pairs),
                )
            )
        translations = self._translations(
            ctx, worktree, snapshot, agent_since or snapshot, unit, pairs, protected
        )
        if translations is not None:
            outcome.results.append(translations)
        if any(r.rule.retirement for r in rules):
            # The retirement removes the source language's API on purpose (D-60).
            outcome.results.append(
                check_api([], api_changing=False, covered=lambda _c: True, gated=False)
            )
        else:
            outcome.results.append(self._api(ctx, unit, rules, changed))
        lines = patch_lines(diff.patch)
        agent_diff = worktree.diff(agent_since) if agent_since else diff
        # Suppressions count on the agent's lines, like I-8: a codemod's are the rule's design
        # (the module port's scaffold allows unsafe code on its bridge, D-58).
        globs = [
            g
            for r in rules
            if isinstance(r.rule.allow_suppressions, list)
            for g in r.rule.allow_suppressions
        ]
        outcome.results.append(
            check_suppressions(
                patch_lines(agent_diff.patch),
                allowed=any(r.rule.allow_suppressions is True for r in rules),
                allowed_in=PathMatcher(globs).matches if globs else None,
            )
        )
        outcome.results.append(check_build_commands(lines))
        # I-8 bounds the agent's changes to the project's own files, a port's façades (D-58,
        # D-62).
        size = diff_size(
            agent_diff.files,
            new_code=PathMatcher([g for r in rules for g in r.rule.extra_scope]).matches,
            port=any(r.rule.kind is RuleKind.PORT for r in rules),
        )
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

    def _pairs(self, root: Path, snapshot: tuple[Worktree, str] | None) -> dict[str, TestPair]:
        """The port's test map, now (in ``root``) or at a snapshot commit; empty without."""
        port = self.port
        if port is None or port.test_map is None:
            return {}
        if snapshot is None:
            path = root / port.test_map
            data = path.read_bytes() if path.is_file() else None
        else:
            data = snapshot[0].show(snapshot[1], port.test_map)
        if data is None:
            return {}
        entries, _ = read_test_map(
            data.decode("utf-8", errors="replace"), port.source_language, port.target_language
        )
        return by_source(entries)

    def _translations(
        self,
        ctx: AdapterContext,
        worktree: Worktree,
        snapshot: str,
        mapped_since: str,
        unit: WorkUnit,
        pairs: Mapping[str, TestPair],
        protected: PathTest,
    ) -> InvariantResult | None:
        """I-4 across languages (D-60): each test the agent mapped since ``mapped_since`` (the
        tree after the codemods, whose entries are the rule's), against its translation. None
        when the agent maps nothing."""
        port = self.port
        if port is None:
            return None
        before = self._pairs(worktree.path, (worktree, mapped_since))
        added = [p for source, p in sorted(pairs.items()) if before.get(source) != p]
        if not added:
            return None
        sources: list[TestAssertions] = []
        old = Path(tempfile.mkdtemp(prefix="codecoremorph-tests-"))
        try:
            # The source cases as they were before the unit (a retirement deletes them).
            for path in unit.files:
                data = worktree.show(snapshot, path)
                if data is not None:
                    target = old.joinpath(*path.split("/"))
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
            with suppress(AdapterNotSupportedError):
                sources = self.adapter.extract_assertions(replace(ctx, worktree=old), unit.files)
        finally:
            shutil.rmtree(old, ignore_errors=True)
        try:
            rust_files = [
                p.relative_to(worktree.path).as_posix() for p in worktree.path.rglob("*.rs")
            ]
            targets = port.adapter.extract_assertions(port.ctx, rust_files)
        except AdapterNotSupportedError:
            return None
        found = weaker_translations(added, sources, targets)
        if not found:
            return InvariantResult(invariant="I-4", passed=True)
        cases = {a.test_id: a.file for a in sources}
        gate = self.settings.gate_test_changes or any(
            protected(cases[p.source]) for p in added if p.source in cases
        )
        return InvariantResult(
            invariant="I-4",
            passed=False,
            severity=Severity.WARNING,
            needs_approval=gate,
            message="Translated tests check less than their originals: "
            + "; ".join(found[:10])
            + (f"; and {len(found) - 10} more" if len(found) > 10 else "")
            + ".",
        )

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
            if self.port is not None:
                # A port's own tests are guarded too (a later unit must not weaken a test it
                # translated), in their package as it was: its manifest names the tests.
                for path in existed:
                    parts = path.split("/")
                    for n in range(len(parts) - 1, 0, -1):
                        manifest = "/".join([*parts[:n], "Cargo.toml"])
                        data = worktree.show(snapshot, manifest)
                        if data is not None and not old.joinpath(*manifest.split("/")).exists():
                            old.joinpath(*manifest.split("/")).write_bytes(data)
                with suppress(AdapterNotSupportedError):
                    port = self.port
                    before = port.adapter.extract_assertions(
                        replace(port.ctx, worktree=old), existed
                    )
                    after = port.adapter.extract_assertions(port.ctx, present)
                    found = seen.assertions or ([], [])
                    seen.assertions = (found[0] + before, found[1] + after)
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
            now = api_symbols(store.symbols())
            parsed = [e.path for e in store.files()]
        kept = still_declared(snapshot.symbols, changed=changed, parsed=parsed)
        changes = api_changes(snapshot.symbols, [*now, *kept], files=touched)
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
