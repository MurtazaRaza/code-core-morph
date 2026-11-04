"""Starting, resuming, answering and stopping runs: what the CLI's run commands call
(FR-10, FR-11, FR-14, SPEC §7.14).

A run lives in its repository's state directory: the store (``codecoremorph.db``) and
``runs/<run-id>/``. Its engine holds the repository lock while it works; a parked run (stopped
at a gate or by Ctrl-C) keeps the lock, worktree and build for ``codecoremorph resume``. The
CLI and the dashboard only write decisions into the store; the engine acts on them.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from codecoremorph.agent.host import CHECKPOINT_EMAIL, HostFiles
from codecoremorph.config import load_config
from codecoremorph.config.models import SandboxMode
from codecoremorph.core.errors import (
    CodeCoreMorphError,
    RunNotActiveError,
    UsageError,
)
from codecoremorph.core.ids import run_branch
from codecoremorph.core.models import (
    ApprovalDecision,
    ApprovalGate,
    ApprovalRecord,
    Event,
    EventType,
    HostStatus,
    RunRecord,
    RunStatus,
    UnitStatus,
)
from codecoremorph.core.models.vcs import DirtyState
from codecoremorph.core.paths import HomeLayout
from codecoremorph.orchestrator.run import (
    AgentAction,
    LlmFactory,
    RunEngine,
    RunOptions,
    RunResult,
    agent_request,
    finish_report,
    new_run_record,
    reject_unit,
    run_environment,
)
from codecoremorph.orchestrator.session import RunSession, open_session, project_root
from codecoremorph.report.build import load_unit_result
from codecoremorph.store.db import Store
from codecoremorph.store.events import EventWriter
from codecoremorph.store.layout import StateDir
from codecoremorph.vcs.lock import RepoLock, held_by_running_command, read_lock
from codecoremorph.vcs.repository import Repository
from codecoremorph.vcs.workspace import RunWorkspace
from codecoremorph.vcs.worktree import Worktree

log = logging.getLogger(__name__)

WarningSink = Callable[[str], None]


def _now() -> datetime:
    return datetime.now(UTC)


# A plan decision is taken in these statuses: the plan can be approved while it is being made.
BEFORE_PLAN_GATE = frozenset(
    {
        RunStatus.CREATED,
        RunStatus.PROVISIONING,
        RunStatus.BASELINING,
        RunStatus.INDEXING,
        RunStatus.CHARACTERIZING,
        RunStatus.PLANNING,
        RunStatus.AWAITING_PLAN_APPROVAL,
    }
)


@dataclass(frozen=True)
class RunPlace:
    """Where a repository's runs live, found from its manifest."""

    config_path: Path
    repository: Path
    state_dir: StateDir

    @classmethod
    def for_config(cls, config_path: Path) -> RunPlace:
        config = load_config(config_path).config
        repository = Repository.discover(project_root(config_path, config))
        return cls(config_path, repository.root, StateDir.for_repo(repository.root))

    def store(self) -> Store:
        return Store.open(self.state_dir.db_path)

    def record(self, store: Store, run_id: str | None) -> RunRecord:
        """The run named, or the latest one."""
        if run_id is None:
            runs = store.list_runs()
            if not runs:
                raise UsageError(
                    "this repository has no runs yet",
                    hint="Start one with `codecoremorph run`.",
                )
            return runs[0]
        record = store.get_run(run_id)
        if record is None:
            raise UsageError(
                f"this repository has no run {run_id}",
                hint="`codecoremorph status` lists the runs.",
            )
        return record

    def engine_alive(self, run_id: str) -> bool:
        """Whether ``run_id``'s engine is running now (it holds the lock, not parked)."""
        return held_by_running_command(self.state_dir.lock_path, run_id)


def _end_session(session: RunSession, result: RunResult) -> None:
    """Park a stopped run; otherwise clean up, keeping the branch only if it has commits."""
    session.park = result.parked
    session.keep_build = False
    session.delete_branch = not result.parked and result.head == session.workspace.base_commit


