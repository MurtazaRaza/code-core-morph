"""One work unit end to end, outside a full run (Phase 4).

- ``codecoremorph debug unit``: validate → workspace → provision → baseline → index →
  codemods → agent → verification, in one process, with a model provider as the agent.
- ``codecoremorph unit start|submit|abort`` (D-41): the same steps with a host agent (Claude
  Code through the ``codecoremorph`` skill) making the edits; the run parks between commands.

A ``debug unit`` is not committed unless asked; a host unit's success is one commit on its
run branch. The full run graph (Phase 5) owns multi-unit runs.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from codecoremorph.agent.host import HostFiles, HostStep, HostUnit
from codecoremorph.agent.loop import AgentSettings, UnitOutcome, UnitRunner
from codecoremorph.config import load_config
from codecoremorph.config.models import SandboxMode
from codecoremorph.core.errors import ConfigError, RunNotActiveError, UsageError
from codecoremorph.core.globs import PathMatcher
from codecoremorph.core.ids import run_branch, unit_id
from codecoremorph.core.models import (
    Baseline,
    FileEntry,
    FileRole,
    HostStatus,
    HostUnitState,
    LoadedRule,
    Plan,
    RuleKind,
    UnitStatus,
    WorkUnit,
)
from codecoremorph.core.models.vcs import DirtyState
from codecoremorph.core.paths import HomeLayout
from codecoremorph.llm.service import LLMService
from codecoremorph.orchestrator.planning import BASELINE_FILE, baseline_cache, reusable_baseline
from codecoremorph.orchestrator.session import RunSession, project_root
from codecoremorph.planner.planner import SKIP_REASONS, skip_matches
from codecoremorph.rules.applicability import StandardOrder, rule_excludes
from codecoremorph.store.index import IndexStore
from codecoremorph.store.layout import StateDir
from codecoremorph.vcs.lock import RepoLock, read_lock
from codecoremorph.vcs.repository import Repository
from codecoremorph.vcs.workspace import RunWorkspace
from codecoremorph.vcs.worktree import Worktree

log = logging.getLogger(__name__)

Step = Callable[[str], None]


def _log_step(message: str) -> None:
    log.info("%s", message)


@dataclass(frozen=True)
class SingleUnitResult:
    unit: WorkUnit
    outcome: UnitOutcome
    baseline: Baseline


@dataclass(frozen=True)
class PreparedUnit:
    unit: WorkUnit
    rules: list[LoadedRule]
    order: StandardOrder
    baseline: Baseline
    index_path: Path | None
    build_files: list[str]
    project_files: list[str]
    scope_dirs: list[str]
    uncompiled: list[str] = field(default_factory=list[str])


def skipped_files(rules: list[LoadedRule], entries: dict[str, FileEntry], files: list[str]) -> None:
    """Refuse files a rule's skip list excludes (headers shared with C, …), as the plan does."""
    for loaded in rules:
        for condition in loaded.rule.skip:
            matched = [f for f in files if f in entries and skip_matches(condition, entries[f])]
            if matched:
                raise UsageError(
                    f"{loaded.rule.id} doesn't apply to {SKIP_REASONS[condition]}, and these "
                    f"files are: {', '.join(matched)}",
                    reason=f"the rule's skip list has {condition.value}",
                    hint="Leave these files out of the unit; the plan marks such units skipped.",
                )


