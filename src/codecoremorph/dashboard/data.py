"""The dashboard's reads and its two writes (SPEC §7.12, §7.13).

Reads open the state database read-only, so they never contend with the engine's writes, and
read the run's files (plan, graph, results, attempt logs). The writes are the CLI's: a gate
decision (``record_decision``) and an abort request (``abort_run``); the engine acts on them.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import cast

from pydantic import BaseModel, JsonValue, ValidationError

from codecoremorph import __version__
from codecoremorph.config import load_config
from codecoremorph.config.models import MigrationConfig
from codecoremorph.core.errors import CodeCoreMorphError, NotFoundError, UsageError
from codecoremorph.core.ids import run_branch, validate_run_id, validate_unit_id
from codecoremorph.core.models import (
    ApprovalDecision,
    ApprovalGate,
    ApprovalRecord,
    DependencyGraph,
    Event,
    EventType,
    Plan,
    RunRecord,
    RunReport,
    RunStatus,
    UnitStatus,
    WorkUnit,
)
from codecoremorph.core.secrets import Redactor
from codecoremorph.dashboard.models import (
    AbortBody,
    ActionResult,
    Activity,
    AttemptFiles,
    AttemptView,
    Budget,
    DecisionBody,
    GraphFileView,
    GraphUnitView,
    GraphView,
    PlanView,
    PortFile,
    PortView,
    RunDetail,
    RunSummary,
    SessionInfo,
    TextFile,
    UnitGate,
    UnitView,
)
from codecoremorph.orchestrator.lifecycle import (
    BEFORE_PLAN_GATE,
    RunPlace,
    abort_run,
    record_decision,
    waiting_agent,
)
from codecoremorph.report.build import build_report, load_unit_result, redacted
from codecoremorph.report.render import render_markdown
from codecoremorph.store.db import Store
from codecoremorph.store.layout import AttemptArtifacts, RunArtifacts
from codecoremorph.vcs.repository import Repository

log = logging.getLogger(__name__)

RECENT_EVENTS = 50
LOG_TAIL_BYTES = 256 * 1024
DIFF_BYTES = 2 * 1024 * 1024
FILE_BYTES = 1024 * 1024
TRANSCRIPT_BYTES = 4 * 1024 * 1024

# A file in several units is colored by the most pressing of their statuses.
_PRESSING = [
    UnitStatus.IN_PROGRESS,
    UnitStatus.AWAITING_APPROVAL,
    UnitStatus.FAILED,
    UnitStatus.PENDING,
    UnitStatus.COMMITTED,
    UnitStatus.TOO_LARGE,
    UnitStatus.SKIPPED,
    UnitStatus.EXCLUDED,
]


@dataclass(frozen=True)
class DashboardContext:
    """The repository the dashboard serves, found from its manifest like the CLI's."""

    place: RunPlace
    project: str
    redactor: Redactor = field(default_factory=Redactor.from_environment)

    @classmethod
    def for_config(cls, config_path: Path) -> DashboardContext:
        place = RunPlace.for_config(config_path)
        project = load_config(config_path).config.project.name
        if place.state_dir.db_path.is_file():
            Store.open(place.state_dir.db_path).close()  # migrate once; the reads are read-only
        return cls(place=place, project=project)

    @property
    def db_path(self) -> Path:
        return self.place.state_dir.db_path

    def artifacts(self, run_id: str) -> RunArtifacts:
        return self.place.state_dir.run(run_id)


@contextmanager
def _reader(ctx: DashboardContext) -> Iterator[Store]:
    with Store.open_readonly(ctx.db_path) as store:
        yield store


def _run_id(run_id: str) -> str:
    try:
        return validate_run_id(run_id)
    except ValueError:
        raise NotFoundError(f"there is no run {run_id!r}") from None


def _unit_id(unit_id: str) -> str:
    try:
        return validate_unit_id(unit_id.upper())
    except ValueError:
        raise NotFoundError(f"there is no unit {unit_id!r}") from None