def _drive(
    session: RunSession, engine: RunEngine, *, fresh: bool, agent: AgentAction | None = None
) -> RunResult:
    try:
        result = engine.drive(fresh=fresh, agent=agent)
    except CodeCoreMorphError as exc:
        # An environment problem (credentials, Docker, a toolchain): keep everything, so the
        # run continues once it is fixed.
        session.park = True
        if agent is not None and agent.kind == "submit":
            again = "then run `codecoremorph unit submit` again: the edits file is back in place."
        else:
            again = f"then continue it with `codecoremorph resume {session.run_id}`."
        resume = f"The run is parked: {again}"
        exc.hint = f"{exc.hint} {resume}" if exc.hint else resume
        raise
    except BaseException:
        session.park = True
        raise
    finally:
        engine.graph.close()
        engine.llm.close()
    _end_session(session, result)
    return result


def start_run(
    config_path: Path,
    *,
    base: str | None,
    mode: SandboxMode | None,
    options: RunOptions,
    llm_factory: LlmFactory | None = None,
    on_warning: WarningSink | None = None,
) -> RunResult:
    """``codecoremorph run``: a new run, driven until it ends or parks."""
    with open_session(
        config_path, base=base, mode=mode, on_warning=on_warning, delete_branch=False
    ) as session:
        session.park = True  # until the run ends; a crash leaves it resumable
        with Store.open(StateDir.for_repo(session.workspace.repository.root).db_path) as store:
            store.create_run(new_run_record(session))
            engine = RunEngine(session, store, options=options, llm_factory=llm_factory)
            store.set_run_environment(
                session.run_id, run_environment(session, engine.llm, host=options.host)
            )
            engine.events.emit(
                EventType.RUN_CREATED,
                base_commit=session.workspace.base_commit,
                branch=session.workspace.worktree.branch,
            )
            return _drive(session, engine, fresh=True)


def resume_run(
    config_path: Path,
    run_id: str | None,
    *,
    options: RunOptions,
    agent: AgentAction | None = None,
    llm_factory: LlmFactory | None = None,
    on_warning: WarningSink | None = None,
) -> RunResult:
    """``codecoremorph resume``: continue a parked or crashed run from its last checkpoint.
    ``unit submit|abort`` pass the host agent's ``agent`` action for the unit waiting for it.
    A run started with a host agent stays one (D-44)."""
    place = RunPlace.for_config(config_path)
    with place.store() as store:
        record = place.record(store, run_id)
        if record.status.is_terminal:
            raise RunNotActiveError(
                f"run {record.id} has already ended: {record.status}",
                hint="Start a new run with `codecoremorph run`.",
            )
        environment = store.run_environment(record.id)
    if environment.get("agent") == "host":
        options = replace(options, host=True, ask=None)
    elif agent is not None:
        raise UsageError(
            f"run {record.id} doesn't use a host agent: its model makes the edits",
            hint=f"Continue it with `codecoremorph resume {record.id}`.",
        )
    sandbox = environment.get("sandbox")
    mode = SandboxMode.DOCKER if sandbox == "docker" else SandboxMode.NATIVE
    manifest = environment.get("config_path")
    path = Path(manifest) if isinstance(manifest, str) and Path(manifest).is_file() else config_path
    base = record.base_commit or ""
    with (
        open_session(path, mode=mode, on_warning=on_warning, resume=(record.id, base)) as session,
        place.store() as store,
    ):
        engine = RunEngine(session, store, options=options, llm_factory=llm_factory)
        engine.events.emit(EventType.LOG, message="the run resumes")
        return _drive(session, engine, fresh=False, agent=agent)


# --------------------------------------------------------------------------- decisions


@dataclass(frozen=True)
class Decision:
    approval: ApprovalRecord
    engine_alive: bool