def prepare_unit(
    session: RunSession,
    *,
    rule_ids: list[str],
    files: list[str],
    step: Step,
    planned: WorkUnit | None = None,
) -> PreparedUnit:
    """Check the rules, capture the baseline, index, and resolve the unit's files.

    ``planned`` is the plan unit being run: the unit keeps its ID and position, and its
    reference files are in the unit only for their references to the symbols it changes
    (their own rule sites belong to other units)."""
    config = session.config
    migration = config.migration
    source, target = migration.source.standard, migration.target.standard
    order = StandardOrder(
        migration.source.language, tuple(session.adapter.capabilities().standards)
    )
    rules = [session.rules.get(r) for r in rule_ids]
    for loaded in rules:
        reason = rule_excludes(order, loaded.rule, source, target)
        if reason is not None:
            raise ConfigError(
                f"{loaded.rule.id} doesn't apply to {source} -> {target}: it {reason}",
                hint="Pick a rule that applies to the manifest's migration.",
            )
    ctx = session.ctx
    baseline = reusable_baseline(session, step)
    step("index")
    index = session.adapter.index(ctx)
    code_roles = (FileRole.SOURCE, FileRole.HEADER, FileRole.TEST)
    with IndexStore.open(index.path) as store:
        entries = store.files()
    build_files = [f.path for f in entries if f.role is FileRole.BUILD]
    project_files = [
        f.path
        for f in entries
        if f.role in code_roles and f.language == config.migration.source.language
    ]
    scope_dirs = [*config.project.source_dirs, *config.project.test_dirs]
    in_scope = PathMatcher([d for d in scope_dirs if d != "."]) if "." not in scope_dirs else None
    uncompiled = [f for f in index.meta.uncompiled if in_scope is None or in_scope.matches(f)]
    if not files:
        if not any(r.rule.kind is RuleKind.PROJECT for r in rules):
            raise UsageError(
                "name the unit's files",
                hint="e.g. --rule replace_typedefs_with_using include/geo/types.h",
            )
        files = build_files
    missing = [f for f in files if not (ctx.worktree / f).is_file()]
    if missing:
        raise UsageError(
            f"not files of the repository at the base commit: {', '.join(missing)}",
            hint="Give paths relative to the repository root, with forward slashes.",
        )
    references = sorted(set(planned.reference_files if planned else ()) & set(files))
    skipped_files(rules, {e.path: e for e in entries}, [f for f in files if f not in references])
    unit = WorkUnit(
        id=planned.id if planned else unit_id(1),
        seq=planned.seq if planned else 1,
        rules=rule_ids,
        files=sorted(set(files)),
        reference_files=references,
    )
    return PreparedUnit(
        unit=unit,
        rules=rules,
        order=order,
        baseline=baseline,
        index_path=index.path,
        build_files=build_files,
        project_files=project_files,
        scope_dirs=scope_dirs,
        uncompiled=uncompiled,
    )


def _runner(
    session: RunSession,
    prepared: PreparedUnit,
    llm: LLMService | None,
    settings: AgentSettings | None,
) -> UnitRunner:
    config = session.config
    return UnitRunner(
        adapter=session.adapter,
        ctx=session.ctx,
        worktree=session.workspace.worktree,
        baseline=prepared.baseline,
        llm=llm,
        order=prepared.order,
        source=config.migration.source.standard,
        target=config.migration.target.standard,
        run_id=session.run_id,
        artifacts=session.artifacts,
        index_path=prepared.index_path,
        build_files=prepared.build_files,
        project_files=prepared.project_files,
        scope_dirs=prepared.scope_dirs,
        settings=settings
        or AgentSettings.from_config(
            config.guardrails, store_transcripts=config.reporting.store_transcripts
        ),
        uncompiled=prepared.uncompiled,
    )


def run_single_unit(
    session: RunSession,
    *,
    rule_ids: list[str],
    files: list[str],
    llm: LLMService,
    settings: AgentSettings | None = None,
    on_step: Step | None = None,
) -> SingleUnitResult:
    step = on_step or _log_step
    prepared = prepare_unit(session, rule_ids=rule_ids, files=files, step=step)
    runner = _runner(session, prepared, llm, settings)
    unit = prepared.unit
    step(f"unit {unit.id}: {', '.join(rule_ids)} on {len(unit.files)} files")
    outcome = runner.run(unit, prepared.rules)
    return SingleUnitResult(unit=unit, outcome=outcome, baseline=prepared.baseline)


# --------------------------------------------------------------------------- host agent


def start_host_unit(
    session: RunSession,
    *,
    rule_ids: list[str],
    files: list[str],
    on_step: Step | None = None,
    plan_unit: WorkUnit | None = None,
) -> HostStep:
    """Prepare a unit for a host agent; the session parks if the unit waits for edits.

    With ``plan_unit``, the unit is that unit of the plan: its rules, files and reference
    files (``rule_ids`` and ``files`` are then ignored)."""
    step = on_step or _log_step
    config = session.config
    if plan_unit is not None:
        rule_ids, files = list(plan_unit.rules), list(plan_unit.files)
    prepared = prepare_unit(
        session,
        rule_ids=rule_ids,
        files=files,
        step=step,
        planned=plan_unit,
    )
    (session.artifacts.root / BASELINE_FILE).write_text(
        prepared.baseline.model_dump_json(indent=2), encoding="utf-8"
    )
    runner = _runner(session, prepared, None, None)
    worktree = session.workspace.worktree
    head = worktree.head()
    now = datetime.now(UTC)
    state = HostUnitState(
        run_id=session.run_id,
        config_path=str(session.config_path.resolve()),
        worktree=str(worktree.path),
        sandbox=session.executor.kind,
        base_commit=session.workspace.base_commit,
        unit=prepared.unit,
        source=config.migration.source.standard,
        target=config.migration.target.standard,
        snapshot=head,
        head=head,
        index_path=str(prepared.index_path),
        build_files=prepared.build_files,
        project_files=prepared.project_files,
        scope_dirs=prepared.scope_dirs,
        plan_unit=plan_unit.id if plan_unit else None,
        uncompiled=prepared.uncompiled,
        excused_tests=sorted({*prepared.baseline.tests.failing, *prepared.baseline.tests.flaky}),
        configured_standard=prepared.baseline.standard,
        max_retry_budget=runner.settings.max_retry_budget,
        max_edit_retries=runner.settings.max_edit_retries,
        status=HostStatus.WAITING,
        user_tree=session.workspace.dirty,
        started_at=now,
        updated_at=now,
    )
    host = HostUnit(
        runner,
        prepared.rules,
        HostFiles(session.artifacts.root),
        language=config.migration.source.language,
        author=config.git.author,
        baselines=baseline_cache(session),
    )
    unit = prepared.unit
    step(f"unit {unit.id}: {', '.join(rule_ids)} on {len(unit.files)} files")
    result = host.start(state)
    end_session(session, result.state)
    return result


