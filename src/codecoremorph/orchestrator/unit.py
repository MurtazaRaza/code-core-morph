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
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from codecoremorph.agent.host import HostFiles, HostStep, HostUnit
from codecoremorph.agent.loop import AgentSettings, UnitOutcome, UnitRunner
from codecoremorph.config import load_config
from codecoremorph.config.models import SandboxMode
from codecoremorph.core.errors import ConfigError, RunNotActiveError, UsageError
from codecoremorph.core.ids import run_branch, unit_id
from codecoremorph.core.models import (
    Baseline,
    FileRole,
    HostStatus,
    HostUnitState,
    LoadedRule,
    RuleKind,
    WorkUnit,
)
from codecoremorph.core.models.vcs import DirtyState
from codecoremorph.core.paths import HomeLayout
from codecoremorph.llm.service import LLMService
from codecoremorph.orchestrator.planning import BASELINE_FILE
from codecoremorph.orchestrator.session import RunSession, project_root
from codecoremorph.parity.baseline import capture_baseline
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


def prepare_unit(
    session: RunSession, *, rule_ids: list[str], files: list[str], step: Step
) -> PreparedUnit:
    """Check the rules, capture the baseline, index, and resolve the unit's files."""
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
    baseline = capture_baseline(
        session.adapter,
        ctx,
        base_commit=session.workspace.base_commit,
        runs=config.parity.baseline_runs,
        require_tests=config.guardrails.require_passing_tests,
        on_step=lambda name: step(f"baseline: {name}"),
    )
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
    unit = WorkUnit(id=unit_id(1), seq=1, rules=rule_ids, files=sorted(set(files)))
    return PreparedUnit(
        unit=unit,
        rules=rules,
        order=order,
        baseline=baseline,
        index_path=index.path,
        build_files=build_files,
        project_files=project_files,
        scope_dirs=[*config.project.source_dirs, *config.project.test_dirs],
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
) -> HostStep:
    """Prepare a unit for a host agent; the session parks if the unit waits for edits."""
    step = on_step or _log_step
    config = session.config
    prepared = prepare_unit(session, rule_ids=rule_ids, files=files, step=step)
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
    )
    runner = _runner(session, prepared, None, None)
    runner.configured_standard = state.configured_standard
    return HostUnit(
        runner,
        rules,
        HostFiles(session.artifacts.root),
        language=config.migration.source.language,
        author=config.git.author,
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

    def latest(self) -> HostUnitState | None:
        """The most recently updated host unit, active or ended."""
        if not self.state_dir.runs_dir.is_dir():
            return None
        states = [
            HostFiles(path).load()
            for path in self.state_dir.runs_dir.iterdir()
            if HostFiles(path).state.is_file()
        ]
        return max(states, key=lambda s: s.updated_at, default=None)