def record_decision(
    config_path: Path,
    run_id: str | None,
    *,
    decision: ApprovalDecision,
    unit_id: str | None = None,
    exclude: list[str] | None = None,
    reason: str | None = None,
    source: str = "cli",
) -> Decision:
    """``codecoremorph approve|reject``: write the decision for the gate the run waits at (or
    will wait at: a plan can be approved while it is being made)."""
    place = RunPlace.for_config(config_path)
    with place.store() as store:
        record = place.record(store, run_id)
        if record.status.is_terminal:
            raise RunNotActiveError(f"run {record.id} has already ended: {record.status}")
        waiting = [u for u in store.units(record.id) if u.status is UnitStatus.AWAITING_APPROVAL]
        if unit_id is None and record.status not in BEFORE_PLAN_GATE:
            if len(waiting) != 1:
                raise UsageError(
                    f"run {record.id} has no plan waiting for approval"
                    if not waiting
                    else f"several units wait for approval: {', '.join(u.id for u in waiting)}",
                    hint="Name the unit with --unit." if waiting else "Nothing needs a decision.",
                )
            unit_id = waiting[0].id
        if unit_id is not None:
            unit_id = unit_id.strip().upper()
            if unit_id not in {u.id for u in waiting}:
                raise UsageError(
                    f"unit {unit_id} of run {record.id} isn't waiting for approval",
                    hint="`codecoremorph status` shows what the run waits for.",
                )
            if exclude:
                raise UsageError("--exclude applies to the plan gate, not to a unit")
        approval = store.add_approval(
            ApprovalRecord(
                run_id=record.id,
                gate=ApprovalGate.UNIT if unit_id else ApprovalGate.PLAN,
                unit_id=unit_id,
                decision=decision,
                reason=reason,
                exclude=[u.strip().upper() for u in exclude or []],
                source=source,
                created_at=_now(),
            )
        )
    return Decision(approval, place.engine_alive(record.id))


@dataclass
class AbortOutcome:
    run_id: str
    engine_alive: bool  # the engine stops at its next safe point and writes the report
    report_md: Path | None = None
    branch_kept: bool = False
    notes: list[str] = field(default_factory=list[str])


def abort_run(
    config_path: Path, run_id: str | None, *, reason: str | None, source: str = "cli"
) -> AbortOutcome:
    """``codecoremorph abort`` (and the dashboard's). A running engine stops at its next safe
    point; a parked or crashed run is ended here, without starting a sandbox: a unit waiting
    for approval is taken off the branch, the report is written and the worktree removed."""
    place = RunPlace.for_config(config_path)
    with place.store() as store:
        record = place.record(store, run_id)
        if record.status.is_terminal:
            raise RunNotActiveError(f"run {record.id} has already ended: {record.status}")
        approval = store.add_approval(
            ApprovalRecord(
                run_id=record.id,
                gate=ApprovalGate.RUN,
                decision=ApprovalDecision.ABORT,
                reason=reason,
                source=source,
                created_at=_now(),
            )
        )
        if place.engine_alive(record.id):
            return AbortOutcome(record.id, engine_alive=True)
        return _finish_offline(place, store, record, approval)