def _record(store: Store, run_id: str) -> RunRecord:
    record = store.get_run(run_id)
    if record is None:
        raise NotFoundError(
            f"this repository has no run {run_id}", hint="The runs page lists the runs."
        )
    return record


def _load[M: BaseModel](model: type[M], path: Path) -> M | None:
    if not path.is_file():
        return None
    try:
        return model.model_validate_json(path.read_bytes())
    except (OSError, ValidationError):
        log.warning("ignoring an unreadable %s", path)
        return None


@lru_cache(maxsize=16)
def _config_at(path: str, mtime_ns: int) -> MigrationConfig | None:
    del mtime_ns  # part of the cache key: an edited manifest is read again
    try:
        return load_config(Path(path)).config
    except CodeCoreMorphError:
        return None


def _run_config(ctx: DashboardContext, environment: dict[str, JsonValue]) -> MigrationConfig | None:
    """The manifest the run was started with, if it is still there, else the dashboard's."""
    recorded = environment.get("config_path")
    path = Path(recorded) if isinstance(recorded, str) else ctx.place.config_path
    if not path.is_file():
        path = ctx.place.config_path
    try:
        return _config_at(str(path), path.stat().st_mtime_ns)
    except OSError:
        return None


def _waiting(record: RunRecord, units: list[WorkUnit]) -> list[str]:
    waiting = [u.id for u in units if u.status is UnitStatus.AWAITING_APPROVAL]
    if record.status is RunStatus.AWAITING_PLAN_APPROVAL:
        waiting.insert(0, "plan")
    return waiting