def end_session(session: RunSession, state: HostUnitState) -> None:
    """Park a waiting unit's run; otherwise clean up, keeping the branch only with a commit."""
    session.park = state.status is HostStatus.WAITING
    session.delete_branch = state.commit is None
    session.keep_build = False


def resume_host_unit(session: RunSession, state: HostUnitState) -> HostUnit:
    """The host unit of a resumed session, rebuilt from its saved state."""
    config = session.config
    baseline = Baseline.model_validate_json((session.artifacts.root / BASELINE_FILE).read_bytes())
    rules = [session.rules.get(r) for r in state.unit.rules]
    prepared = PreparedUnit(
        unit=state.unit,
        rules=rules,
        order=StandardOrder(
            config.migration.source.language, tuple(session.adapter.capabilities().standards)
        ),
        baseline=baseline,
        index_path=Path(state.index_path) if state.index_path else None,
        build_files=state.build_files,
        project_files=state.project_files,
        scope_dirs=state.scope_dirs,
        uncompiled=state.uncompiled,
    )
    runner = _runner(session, prepared, None, None)
    runner.configured_standard = state.configured_standard
    runner.error_lines = list(state.error_lines)
    return HostUnit(
        runner,
        rules,
        HostFiles(session.artifacts.root),
        language=config.migration.source.language,
        author=config.git.author,
        baselines=baseline_cache(session),
    )


def abort_host_unit(run: HostRun, reason: str | None) -> HostStep:
    """Give the active unit up: remove its worktree, build and run branch, release the lock.

    It starts no sandbox (a parked run's container is already gone) and doesn't need the
    worktree to be intact, so it also cleans up after a crash.
    """
    state = run.active()
    lock = RepoLock.resume(run.state_dir.lock_path, state.run_id)
    home = HomeLayout.resolve()
    workspace = RunWorkspace(
        run_id=state.run_id,
        repository=Repository(run.repository),
        base_commit=state.base_commit,
        worktree=Worktree(Path(state.worktree), run_branch(state.run_id)),
        build_dir=home.build_dir(state.run_id),
        cache_dir=home.run_cache(state.run_id),
        dirty=DirtyState(),
        lock=lock,
    )
    why = reason or "the agent stopped the unit"
    state = HostUnitState.model_validate(
        {
            **state.model_dump(),
            "status": HostStatus.ABORTED,
            "reason": why,
            "updated_at": datetime.now(UTC),
        }
    )
    try:
        run.files(state.run_id).save(state)
    finally:
        workspace.cleanup(delete_branch=True)
        if state.sandbox == "docker":
            from codecoremorph.sandbox.docker.executor import release_run

            release_run(state.run_id)
    return HostStep(
        state,
        f"Unit {state.unit.id} was aborted: {why}. Its worktree and run branch were removed; "
        "nothing was kept.",
    )


def user_tree_warnings(session: RunSession, state: HostUnitState) -> list[str]:
    """Files of the user's working tree that changed while the unit ran. CodeCoreMorph never
    changes them, so a host that edited them by mistake made edits the unit doesn't have."""
    before = set(state.user_tree.modified) | set(state.user_tree.untracked)
    now = session.workspace.repository.dirty_state()
    changed = sorted((set(now.modified) | set(now.untracked)) - before)
    if not changed:
        return []
    more = f" and {len(changed) - 10} more" if len(changed) > 10 else ""
    shown = ", ".join(changed[:10]) + more
    return [
        f"files in the project directory changed while the unit ran: {shown}. The unit "
        "doesn't see them: put every edit in the edits file. Don't revert them yourself; "
        "they may be the user's work"
    ]