def _finish_offline(
    place: RunPlace, store: Store, record: RunRecord, approval: ApprovalRecord
) -> AbortOutcome:
    run_id = record.id
    outcome = AbortOutcome(run_id, engine_alive=False)
    lock = RepoLock.resume(place.state_dir.lock_path, run_id) if _holds(place, run_id) else None
    home = HomeLayout.resolve()
    worktree = Worktree(home.worktree(run_id), run_branch(run_id))
    artifacts = place.state_dir.run(run_id)
    base = record.base_commit or ""
    head: str | None = None
    try:
        if worktree.path.is_dir():
            # A unit waiting for its host agent leaves checkpoint commits: they go too.
            worktree.rollback(worktree.below(CHECKPOINT_EMAIL, base) if base else worktree.head())
            _end_host_unit(artifacts.root, "the run was aborted")
            for unit in store.units(run_id):
                if unit.status is UnitStatus.AWAITING_APPROVAL:
                    result = load_unit_result(artifacts, unit.id)
                    reject_unit(worktree, artifacts, store, unit, result, approval)
                elif unit.status is UnitStatus.IN_PROGRESS:
                    store.put_units(
                        run_id, [unit.model_copy(update={"status": UnitStatus.PENDING})], _now()
                    )
            head = worktree.head()
        else:
            outcome.notes.append(f"the run's worktree was already gone: {worktree.path}")
        if approval.id is not None:
            store.consume_approval(approval.id, _now())
        finished = _now()
        store.set_run_status(run_id, RunStatus.ABORTED, finished)
        EventWriter(store, run_id).emit(
            EventType.RUN_STATUS, status="aborted", reason=approval.reason
        )
        project = load_config(place.config_path).config.project.name
        outcome.report_md, _ = finish_report(
            store,
            artifacts,
            run_id,
            project=project,
            branch=worktree.branch,
            head=head,
            status=RunStatus.ABORTED,
            reason=approval.reason,
            finished=finished,
        )
    finally:
        keep_branch = head is not None and head != base
        outcome.branch_kept = keep_branch
        if lock is not None:
            workspace = RunWorkspace(
                run_id=run_id,
                repository=Repository(place.repository),
                base_commit=base,
                worktree=worktree,
                build_dir=home.build_dir(run_id),
                cache_dir=home.run_cache(run_id),
                dirty=DirtyState(),
                lock=lock,
            )
            workspace.cleanup(delete_branch=not keep_branch)
        if store.run_environment(run_id).get("sandbox") == "docker":
            from codecoremorph.sandbox.docker.executor import release_run

            release_run(run_id)
    return outcome


def _holds(place: RunPlace, run_id: str) -> bool:
    holder = read_lock(place.state_dir.lock_path)
    return holder is not None and holder.run_id == run_id


def _end_host_unit(run_dir: Path, reason: str) -> None:
    """Mark the run's unit that waits for its host agent as aborted."""
    files = HostFiles(run_dir)
    if not files.state.is_file():
        return
    state = files.load()
    if state.in_run and state.status is HostStatus.WAITING:
        files.save(
            state.model_copy(
                update={
                    "status": HostStatus.ABORTED,
                    "reason": reason,
                    "head": state.snapshot,
                    "updated_at": _now(),
                }
            )
        )


def waiting_agent(run_dir: Path) -> dict[str, Any] | None:
    """The unit of a host-agent run that waits for the agent's edits, as the engine's
    ``agent`` interrupt describes it; None if no unit waits."""
    files = HostFiles(run_dir)
    if not files.state.is_file():
        return None
    state = files.load()
    if not state.in_run or state.status is not HostStatus.WAITING:
        return None
    return agent_request(files, state)


# --------------------------------------------------------------------------- status


@dataclass(frozen=True)
class RunOverview:
    record: RunRecord
    unit_counts: dict[str, int]
    current: str | None  # the unit in progress
    waiting: list[str]  # what waits for a decision: "plan", or unit IDs
    tokens: int
    cost_usd: float
    engine_alive: bool
    events: list[Event]
    report_md: Path | None
    agent: dict[str, Any] | None = None  # the unit waiting for its host agent's edits (D-44)


def overview(place: RunPlace, store: Store, record: RunRecord, *, events: int = 10) -> RunOverview:
    units = store.units(record.id)
    counts: dict[str, int] = {}
    for unit in units:
        counts[unit.status.value] = counts.get(unit.status.value, 0) + 1
    calls = store.llm_calls(record.id)
    waiting = [u.id for u in units if u.status is UnitStatus.AWAITING_APPROVAL]
    if record.status is RunStatus.AWAITING_PLAN_APPROVAL:
        waiting.insert(0, "plan")
    all_events = store.list_events(record.id)
    report = place.state_dir.run(record.id).report_md
    return RunOverview(
        record=record,
        unit_counts=dict(sorted(counts.items())),
        current=next((u.id for u in units if u.status is UnitStatus.IN_PROGRESS), None),
        waiting=waiting,
        tokens=sum(c.tokens for c in calls),
        cost_usd=round(sum(c.cost_usd for c in calls), 4),
        engine_alive=place.engine_alive(record.id),
        events=all_events[-events:],
        report_md=report if report.is_file() else None,
        agent=None
        if record.status.is_terminal
        else waiting_agent(place.state_dir.run(record.id).root),
    )