def _counts(units: list[WorkUnit]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for unit in units:
        counts[unit.status.value] = counts.get(unit.status.value, 0) + 1
    return dict(sorted(counts.items()))


def _summary_fields(
    ctx: DashboardContext,
    record: RunRecord,
    units: list[WorkUnit],
    environment: dict[str, JsonValue],
) -> dict[str, object]:
    agent = environment.get("agent")
    sandbox = environment.get("sandbox")
    return {
        "id": record.id,
        "status": record.status,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "base_commit": record.base_commit,
        "branch": run_branch(record.id),
        "unit_counts": _counts(units),
        "waiting": _waiting(record, units),
        "engine_alive": ctx.place.engine_alive(record.id),
        "agent": agent if isinstance(agent, str) else None,
        "sandbox": sandbox if isinstance(sandbox, str) else None,
    }


# --------------------------------------------------------------------------- reads


def session_info(ctx: DashboardContext) -> SessionInfo:
    return SessionInfo(
        project=ctx.project,
        repository=str(ctx.place.repository),
        config_path=str(ctx.place.config_path),
        version=__version__,
    )


def list_runs(ctx: DashboardContext) -> list[RunSummary]:
    if not ctx.db_path.is_file():
        return []
    with _reader(ctx) as store:
        return [
            RunSummary.model_validate(
                _summary_fields(ctx, r, store.units(r.id), store.run_environment(r.id))
            )
            for r in store.list_runs()
        ]


def run_detail(ctx: DashboardContext, run_id: str) -> RunDetail:
    run_id = _run_id(run_id)
    with _reader(ctx) as store:
        record = _record(store, run_id)
        units = store.units(run_id)
        environment = store.run_environment(run_id)
        calls = store.llm_calls(run_id)
        events = store.recent_events(run_id, limit=RECENT_EVENTS)
        steps = store.recent_events(run_id, limit=1, types=[EventType.STEP_STARTED])
        last_id = store.last_event_id(run_id)
    artifacts = ctx.artifacts(run_id)
    plan = _load(Plan, artifacts.plan_json)
    config = _run_config(ctx, environment)
    guardrails = config.guardrails if config else None
    activity = None
    if steps:
        message = steps[-1].data.get("message")
        activity = Activity(ts=steps[-1].ts, message=str(message or ""))
    return RunDetail.model_validate(
        {
            **_summary_fields(ctx, record, units, environment),
            "language": plan.language if plan else None,
            "source_standard": plan.source_standard if plan else None,
            "target_standard": plan.target_standard if plan else None,
            "units_total": len(units),
            "units_done": sum(1 for u in units if u.status.is_terminal),
            "current_unit": next((u.id for u in units if u.status is UnitStatus.IN_PROGRESS), None),
            "activity": activity,
            "agent_waiting": None if record.status.is_terminal else waiting_agent(artifacts.root),
            "budget": Budget(
                tokens=sum(c.tokens for c in calls),
                cost_usd=round(sum(c.cost_usd for c in calls), 4),
                max_tokens=guardrails.max_run_tokens if guardrails else None,
                max_cost_usd=guardrails.max_run_cost_usd if guardrails else None,
            ),
            "plan_ready": plan is not None,
            "graph_ready": artifacts.graph_json.is_file(),
            "report_ready": artifacts.report_json.is_file(),
            "events": events,
            "last_event_id": last_id,
        }
    )


def _pending(
    store: Store, run_id: str, gate: ApprovalGate, unit_id: str | None = None
) -> ApprovalRecord | None:
    """A decision for ``gate`` (or an abort) that the engine hasn't acted on yet."""
    for approval in store.approvals(run_id, pending=True):
        if approval.gate is ApprovalGate.RUN and approval.decision is ApprovalDecision.ABORT:
            return approval
        if approval.gate is gate and (unit_id is None or approval.unit_id in (None, unit_id)):
            return approval
    return None


def plan_view(ctx: DashboardContext, run_id: str) -> PlanView:
    run_id = _run_id(run_id)
    with _reader(ctx) as store:
        record = _record(store, run_id)
        units = store.units(run_id)
        pending = _pending(store, run_id, ApprovalGate.PLAN)
    plan = _load(Plan, ctx.artifacts(run_id).plan_json)
    if plan is None:
        raise NotFoundError(
            f"run {run_id} has no plan yet",
            hint="The plan appears once the run has measured its baseline and indexed the code.",
        )
    if units:
        plan = plan.model_copy(update={"units": units})
    return PlanView(
        plan=plan,
        gate_open=record.status in BEFORE_PLAN_GATE and not record.status.is_terminal,
        pending=pending,
    )


def graph_view(ctx: DashboardContext, run_id: str) -> GraphView:
    run_id = _run_id(run_id)
    with _reader(ctx) as store:
        _record(store, run_id)
        units = store.units(run_id)
    artifacts = ctx.artifacts(run_id)
    graph = _load(DependencyGraph, artifacts.graph_json)
    if graph is None:
        raise NotFoundError(
            f"run {run_id} has no dependency graph",
            reason="the graph is saved when the plan is made; runs planned before Phase 7 "
            "don't have one",
        )
    if not units:
        plan = _load(Plan, artifacts.plan_json)
        units = plan.units if plan else []
    status_of = {u.id: u.status for u in units}
    file_units: dict[str, list[str]] = {}
    for unit in units:
        for path in unit.files:
            file_units.setdefault(path, []).append(unit.id)

    def pressing(ids: list[str]) -> UnitStatus | None:
        statuses = {status_of[i] for i in ids}
        return next((s for s in _PRESSING if s in statuses), None)

    files = [
        GraphFileView(
            path=f.path,
            cluster=f.cluster,
            units=file_units.get(f.path, []),
            status=pressing(file_units.get(f.path, [])),
        )
        for f in graph.files
    ]
    unit_edges: set[tuple[str, str]] = set()
    for source, target in graph.edges:
        sources, targets = set(file_units.get(source, [])), set(file_units.get(target, []))
        for a in sources - targets:
            for b in targets - sources:
                unit_edges.add((a, b))
    return GraphView(
        files=files,
        edges=list(graph.edges),
        units=[
            GraphUnitView(id=u.id, seq=u.seq, status=u.status, rules=u.rules, files=u.files)
            for u in units
        ],
        unit_edges=sorted(unit_edges),
    )


def _attempt_numbers(unit_dir: Path) -> list[int]:
    if not unit_dir.is_dir():
        return []
    return sorted(int(p.name) for p in unit_dir.iterdir() if p.is_dir() and p.name.isdigit())


def _file_names(directory: Path) -> list[str]:
    if not directory.is_dir():
        return []
    return sorted(p.name for p in directory.iterdir() if p.is_file())


def unit_view(ctx: DashboardContext, run_id: str, unit_id: str) -> UnitView:
    run_id, unit_id = _run_id(run_id), _unit_id(unit_id)
    with _reader(ctx) as store:
        _record(store, run_id)
        unit = next((u for u in store.units(run_id) if u.id == unit_id), None)
        attempts = store.attempts(run_id, unit_id)
        pending = _pending(store, run_id, ApprovalGate.UNIT, unit_id)
    artifacts = ctx.artifacts(run_id)
    if unit is None:
        plan = _load(Plan, artifacts.plan_json)
        unit = next((u for u in plan.units if u.id == unit_id), None) if plan else None
    if unit is None:
        raise NotFoundError(f"run {run_id} has no unit {unit_id}")
    result = load_unit_result(artifacts, unit_id)
    unit_dir = artifacts.unit_dir(unit_id)
    return UnitView(
        unit=unit,
        result=result,
        attempts=attempts,
        attempt_files=[
            AttemptFiles(number=n, files=_file_names(artifacts.attempt(unit_id, n).root))
            for n in _attempt_numbers(unit_dir)
        ],
        gate=UnitGate(
            waiting=unit.status is UnitStatus.AWAITING_APPROVAL,
            reasons=list(result.approvals) if result else [],
            commit=result.commit if result else None,
            pending=pending,
        ),
    )


PORT_FILE_BYTES = 256_000
_SOURCE_SUFFIXES = (".h", ".hh", ".hpp", ".hxx", ".c", ".cc", ".cpp", ".cxx", ".c++")
_TARGET_SUFFIXES = (".rs", ".hpp", ".toml")


def port_view(ctx: DashboardContext, run_id: str, unit_id: str) -> PortView:
    """The two sides of a module port's unit: its C++ files at the commit before it, and the new
    code under the crate it committed (generated files and lockfiles left out)."""
    view = unit_view(ctx, run_id, unit_id)
    unit, result = view.unit, view.result
    if unit.port is None:
        raise NotFoundError(f"unit {unit.id} of run {run_id} isn't a module port")
    repository = Repository(ctx.place.repository)
    after = result.commit if result else None
    if after is not None:
        before: str | None = repository.resolve_commit(f"{after}^")
    else:
        plan = _load(Plan, ctx.artifacts(run_id).plan_json)
        before = plan.base_commit if plan else None
    crate_dir = f"rust/{unit.port.crate}/"
    own = [f"{crate_dir}src/{unit.port.module}.rs", f"{crate_dir}src/bridge/{unit.port.module}.rs"]
    changed = result.changed_files if result else []
    targets = [f for f in own if f in changed] + sorted(
        f
        for f in changed
        if f.startswith("rust/") and f.endswith(_TARGET_SUFFIXES) and f not in own
    )

    def read(commit: str | None, path: str) -> PortFile | None:
        data = repository.show(commit, path) if commit else None
        if data is None:
            return None
        text = data[:PORT_FILE_BYTES].decode("utf-8", errors="replace")
        return PortFile(
            path=path, text=ctx.redactor.redact(text), truncated=len(data) > PORT_FILE_BYTES
        )

    sources = [
        found
        for path in unit.files
        if path.lower().endswith(_SOURCE_SUFFIXES)
        if (found := read(before, path)) is not None
    ]
    return PortView(
        unit_id=unit.id,
        crate=unit.port.crate,
        module=unit.port.module,
        build_target=unit.port.build_target,
        before=before,
        after=after,
        sources=sources,
        targets=[found for path in targets if (found := read(after, path)) is not None],
    )


def _text(ctx: DashboardContext, path: Path, limit: int, *, tail: bool) -> TextFile | None:
    """A text file, or its first or last ``limit`` bytes, with credentials redacted."""
    if not path.is_file():
        return None
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size > limit and tail:
            handle.seek(size - limit)
        data = handle.read(limit)
    text = data.decode("utf-8", errors="replace")
    if size > limit and tail:
        text = text.split("\n", 1)[-1]  # drop the partial first line
    return TextFile(
        name=path.name, text=ctx.redactor.redact(text), size=size, truncated=size > limit
    )


def _transcript(
    ctx: DashboardContext, path: Path
) -> tuple[list[dict[str, JsonValue]] | None, bool]:
    if not path.is_file():
        return None, False
    entries: list[dict[str, JsonValue]] = []
    read = 0
    truncated = False
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            read += len(line)
            if read > TRANSCRIPT_BYTES:
                truncated = True
                break
            try:
                entry = json.loads(ctx.redactor.redact(line))
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                entries.append(cast(dict[str, JsonValue], entry))
    return entries, truncated


def _attempt_dir(ctx: DashboardContext, run_id: str, unit_id: str, number: int) -> AttemptArtifacts:
    if number < 1:
        raise NotFoundError(f"there is no attempt {number}")
    return ctx.artifacts(run_id).attempt(unit_id, number)


def attempt_view(ctx: DashboardContext, run_id: str, unit_id: str, number: int) -> AttemptView:
    run_id, unit_id = _run_id(run_id), _unit_id(unit_id)
    with _reader(ctx) as store:
        _record(store, run_id)
        attempt = next((a for a in store.attempts(run_id, unit_id) if a.number == number), None)
    files = _attempt_dir(ctx, run_id, unit_id, number)
    if attempt is None and not files.root.is_dir():
        raise NotFoundError(f"unit {unit_id} of run {run_id} has no attempt {number}")
    transcript, truncated = _transcript(ctx, files.transcript_jsonl)
    return AttemptView(
        unit_id=unit_id,
        number=number,
        attempt=attempt,
        diff=_text(ctx, files.diff_patch, DIFF_BYTES, tail=False),
        feedback=_text(ctx, files.feedback_md, FILE_BYTES, tail=False),
        build_log=_text(ctx, files.build_log, LOG_TAIL_BYTES, tail=True),
        test_log=_text(ctx, files.test_log, LOG_TAIL_BYTES, tail=True),
        transcript=transcript,
        transcript_truncated=truncated,
        files=_file_names(files.root),
    )


def attempt_file(
    ctx: DashboardContext, run_id: str, unit_id: str, number: int, name: str
) -> TextFile:
    """One of the files an attempt kept (such as a host agent's ``edits-*.json``). Only names
    listed in the attempt's directory are served."""
    run_id, unit_id = _run_id(run_id), _unit_id(unit_id)
    files = _attempt_dir(ctx, run_id, unit_id, number)
    if name not in _file_names(files.root):
        raise NotFoundError(f"attempt {number} of unit {unit_id} has no file {name!r}")
    text = _text(ctx, files.root / name, FILE_BYTES, tail=False)
    if text is None:
        raise NotFoundError(f"attempt {number} of unit {unit_id} has no file {name!r}")
    return text


def report(ctx: DashboardContext, run_id: str) -> RunReport:
    """The run's report; a run still in progress gets one built from its records."""
    run_id = _run_id(run_id)
    artifacts = ctx.artifacts(run_id)
    written = _load(RunReport, artifacts.report_json)
    if written is not None:
        return written
    with _reader(ctx) as store:
        record = _record(store, run_id)
        built = build_report(
            store,
            artifacts,
            run_id,
            project=ctx.project,
            branch=run_branch(run_id),
            head=None,
            status=record.status,
            reason=None if record.status.is_terminal else "in progress",
            finished_at=None,
        )
    return redacted(built, ctx.redactor)


def report_markdown(ctx: DashboardContext, run_id: str) -> str:
    run_id = _run_id(run_id)
    path = ctx.artifacts(run_id).report_md
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return render_markdown(report(ctx, run_id))


def events_after(ctx: DashboardContext, run_id: str, after_id: int, limit: int) -> list[Event]:
    with _reader(ctx) as store:
        return store.list_events(run_id, after_id=after_id, limit=limit)


def require_run(ctx: DashboardContext, run_id: str) -> str:
    run_id = _run_id(run_id)
    if not ctx.db_path.is_file():
        raise NotFoundError(f"this repository has no run {run_id}")
    with _reader(ctx) as store:
        _record(store, run_id)
    return run_id


# --------------------------------------------------------------------------- writes


def _next_step(run_id: str, engine_alive: bool) -> str:
    if engine_alive:
        return "Recorded. The run acts on it right away."
    return (
        f"Recorded. No engine is running run {run_id}: it acts on the decision when the run "
        f"continues (`codecoremorph resume {run_id}`)."
    )


def decide_plan(ctx: DashboardContext, run_id: str, body: DecisionBody) -> ActionResult:
    run_id = require_run(ctx, run_id)
    with _reader(ctx) as store:
        record = _record(store, run_id)
    if record.status not in BEFORE_PLAN_GATE or record.status.is_terminal:
        raise UsageError(
            f"run {run_id}'s plan was already decided: the run is {record.status.value}"
        )
    if body.exclude and body.decision != "approve":
        raise UsageError("excluding units goes with approving the plan")
    decision = record_decision(
        ctx.place.config_path,
        run_id,
        decision=ApprovalDecision(body.decision),
        exclude=body.exclude or None,
        reason=body.reason,
        source="dashboard",
    )
    return ActionResult(
        run_id=run_id,
        approval_id=decision.approval.id,
        engine_alive=decision.engine_alive,
        message=_next_step(run_id, decision.engine_alive),
    )


def decide_unit(
    ctx: DashboardContext, run_id: str, unit_id: str, body: DecisionBody
) -> ActionResult:
    run_id, unit_id = require_run(ctx, run_id), _unit_id(unit_id)
    if body.exclude:
        raise UsageError("excluding units applies to the plan gate, not to a unit")
    decision = record_decision(
        ctx.place.config_path,
        run_id,
        decision=ApprovalDecision(body.decision),
        unit_id=unit_id,
        reason=body.reason,
        source="dashboard",
    )
    return ActionResult(
        run_id=run_id,
        approval_id=decision.approval.id,
        engine_alive=decision.engine_alive,
        message=_next_step(run_id, decision.engine_alive),
    )


def abort(ctx: DashboardContext, run_id: str, body: AbortBody) -> ActionResult:
    run_id = require_run(ctx, run_id)
    outcome = abort_run(ctx.place.config_path, run_id, reason=body.reason, source="dashboard")
    if outcome.engine_alive:
        message = "The engine stops at its next safe point and writes the report."
    else:
        message = "The run was ended and its report written."
        if outcome.branch_kept:
            message += f" Its branch {run_branch(run_id)} keeps the units it committed."
    return ActionResult(
        run_id=run_id, approval_id=None, engine_alive=outcome.engine_alive, message=message
    )


__all__ = [
    "DashboardContext",
    "abort",
    "attempt_file",
    "attempt_view",
    "decide_plan",
    "decide_unit",
    "events_after",
    "graph_view",
    "list_runs",
    "plan_view",
    "report",
    "report_markdown",
    "require_run",
    "run_detail",
    "session_info",
    "unit_view",
]