def sandbox_mode(state: HostUnitState) -> SandboxMode:
    """Resume with the executor the unit started with: its build tree depends on it."""
    return SandboxMode.DOCKER if state.sandbox == "docker" else SandboxMode.NATIVE


@dataclass(frozen=True)
class HostRun:
    """Where to find a repository's host units, from its manifest."""

    repository: Path
    state_dir: StateDir

    @classmethod
    def for_config(cls, config_path: Path) -> HostRun:
        config = load_config(config_path).config
        repository = Repository.discover(project_root(config_path, config))
        return cls(repository.root, StateDir.for_repo(repository.root))

    def files(self, run_id: str) -> HostFiles:
        return HostFiles(self.state_dir.run(run_id).root)

    def active(self) -> HostUnitState:
        """The state of the unit that holds the repository lock."""
        holder = read_lock(self.state_dir.lock_path)
        if holder is None:
            raise RunNotActiveError(
                "no unit is active in this repository",
                hint="Start one with `codecoremorph unit start --rule RULE FILE...`.",
            )
        files = self.files(holder.run_id)
        if not files.state.is_file():
            raise RunNotActiveError(
                f"run {holder.run_id} holds this repository, but it isn't a host unit",
                reason="another CodeCoreMorph command is running here",
                hint="Wait for it to finish.",
            )
        return files.load()

    def states(self) -> list[HostUnitState]:
        """Every host unit of the repository, active or ended."""
        if not self.state_dir.runs_dir.is_dir():
            return []
        return [
            HostFiles(path).load()
            for path in sorted(self.state_dir.runs_dir.iterdir())
            if HostFiles(path).state.is_file()
        ]

    def latest(self) -> HostUnitState | None:
        """The most recently updated host unit, active or ended."""
        return max(self.states(), key=lambda s: s.updated_at, default=None)

    def latest_plan(self) -> tuple[Path, Plan] | None:
        """The repository's latest plan (``codecoremorph plan``), if any. Run IDs sort by time."""
        runs = self.state_dir.runs_dir
        plans = sorted(runs.glob("*/plan.json")) if runs.is_dir() else []
        if not plans:
            return None
        return plans[-1], Plan.model_validate_json(plans[-1].read_bytes())

    def plan(self) -> tuple[Path, Plan]:
        found = self.latest_plan()
        if found is None:
            raise UsageError(
                "this repository has no plan yet",
                hint="Run `codecoremorph plan` first; it builds and tests the project, so it "
                "takes a few minutes.",
            )
        return found

    def refuse_skipped(self, rule_ids: list[str], files: list[str]) -> None:
        """Refuse, before any build, files the latest plan skips for one of ``rule_ids``. The
        rule's skip list is checked again against the index once the unit starts."""
        found = self.latest_plan()
        if found is None:
            return
        for unit in found[1].units:
            if unit.status is not UnitStatus.SKIPPED or not set(unit.rules) & set(rule_ids):
                continue
            hit = sorted(set(unit.files) & set(files))
            if hit:
                raise UsageError(
                    f"the plan skips {', '.join(hit)} for {', '.join(unit.rules)}",
                    reason=_reason(unit),
                    hint="Leave these files out of the unit, and tell the user why.",
                )

    def plan_unit(self, unit_id: str) -> WorkUnit:
        """A pending unit of the latest plan; skipped and too-large units are refused."""
        path, plan = self.plan()
        wanted = unit_id.strip().upper()
        for unit in plan.units:
            if unit.id != wanted:
                continue
            if unit.status is not UnitStatus.PENDING:
                raise UsageError(
                    f"plan unit {unit.id} is {unit.status.value}",
                    reason=_reason(unit),
                    hint="Go on with the next pending unit (`codecoremorph unit list`), and tell "
                    "the user why this one was left out.",
                )
            return unit
        ids = [u.id for u in plan.units]
        shown = f"{ids[0]} to {ids[-1]}" if ids else "no units"
        raise UsageError(
            f"the plan has no unit {unit_id}",
            reason=f"{path} has {shown}",
            hint="`codecoremorph unit list` shows them.",
        )

    def plan_progress(self) -> dict[str, HostUnitState]:
        """The last run of each plan unit that was started with ``--plan-unit``."""
        progress: dict[str, HostUnitState] = {}
        for state in sorted(self.states(), key=lambda s: s.updated_at):
            if state.plan_unit is not None:
                progress[state.plan_unit] = state
        return progress


def _reason(unit: WorkUnit) -> str:
    """Why the planner left a unit out, from its note."""
    note = (unit.note or "").removeprefix("skipped: ").strip()
    return note or "the planner left it out on purpose"