# --------------------------------------------------------------------------- clean


@dataclass
class CleanOutcome:
    cleaned: list[str] = field(default_factory=list[str])
    skipped: dict[str, str] = field(default_factory=dict[str, str])
    branches_deleted: list[str] = field(default_factory=list[str])


def clean_runs(
    place: RunPlace,
    *,
    run_id: str | None,
    every: bool,
    branches: bool,
    older_than_days: float | None,
) -> CleanOutcome:
    """``codecoremorph clean``: remove what finished runs left outside the repository (their
    worktrees, build trees, caches and containers) and, with ``branches``, their branches.
    Active and parked runs are left alone (FR-6.5)."""
    outcome = CleanOutcome()
    home = HomeLayout.resolve()
    repository = Repository(place.repository)
    holder = read_lock(place.state_dir.lock_path)
    with place.store() as store:
        records = {r.id: r for r in store.list_runs()}
    ids = sorted(set(records) | _run_dirs(place.state_dir) | set(_branch_ids(repository)))
    if run_id is not None:
        ids = [i for i in ids if i == run_id]
        if not ids:
            raise UsageError(f"this repository has no run {run_id}")
    elif not every and older_than_days is None:
        raise UsageError(
            "name a run, or use --all or --older-than", hint="e.g. codecoremorph clean --all"
        )
    cutoff = _now() - timedelta(days=older_than_days) if older_than_days is not None else None
    for rid in ids:
        record = records.get(rid)
        if holder is not None and holder.run_id == rid:
            outcome.skipped[rid] = "active or parked: resume or abort it first"
            continue
        if record is not None and not record.status.is_terminal:
            outcome.skipped[rid] = f"not finished ({record.status}): resume or abort it first"
            continue
        if cutoff is not None and record is not None and record.updated_at > cutoff:
            outcome.skipped[rid] = "newer than --older-than"
            continue
        repository.remove_worktree(home.worktree(rid))
        for directory in (home.build_dir(rid), home.run_cache(rid)):
            shutil.rmtree(directory, ignore_errors=True)
        if record is not None and record.repo_root and _was_docker(place, rid):
            from codecoremorph.sandbox.docker.executor import release_run

            release_run(rid)
        outcome.cleaned.append(rid)
        branch = run_branch(rid)
        if branches and repository.branch_exists(branch):
            repository.delete_run_branch(branch)
            outcome.branches_deleted.append(branch)
    return outcome


def _was_docker(place: RunPlace, run_id: str) -> bool:
    with place.store() as store:
        return store.run_environment(run_id).get("sandbox") == "docker"


def _run_dirs(state_dir: StateDir) -> set[str]:
    from codecoremorph.core.ids import is_run_id

    runs = state_dir.runs_dir
    return {p.name for p in runs.iterdir() if is_run_id(p.name)} if runs.is_dir() else set()


def _branch_ids(repository: Repository) -> list[str]:
    from codecoremorph.core.ids import BRANCH_PREFIX

    return [b.removeprefix(BRANCH_PREFIX) for b in repository.run_branches()]


__all__ = [
    "BEFORE_PLAN_GATE",
    "AbortOutcome",
    "CleanOutcome",
    "Decision",
    "RunOverview",
    "RunPlace",
    "abort_run",
    "clean_runs",
    "overview",
    "record_decision",
    "resume_run",
    "start_run",
    "waiting_agent",
]
