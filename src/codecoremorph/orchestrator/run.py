"""``codecoremorph run``: a whole migration as a LangGraph state machine (SPEC §7.5, FR-14).

```
baseline → index → plan → plan_gate → next_unit ─┬→ unit ─┬→ unit_gate → next_unit
                                        ↑        │    │   └→ next_unit
                                        │        │    └→ agent ⟲ ─┬→ unit_gate → next_unit
                                        └────────┘                └→ next_unit
                                                     no unit left → final_verify → report
```

Validating the manifest, preparing the workspace and provisioning the toolchain happen when
the engine opens its session (``open_session``): they make the resources every node uses, so
the graph starts at ``baseline``. A stop (``codecoremorph abort``, a budget, a rejected plan)
sends the graph straight to ``report``.

- **Units** run one at a time through ``UnitRunner``: codemods, agent attempts with feedback,
  verification and the safety invariants. A green unit becomes one commit with trailers; a
  failed one rolls back, and later units still run (§4.5).
- **Gates** use LangGraph interrupts. The engine answers them from the store's ``approvals``
  rows, written by ``codecoremorph approve|reject`` or by the CLI's own prompt. A unit gate
  comes after the unit's commit, and rejecting it resets the branch to the commit before
  (D-43): the branch is green at every point, also while a unit waits.
- **Stops.** ``codecoremorph abort`` ends the run with a report. The first Ctrl-C parks it:
  the unit in progress rolls back, and ``codecoremorph resume`` continues it. A killed engine
  resumes the same way: the worktree goes back to the branch's last commit and the graph to
  its last checkpoint; a unit that committed before the kill is found by its trailers.
- **Host agents** (``run --host-agent``, D-44). A coding agent that runs CodeCoreMorph as a
  tool makes the edits, and no model is called. The ``unit`` node runs the codemods and the
  first verification as ``HostUnit``; when edits are needed, the ``agent`` node interrupts and
  the run parks. ``codecoremorph unit submit`` resumes it: the node applies and verifies the
  edits and loops back to wait after a rejection or a failed attempt. ``unit abort`` gives the
  unit up and the run goes on. A host's command never waits in the process: at a gate with no
  decision in the store, the run parks for ``approve``/``reject`` and ``resume``.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import JsonValue

from codecoremorph import __version__
from codecoremorph.adapters.base import AdapterNotSupportedError
from codecoremorph.adapters.config import language_config
from codecoremorph.agent.host import CHECKPOINT_EMAIL, HostFiles, HostStep, HostUnit
from codecoremorph.agent.loop import AgentSettings, UnitOutcome, UnitRunner
from codecoremorph.config.models import MigrationConfig, PlanApproval, UnitApproval
from codecoremorph.core.errors import (
    BaselineError,
    BudgetExhaustedError,
    ConfigError,
    ExitCode,
    UsageError,
)
from codecoremorph.core.globs import PathMatcher
from codecoremorph.core.models import (
    ApprovalDecision,
    ApprovalGate,
    ApprovalRecord,
    Attempt,
    AttemptOutcome,
    Baseline,
    EventType,
    FileRole,
    FinalVerification,
    HostStatus,
    HostUnitState,
    LlmCallRecord,
    LoadedRule,
    Role,
    RunRecord,
    RunStatus,
    SymbolIndex,
    TestStatus,
    UnitResult,
    UnitStatus,
    Usage,
    WorkUnit,
)
from codecoremorph.llm.ledger import LedgerEntry, UsageLedger
from codecoremorph.llm.service import LLMService
from codecoremorph.orchestrator.control import StopControl, StopRequestedError
from codecoremorph.orchestrator.graph import STOP, NodeSpec, RunGraph, RunState, wait_for_decision
from codecoremorph.orchestrator.planning import (
    baseline_cache,
    plan_from,
    reusable_baseline,
    write_baseline,
)
from codecoremorph.orchestrator.session import RunSession, build_standard
from codecoremorph.orchestrator.unit import (
    PreparedUnit,
    host_unit_state,
    make_runner,
    prepare_unit,
    resume_host_unit,
    user_tree_warnings,
)
from codecoremorph.parity.baseline import unit_baseline
from codecoremorph.prompts import PROMPTS_VERSION
from codecoremorph.report.build import (
    FINAL_FILE,
    build_report,
    load_unit_result,
    unit_result_path,
    write_json,
    write_report,
)
from codecoremorph.store.db import Store
from codecoremorph.store.events import EventWriter
from codecoremorph.store.index import IndexStore
from codecoremorph.store.layout import RunArtifacts
from codecoremorph.vcs.messages import unit_commit_message
from codecoremorph.vcs.worktree import Worktree

log = logging.getLogger(__name__)

StepSink = Callable[[str], None]
LlmFactory = Callable[[UsageLedger], LLMService]
Asker = Callable[[dict[str, Any]], ApprovalRecord | None]

_STOP_STATUS = {
    "aborted": RunStatus.ABORTED,
    "budget_exhausted": RunStatus.BUDGET_EXHAUSTED,
    "rejected": RunStatus.REJECTED,
    "failed": RunStatus.FAILED,
    "baseline_failed": RunStatus.FAILED,
}
_LEFT_OUT = frozenset(
    {UnitStatus.FAILED, UnitStatus.SKIPPED, UnitStatus.TOO_LARGE, UnitStatus.EXCLUDED}
)
_EXIT_CODES = {
    RunStatus.COMPLETED: ExitCode.SUCCESS,
    RunStatus.COMPLETED_WITH_FAILURES: ExitCode.COMPLETED_WITH_FAILURES,
    RunStatus.BUDGET_EXHAUSTED: ExitCode.BUDGET_EXHAUSTED,
    RunStatus.ABORTED: ExitCode.ABORTED,
    RunStatus.REJECTED: ExitCode.REJECTED,
    RunStatus.FAILED: ExitCode.COMPLETED_WITH_FAILURES,
}


def _now() -> datetime:
    return datetime.now(UTC)


def _quiet(_message: str) -> None:
    return None


def _nothing() -> None:
    return None


@dataclass(frozen=True)
class RunOptions:
    yes: bool = False  # --yes: approve the plan gate without asking (FR-11.4)
    ask: Asker | None = None  # answers a gate interactively; None: wait for the store
    poll_s: float = 1.0
    on_step: StepSink = _quiet
    on_warning: StepSink = _quiet
    on_stop_requested: Callable[[], None] = _nothing  # the first Ctrl-C
    host: bool = False  # a host agent makes the edits and no model is called (D-44)


AGENT_GATE = "agent"  # the interrupt where a unit waits for its host agent's edits


@dataclass(frozen=True)
class AgentAction:
    """What the host agent's command asks of the unit that waits for it (D-44)."""

    kind: Literal["submit", "abort"]
    reason: str | None = None


class _Park(Exception):  # noqa: N818 - control flow, not an error
    """The run waits for someone outside this process: the host agent, or a decision."""

    def __init__(self, request: dict[str, Any]):
        super().__init__(str(request.get("gate")))
        self.request = request


@dataclass(frozen=True)
class RunResult:
    run_id: str
    status: RunStatus
    reason: str | None
    branch: str
    head: str | None
    parked: bool = False  # stopped and resumable
    waiting: dict[str, Any] | None = None  # the gate a parked run waits at
    report_md: Path | None = None
    report_json: Path | None = None
    baseline_failed: bool = False
    # Parked by design: a host-agent run waits for the agent's edits or for a decision.
    awaiting: bool = False
    notes: tuple[str, ...] = ()  # what the host agent's command did, for the agent to read

    @property
    def exit_code(self) -> ExitCode:
        if self.parked:
            return ExitCode.SUCCESS if self.awaiting else ExitCode.ABORTED
        if self.baseline_failed:
            return ExitCode.ENVIRONMENT
        return _EXIT_CODES.get(self.status, ExitCode.INTERNAL)


def _models(attempts: list[Attempt]) -> list[str]:
    return sorted({a.model for a in attempts if a.model})


def agent_request(files: HostFiles, state: HostUnitState) -> dict[str, Any]:
    """The ``agent`` interrupt's request: the unit that waits for its host agent's edits."""
    return {
        "gate": AGENT_GATE,
        "run_id": state.run_id,
        "unit": state.unit.id,
        "rules": list(state.unit.rules),
        "attempt": state.attempt,
        "attempts_allowed": state.attempts_allowed,
        "context_file": str(files.context),
        "edits_file": str(files.edits),
    }


def _host_outcome(state: HostUnitState, warnings: list[str]) -> UnitOutcome:
    """A host unit's end, as the run records units."""
    return UnitOutcome(
        unit_id=state.unit.id,
        succeeded=state.status is HostStatus.SUCCEEDED,
        agent_skipped=state.agent_skipped,
        attempts=list(state.attempts),
        changed_files=list(state.changed_files),
        reason=state.reason,
        summary=state.summary,
        review_notes=list(state.review_notes),
        warnings=list(warnings),
        approvals=list(state.approvals),
        codemods=state.codemods,
    )


class RunEngine:
    """One run's graph and the resources its nodes use. One per engine process."""

    def __init__(
        self,
        session: RunSession,
        store: Store,
        *,
        options: RunOptions,
        llm_factory: LlmFactory | None = None,
    ):
        self.session = session
        self.store = store
        self.options = options
        self.run_id = session.run_id
        self.events = EventWriter(store, self.run_id)
        self.control = StopControl(store, self.run_id, poll_s=options.poll_s)
        guardrails = session.config.guardrails
        self.ledger = UsageLedger(
            max_cost_usd=guardrails.max_run_cost_usd,
            max_tokens=guardrails.max_run_tokens,
            sink=self._record_call,
        )
        for call in store.llm_calls(self.run_id):  # the caps hold across a resume (NFR-7.1)
            total = self.ledger.total
            total.calls += 1
            total.cost_usd = round(total.cost_usd + call.cost_usd, 6)
            total.latency_s = round(total.latency_s + call.latency_s, 3)
            total.usage = total.usage + Usage(
                input_tokens=call.input_tokens,
                output_tokens=call.output_tokens,
                cache_read_tokens=call.cache_read_tokens,
                cache_write_tokens=call.cache_write_tokens,
            )
        if llm_factory is None:
            self.llm = LLMService(session.config.llm, ledger=self.ledger)
        else:
            self.llm = llm_factory(self.ledger)
        self._baseline: Baseline | None = None
        self._index: SymbolIndex | None = None
        self._warnings: dict[str, int] | None = None
        self._tests: set[str] | None = None
        # The standard the build tree is configured with; "" makes the next unit configure.
        self.configured_standard = ""
        self.host_files = HostFiles(session.artifacts.root)  # the waiting host unit's files
        self._agent_action: AgentAction | None = None  # the host command's, used once
        self.notes: list[str] = []
        report = "report"
        self.graph = RunGraph(
            [
                NodeSpec("baseline", self._node_baseline, self._unless_stop("index"),
                         ("index", report)),
                NodeSpec("index", self._node_index, self._unless_stop("plan"), ("plan", report)),
                NodeSpec("plan", self._node_plan, self._unless_stop("plan_gate"),
                         ("plan_gate", report)),
                NodeSpec("plan_gate", self._node_plan_gate, self._unless_stop("next_unit"),
                         ("next_unit", report)),
                NodeSpec("next_unit", self._node_next_unit, self._route_next_unit,
                         ("unit", "final_verify", report)),
                NodeSpec("unit", self._node_unit, self._route_unit,
                         (AGENT_GATE, "unit_gate", "next_unit", report)),
                NodeSpec(AGENT_GATE, self._node_agent, self._route_unit,
                         (AGENT_GATE, "unit_gate", "next_unit", report)),
                NodeSpec("unit_gate", self._node_unit_gate, self._unless_stop("next_unit"),
                         ("next_unit", report)),
                NodeSpec("final_verify", self._node_final_verify, report),
                NodeSpec(report, self._node_report, STOP),
            ],
            first="baseline",
            checkpoints=session.artifacts.checkpoints_db,
            thread=self.run_id,
        )  # fmt: skip

    # ------------------------------------------------------------------ routing

    @staticmethod
    def _unless_stop(next_node: str) -> Callable[[RunState], str]:
        def route(state: RunState) -> str:
            return "report" if state.get("stop") else next_node

        return route

    @staticmethod
    def _route_next_unit(state: RunState) -> str:
        if state.get("stop"):
            return "report"
        return "unit" if state.get("unit") else "final_verify"

    @staticmethod
    def _route_unit(state: RunState) -> str:
        if state.get("stop"):
            return "report"
        if state.get("agent"):
            return AGENT_GATE
        return "unit_gate" if state.get("gate") else "next_unit"

    # ------------------------------------------------------------------ helpers

    @property
    def config(self) -> MigrationConfig:
        return self.session.config

    def _step(self, message: str) -> None:
        self.options.on_step(message)

    def _status(self, status: RunStatus) -> None:
        self.store.set_run_status(self.run_id, status, _now())
        self.events.emit(EventType.RUN_STATUS, status=status.value)

    def _put_unit(self, unit: WorkUnit, status: UnitStatus, note: str | None = None) -> WorkUnit:
        updated = unit.model_copy(update={"status": status, "note": note})
        self.store.put_units(self.run_id, [updated], _now())
        self.events.emit(EventType.UNIT_STATUS, unit_id=unit.id, status=status.value)
        return updated

    def _unit(self, unit_id: str) -> WorkUnit:
        for unit in self.store.units(self.run_id):
            if unit.id == unit_id:
                return unit
        raise KeyError(unit_id)

    def _record_call(self, entry: LedgerEntry) -> None:
        usage = entry.usage
        self.store.add_llm_call(
            LlmCallRecord(
                run_id=self.run_id,
                unit_id=entry.unit_id,
                attempt=entry.attempt,
                role=entry.role,
                provider=entry.provider,
                model=entry.model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_tokens,
                cache_write_tokens=usage.cache_write_tokens,
                cost_usd=entry.cost_usd or 0.0,
                latency_s=entry.latency_s,
                stop_reason=entry.stop_reason.value,
                created_at=_now(),
            )
        )

    def _stop_here(self) -> RunState | None:
        """A node boundary is a safe point: an abort goes to the report; Ctrl-C parks."""
        try:
            self.control.check()
        except StopRequestedError as stop:
            if not stop.abort:
                raise
            return self._aborted(stop)
        return None

    def _aborted(self, stop: StopRequestedError) -> RunState:
        request = stop.request
        if request is not None and request.id is not None:
            self.store.consume_approval(request.id, _now())
        return {
            "stop": "aborted",
            "unit": None,
            "gate": False,
            "agent": False,
            "reason": request.reason if request else None,
        }

    @property
    def baseline(self) -> Baseline:
        if self._baseline is None:
            path = self.session.artifacts.root / "baseline.json"
            self._baseline = Baseline.model_validate_json(path.read_bytes())
        return self._baseline

    @property
    def index(self) -> SymbolIndex:
        if self._index is None:
            self._index = self.session.adapter.index(self.session.ctx)
        return self._index

    def _is_test(self, path: str) -> bool:
        if self._tests is None:
            with IndexStore.open(self.index.path) as store:
                self._tests = {f.path for f in store.files(FileRole.TEST)}
        dirs = list(self.config.project.test_dirs)
        return path in self._tests or (bool(dirs) and PathMatcher(dirs).matches(path))

    @property
    def warnings_map(self) -> dict[str, int]:
        """Warnings per file before the next unit (I-10): the baseline's, updated by every
        committed unit's changed files."""
        if self._warnings is None:
            current = dict(self.baseline.build_warnings)
            for unit in self.store.units(self.run_id):
                result = load_unit_result(self.session.artifacts, unit.id)
                if unit.status is UnitStatus.COMMITTED and result is not None:
                    current.update(result.file_warnings)
            self._warnings = current
        return self._warnings

    def _refresh_standard(self) -> None:
        """After a commit or a rollback the build files may set another standard (D-42)."""
        session = self.session
        standard = build_standard(session.adapter, session.ctx, session.config)
        if standard != session.ctx.lang.standard:
            session.ctx = replace(
                session.ctx, lang=language_config(session.config, standard=standard)
            )

    # ------------------------------------------------------------------ setup nodes

    def _node_baseline(self, state: RunState) -> RunState:
        if (stop := self._stop_here()) is not None:
            return stop
        self._status(RunStatus.BASELINING)
        try:
            baseline = reusable_baseline(self.session, self._step)
        except BaselineError as exc:
            return {"stop": "baseline_failed", "reason": str(exc)}
        write_baseline(self.session, baseline)
        self._baseline = baseline
        self.configured_standard = baseline.standard
        return {"status": RunStatus.BASELINING.value}

    def _node_index(self, state: RunState) -> RunState:
        if (stop := self._stop_here()) is not None:
            return stop
        self._status(RunStatus.INDEXING)
        self._step("index")
        self._index = self.session.adapter.index(self.session.ctx)
        environment = self.store.run_environment(self.run_id)
        environment["tools"] = dict(self._index.meta.tool_versions)
        self.store.set_run_environment(self.run_id, environment)
        return {"status": RunStatus.INDEXING.value}

    def _node_plan(self, state: RunState) -> RunState:
        if (stop := self._stop_here()) is not None:
            return stop
        self._status(RunStatus.PLANNING)
        plan, _ = plan_from(self.session, self.baseline, self.index, step=self._step)
        for warning in plan.warnings:
            self.options.on_warning(warning)
        self.store.put_units(self.run_id, plan.units, _now())
        self.events.emit(
            EventType.STEP_FINISHED,
            step="plan",
            units=len(plan.units),
            pending=len(plan.units_with_status(UnitStatus.PENDING)),
        )
        return {"status": RunStatus.PLANNING.value}

    def _node_plan_gate(self, state: RunState) -> RunState:
        if (stop := self._stop_here()) is not None:
            return stop
        policy = self.config.approvals.plan
        if policy is PlanApproval.AUTO or self.options.yes:
            source = "auto" if policy is PlanApproval.AUTO else "cli --yes"
            self.events.emit(EventType.GATE_DECIDED, gate="plan", decision="approve", source=source)
            self._status(RunStatus.MIGRATING)
            return {"status": RunStatus.MIGRATING.value}
        self._status(RunStatus.AWAITING_PLAN_APPROVAL)
        self.events.emit(EventType.GATE_WAITING, gate="plan")
        answer = int(wait_for_decision({"gate": ApprovalGate.PLAN.value, "run_id": self.run_id}))
        approval = self._approval(answer)
        self.events.emit(
            EventType.GATE_DECIDED,
            gate="plan",
            decision=approval.decision.value,
            source=approval.source,
        )
        if approval.decision is ApprovalDecision.ABORT:
            return {"stop": "aborted", "reason": approval.reason}
        if approval.decision is ApprovalDecision.REJECT:
            return {"stop": "rejected", "reason": approval.reason or "the plan was rejected"}
        self._exclude(approval.exclude)
        self._status(RunStatus.MIGRATING)
        return {"status": RunStatus.MIGRATING.value}

    def _approval(self, approval_id: int) -> ApprovalRecord:
        for approval in self.store.approvals(self.run_id):
            if approval.id == approval_id:
                if approval.consumed_at is None:
                    self.store.consume_approval(approval_id, _now())
                return approval
        raise KeyError(approval_id)

    def _exclude(self, unit_ids: list[str]) -> None:
        units = {u.id: u for u in self.store.units(self.run_id)}
        for unit_id in unit_ids:
            unit = units.get(unit_id)
            if unit is None:
                self.options.on_warning(f"the plan has no unit {unit_id} to exclude")
            elif unit.status is UnitStatus.PENDING:
                self._put_unit(unit, UnitStatus.EXCLUDED, "excluded at the plan gate")

    # ------------------------------------------------------------------ units

    def _node_next_unit(self, state: RunState) -> RunState:
        if (stop := self._stop_here()) is not None:
            return stop
        try:
            self.ledger.check()
        except BudgetExhaustedError as exc:
            return {"stop": "budget_exhausted", "reason": exc.message}
        for unit in self.store.units(self.run_id):
            if unit.status in (UnitStatus.PENDING, UnitStatus.IN_PROGRESS):
                return {
                    "unit": unit.id,
                    "gate": False,
                    "agent": False,
                    "status": RunStatus.MIGRATING.value,
                }
        return {"unit": None, "gate": False, "agent": False}

    def _node_unit(self, state: RunState) -> RunState:
        unit_id = state.get("unit")
        if unit_id is None:
            return {"unit": None, "agent": False}
        if (stop := self._stop_here()) is not None:
            return stop
        unit = self._unit(unit_id)
        session = self.session
        worktree = session.workspace.worktree
        base = session.workspace.base_commit
        recovered = worktree.unit_commits(base).get(unit_id)
        if recovered is not None:
            return self._recover(unit, recovered)
        # A crash may have left a partial attempt, or a host unit's checkpoint commits.
        worktree.rollback(worktree.below(CHECKPOINT_EMAIL, base))
        self.store.delete_attempts(self.run_id, unit_id)
        started = _now()
        unit = self._put_unit(unit, UnitStatus.IN_PROGRESS)
        self._step(f"unit {unit.id}: {', '.join(unit.rules)} on {len(unit.files)} file(s)")
        before = session.workspace.repository.fingerprint()
        try:
            prepared = prepare_unit(
                session,
                rule_ids=list(unit.rules),
                files=list(unit.files),
                step=self._step,
                planned=unit,
                baseline=self.baseline,
                index=self.index,
            )
        except (UsageError, ConfigError) as exc:
            return self._finish_failed(unit, started, f"the unit can't run: {exc.message}")
        host = self.options.host
        runner = make_runner(
            session,
            prepared,
            None if host else self.llm,
            AgentSettings.from_config(
                self.config.guardrails, store_transcripts=self.config.reporting.store_transcripts
            ),
            warnings_before=self.warnings_map,
            guard=self.control.check,
            # The limit measures the unit's model time: a host agent's own time isn't ours.
            deadline=None
            if host
            else time.monotonic() + self.config.guardrails.max_unit_minutes * 60,
        )
        runner.configured_standard = self.configured_standard
        if host:
            return self._start_host_unit(unit, prepared, runner)
        try:
            outcome = runner.run(prepared.unit, prepared.rules)
        except StopRequestedError as stop:
            self._put_unit(unit, UnitStatus.PENDING)
            self.store.delete_attempts(self.run_id, unit_id)
            self._refresh_standard()
            if not stop.abort:
                raise
            return self._aborted(stop)
        except BudgetExhaustedError as exc:
            self._put_unit(unit, UnitStatus.PENDING, "stopped: the run's budget ran out")
            self._refresh_standard()
            return {"stop": "budget_exhausted", "unit": None, "agent": False, "reason": exc.message}
        finally:
            self.configured_standard = runner.configured_standard
        self._record_attempts(unit_id, outcome.attempts)
        changes = before.changes(session.workspace.repository.fingerprint())
        if changes:
            outcome.warnings.append(
                f"your checkout changed while the unit ran ({', '.join(changes)}); "
                "CodeCoreMorph doesn't write there (I-11). If you didn't make these changes, "
                "report a bug"
            )
        if not outcome.succeeded:
            self._refresh_standard()
            return self._finish_failed(
                unit, started, outcome.reason or "the unit failed", outcome=outcome
            )
        return self._finish_succeeded(unit, started, outcome, runner, prepared.rules)

    def _record_attempts(self, unit_id: str, attempts: list[Attempt]) -> None:
        """Store the unit's attempts not stored yet (a host unit's arrive one per command)."""
        known = {a.number for a in self.store.attempts(self.run_id, unit_id)}
        for attempt in attempts:
            if attempt.number in known:
                continue
            self.store.put_attempt(attempt)
            self.events.emit(
                EventType.ATTEMPT_FINISHED,
                unit_id=unit_id,
                attempt=attempt.number,
                outcome=attempt.outcome.value if attempt.outcome else None,
            )

    # ------------------------------------------------------------------ host-agent units

    def _start_host_unit(
        self, unit: WorkUnit, prepared: PreparedUnit, runner: UnitRunner
    ) -> RunState:
        """The unit's codemods and first verification. Unless they finish it, the unit then
        waits for the host agent's edits at the ``agent`` node (D-44)."""
        session = self.session
        state = host_unit_state(
            session,
            prepared,
            runner,
            plan_unit=unit.id,
            configured_standard=runner.configured_standard,
            user_tree=session.workspace.repository.dirty_state(),
            in_run=True,
        )
        host = HostUnit(
            runner,
            prepared.rules,
            self.host_files,
            language=self.config.migration.source.language,
            author=self.config.git.author,
            commits=False,
        )
        try:
            step = host.start(state)
        finally:
            self.configured_standard = runner.configured_standard
        return self._after_host_step(unit, host, step)

    def _node_agent(self, state: RunState) -> RunState:
        """Wait for the host agent; then verify its edits (``codecoremorph unit submit``) or
        give the unit up (``unit abort``). A rejection or a failed attempt waits again."""
        # The command's action is for the unit that waited when the command started, and is
        # used once: a unit that ends here or starts over doesn't pass it on to the next wait.
        # LangGraph replays an interrupt's first answer when the node failed after it, so the
        # answer's value means nothing; the action is the engine's.
        action, self._agent_action = self._agent_action, None
        unit_id = state.get("unit")
        if unit_id is None:
            return {"agent": False}
        unit = self._unit(unit_id)
        session = self.session
        worktree = session.workspace.worktree
        recovered = worktree.unit_commits(session.workspace.base_commit).get(unit_id)
        if recovered is not None:  # the engine stopped between the unit's commit and here
            return self._recover(unit, recovered)
        current = self.host_files.load() if self.host_files.state.is_file() else None
        if current is None or current.unit.id != unit_id or current.status.is_terminal:
            return self._host_unit_lost(unit, current)
        files = self.host_files
        wait_for_decision(agent_request(files, current))
        if action is None:  # a replayed answer, not the agent's command: wait again
            return {"agent": True}
        self.events.emit(EventType.GATE_DECIDED, gate=AGENT_GATE, unit_id=unit_id,
                         decision=action.kind, source="host")  # fmt: skip
        host = resume_host_unit(session, current, warnings_before=self.warnings_map, commits=False)
        if action.kind == "abort":
            worktree.rollback(current.snapshot)
            why = "the agent gave the unit up" + (f": {action.reason}" if action.reason else "")
            ended = current.model_copy(
                update={
                    "status": HostStatus.ABORTED,
                    "reason": why,
                    "head": current.snapshot,
                    "updated_at": _now(),
                }
            )
            files.save(ended)
            message = f"Unit {unit_id} was given up ({why}) and rolled back; the run goes on."
            return self._after_host_step(unit, host, HostStep(ended, message))
        try:
            step = host.submit(current)
        finally:
            self.configured_standard = host.runner.configured_standard
        step.warnings += user_tree_warnings(session, current)
        return self._after_host_step(unit, host, step)

    def _after_host_step(self, unit: WorkUnit, host: HostUnit, step: HostStep) -> RunState:
        """Record a host unit's step; a unit that ended is committed or failed like any other."""
        state = step.state
        self.notes += [f"warning: {w}" for w in step.warnings]
        # A waiting unit's next step is the run's: the command prints it once, at the end.
        waiting = state.status is HostStatus.WAITING
        self.notes.append(f"Unit {unit.id}: {step.message}" if waiting else step.message)
        unit_dir = self.session.artifacts.unit_dir(unit.id)
        unit_dir.mkdir(parents=True, exist_ok=True)
        HostFiles(unit_dir).save(state)  # each unit's last host state, for the record
        self._record_attempts(unit.id, state.attempts)
        if waiting:
            self.events.emit(
                EventType.GATE_WAITING, gate=AGENT_GATE, unit_id=unit.id, attempt=state.attempt
            )
            return {"unit": unit.id, "agent": True, "gate": False}
        outcome = _host_outcome(state, step.warnings)
        if state.status is not HostStatus.SUCCEEDED:
            self._refresh_standard()
            return self._finish_failed(
                unit, state.started_at, state.reason or "the unit failed", outcome=outcome
            )
        update = self._finish_succeeded(unit, state.started_at, outcome, host.runner, host.rules)
        result = load_unit_result(self.session.artifacts, unit.id)
        if result is not None and result.commit is not None:
            done = state.model_copy(update={"commit": result.commit, "head": result.commit})
            self.host_files.save(done)
            HostFiles(unit_dir).save(done)
            self.notes.append(
                f"Unit {unit.id} is commit {result.commit[:12]} on branch "
                f"{self.session.workspace.worktree.branch}."
            )
        return update

    def _host_unit_lost(self, unit: WorkUnit, state: HostUnitState | None) -> RunState:
        """The engine stopped after the host unit ended and before the run recorded it: a
        failure is recorded now; a success whose commit didn't happen runs again."""
        session = self.session
        worktree = session.workspace.worktree
        worktree.rollback(worktree.below(CHECKPOINT_EMAIL, session.workspace.base_commit))
        if (
            state is not None
            and state.unit.id == unit.id
            and state.status
            in (
                HostStatus.FAILED,
                HostStatus.ABORTED,
            )
        ):
            self._record_attempts(unit.id, state.attempts)
            self._refresh_standard()
            return self._finish_failed(
                unit,
                state.started_at,
                state.reason or "the unit failed",
                outcome=_host_outcome(state, []),
            )
        self.store.delete_attempts(self.run_id, unit.id)
        self._put_unit(unit, UnitStatus.PENDING, "restarted: the engine stopped during the unit")
        self.notes.append(
            f"Unit {unit.id} starts over: CodeCoreMorph stopped before it recorded the unit's "
            "result, so the last submission's changes are gone."
        )
        self._refresh_standard()
        return {"unit": None, "gate": False, "agent": False}

    def _finish_failed(
        self,
        unit: WorkUnit,
        started: datetime,
        reason: str,
        *,
        outcome: UnitOutcome | None = None,
    ) -> RunState:
        finished = _now()
        attempts = outcome.attempts if outcome else []
        result = UnitResult(
            unit_id=unit.id,
            status=UnitStatus.FAILED,
            attempts=len(attempts),
            models=_models(attempts),
            reason=reason,
            review_notes=[n for a in attempts for n in a.review_notes],
            warnings=list(outcome.warnings) if outcome else [],
            started_at=started,
            finished_at=finished,
            duration_s=round((finished - started).total_seconds(), 1),
        )
        write_json(unit_result_path(self.session.artifacts, unit.id), result)
        self._put_unit(unit, UnitStatus.FAILED, reason)
        return {"unit": None, "gate": False, "agent": False}

    def _finish_succeeded(
        self,
        unit: WorkUnit,
        started: datetime,
        outcome: UnitOutcome,
        runner: UnitRunner,
        rules: list[LoadedRule],
    ) -> RunState:
        session = self.session
        worktree = session.workspace.worktree
        changed = list(outcome.changed_files)
        codemod_files = (
            [c.file for c in outcome.codemods.changes if c.file in changed]
            if outcome.codemods
            else []
        )
        commit: str | None = None
        file_warnings: dict[str, int] = {}
        if changed:
            summary = outcome.summary or (
                "Deterministic tools completed the unit." if outcome.agent_skipped else ""
            )
            commit = worktree.commit(
                unit_commit_message(
                    run_id=self.run_id,
                    unit_id=unit.id,
                    rules=unit.rules,
                    files=changed,
                    summary=summary,
                    attempts=len(outcome.attempts),
                    models=_models(outcome.attempts),
                    codemod_files=codemod_files,
                ),
                author=self.config.git.author,
            )
            after = runner.invariants.warnings_after if runner.invariants else {}
            file_warnings = {p: after.get(p, 0) for p in changed}
            self._cache_baseline(runner, rules, commit)
        reasons = self._gate_reasons(outcome, changed) if commit else []
        finished = _now()
        status = UnitStatus.AWAITING_APPROVAL if reasons else UnitStatus.COMMITTED
        result = UnitResult(
            unit_id=unit.id,
            status=status,
            commit=commit,
            attempts=len(outcome.attempts),
            agent_skipped=outcome.agent_skipped,
            models=_models(outcome.attempts),
            summary=outcome.summary,
            review_notes=list(outcome.review_notes),
            warnings=list(outcome.warnings),
            approvals=reasons,
            changed_files=changed,
            test_files_changed=[p for p in changed if self._is_test(p)],
            codemod_files=codemod_files,
            remaining_sites={
                rule: len(sites) for rule, sites in runner.last_sites.items() if sites
            },
            file_warnings=file_warnings,
            started_at=started,
            finished_at=finished,
            duration_s=round((finished - started).total_seconds(), 1),
        )
        write_json(unit_result_path(session.artifacts, unit.id), result)
        if status is UnitStatus.COMMITTED:
            self.warnings_map.update(file_warnings)
        self._put_unit(unit, status, "waiting for approval" if reasons else None)
        self._refresh_standard()
        return {"unit": unit.id if reasons else None, "gate": bool(reasons), "agent": False}

    def _cache_baseline(self, runner: UnitRunner, rules: list[LoadedRule], commit: str) -> None:
        """The unit's last verification ran the tests on its commit: a unit or run started
        from that commit reuses them (D-42)."""
        report = runner.last_tests
        if report is None:
            return
        lang = runner.verify_context(rules).lang
        baseline = unit_baseline(
            self.baseline, report, commit=commit, standard=lang.standard, run_id=self.run_id
        )
        baseline_cache(self.session).put(
            baseline, sandbox=self.session.executor.kind, lang=lang, run_id=self.run_id
        )

    def _gate_reasons(self, outcome: UnitOutcome, changed: list[str]) -> list[str]:
        """Why the unit gate must ask, under ``approvals.unit`` (FR-10.1)."""
        policy = self.config.approvals.unit
        reasons: list[str] = []
        if policy in (UnitApproval.API_CHANGES, UnitApproval.ALL):
            reasons += outcome.approvals
        if policy in (UnitApproval.TEST_CHANGES, UnitApproval.ALL):
            tests = [p for p in changed if self._is_test(p)]
            if tests:
                reasons.append(f"test files changed: {', '.join(tests)}")
        if policy is UnitApproval.ALL and not reasons:
            reasons.append("every unit needs approval (approvals.unit: all)")
        return reasons

    def _recover(self, unit: WorkUnit, commit: str) -> RunState:
        """The unit committed before the engine stopped, and its checkpoint didn't record it."""
        result = load_unit_result(self.session.artifacts, unit.id)
        if result is None or result.commit != commit:
            now = _now()
            result = UnitResult(
                unit_id=unit.id,
                status=UnitStatus.COMMITTED,
                commit=commit,
                warnings=[
                    "recovered after a restart: the unit committed before the engine stopped, "
                    "and its details weren't saved"
                ],
                started_at=now,
                finished_at=now,
                duration_s=0,
            )
            write_json(unit_result_path(self.session.artifacts, unit.id), result)
        waiting = bool(result.approvals) and result.approved_by is None
        status = UnitStatus.AWAITING_APPROVAL if waiting else UnitStatus.COMMITTED
        self._put_unit(unit, status, "waiting for approval" if waiting else None)
        self._refresh_standard()
        return {"unit": unit.id if waiting else None, "gate": waiting, "agent": False}

    def _node_unit_gate(self, state: RunState) -> RunState:
        unit_id = state.get("unit")
        if unit_id is None:
            return {"gate": False}
        result = load_unit_result(self.session.artifacts, unit_id)
        self.events.emit(EventType.GATE_WAITING, gate="unit", unit_id=unit_id)
        answer = int(
            wait_for_decision(
                {
                    "gate": ApprovalGate.UNIT.value,
                    "run_id": self.run_id,
                    "unit": unit_id,
                    "reasons": list(result.approvals) if result else [],
                    "commit": result.commit if result else None,
                }
            )
        )
        approval = self._approval(answer)
        self.events.emit(
            EventType.GATE_DECIDED,
            gate="unit",
            unit_id=unit_id,
            decision=approval.decision.value,
            source=approval.source,
        )
        unit = self._unit(unit_id)
        if approval.decision is ApprovalDecision.APPROVE:
            if result is not None:
                approved = result.model_copy(
                    update={"status": UnitStatus.COMMITTED, "approved_by": approval.source}
                )
                write_json(unit_result_path(self.session.artifacts, unit_id), approved)
                self.warnings_map.update(result.file_warnings)
            self._put_unit(unit, UnitStatus.COMMITTED)
            return {"unit": None, "gate": False}
        self._reject_unit(unit, result, approval)
        if approval.decision is ApprovalDecision.ABORT:
            return {"stop": "aborted", "unit": None, "gate": False, "reason": approval.reason}
        return {"unit": None, "gate": False}

    def _reject_unit(
        self, unit: WorkUnit, result: UnitResult | None, approval: ApprovalRecord
    ) -> None:
        """Take a gated unit's commit back off the branch."""
        session = self.session
        reject_unit(
            session.workspace.worktree, session.artifacts, self.store, unit, result, approval
        )
        self._refresh_standard()

    # ------------------------------------------------------------------ the end

    def _node_final_verify(self, state: RunState) -> RunState:
        if (stop := self._stop_here()) is not None:
            return stop
        session = self.session
        head = session.workspace.worktree.head()
        path = session.artifacts.root / FINAL_FILE
        if head == session.workspace.base_commit:
            write_json(path, FinalVerification(ran=False, reason="no unit changed the code"))
            return {"verified": None}
        self._status(RunStatus.VERIFYING)
        self._step("final verification: a clean build and every test")
        started = time.monotonic()
        adapter, ctx = session.adapter, session.ctx
        clean = True
        try:
            adapter.clean_build(ctx)
        except AdapterNotSupportedError:
            clean = False
        final = FinalVerification(ran=True, commit=head, clean=clean)
        configured = adapter.configure(ctx)
        built = adapter.build(ctx) if configured.success else configured
        if not built.success:
            errors = [
                f"{d.file_path}:{d.line}: {d.message}" if d.file_path else d.message
                for d in built.errors[:20]
            ]
            final = final.model_copy(
                update={"build_errors": errors, "reason": "the branch doesn't build"}
            )
        else:
            report = adapter.run_tests(ctx)
            statuses = {c.test_id: c.status for c in report.cases}
            regressions = [
                t for t in self.baseline.tests.passing if statuses.get(t) is not TestStatus.PASSED
            ]
            reason = (
                f"{len(regressions)} baseline-passing test(s) don't pass" if regressions else None
            )
            final = final.model_copy(
                update={
                    "passed": not regressions,
                    "tests_run": len(report.cases),
                    "regressions": regressions,
                    "reason": reason,
                }
            )
        self.configured_standard = ctx.lang.standard
        final = final.model_copy(update={"duration_s": round(time.monotonic() - started, 1)})
        write_json(path, final)
        return {"verified": final.passed}

    def _final_status(self, state: RunState) -> RunStatus:
        stop = state.get("stop")
        if stop:
            return _STOP_STATUS.get(stop, RunStatus.FAILED)
        if state.get("verified") is False:
            return RunStatus.FAILED
        if any(u.status in _LEFT_OUT for u in self.store.units(self.run_id)):
            return RunStatus.COMPLETED_WITH_FAILURES
        return RunStatus.COMPLETED

    def _node_report(self, state: RunState) -> RunState:
        status = self._final_status(state)
        reason = state.get("reason")
        if status is RunStatus.FAILED and not reason and state.get("verified") is False:
            reason = "the final verification failed"
        finished = _now()
        self.store.set_run_status(self.run_id, status, finished)
        self.events.emit(EventType.RUN_STATUS, status=status.value, reason=reason)
        session = self.session
        finish_report(
            self.store,
            session.artifacts,
            self.run_id,
            project=self.config.project.name,
            branch=session.workspace.worktree.branch,
            head=session.workspace.worktree.head(),
            status=status,
            reason=reason,
            finished=finished,
        )
        return {"status": status.value, "reason": reason}

    # ------------------------------------------------------------------ driving

    def drive(self, *, fresh: bool, agent: AgentAction | None = None) -> RunResult:
        """Run the graph until it ends or parks, answering gates as they come. ``agent`` is a
        host agent's ``unit submit`` or ``unit abort`` for the unit that waits for it."""
        worktree = self.session.workspace.worktree
        if agent is not None:
            pending = None if fresh else self.graph.pending()
            if pending is None or pending.get("gate") != AGENT_GATE:
                where = f"at its {pending['gate']} gate" if pending else "for nothing"
                raise UsageError(
                    f"run {self.run_id} doesn't wait for a unit's edits: it waits {where}",
                    hint=f"`codecoremorph status {self.run_id}` shows what it waits for.",
                )
        self._agent_action = agent
        if not fresh:
            # Discard a unit step the engine didn't finish; a host unit's checkpoints stay.
            worktree.rollback(worktree.head())
        try:
            with self.control.catching_ctrl_c(self.options.on_stop_requested):
                if fresh:
                    outcome = self.graph.start({"status": RunStatus.CREATED.value})
                else:
                    waiting = self.graph.pending()
                    if waiting is not None:
                        outcome = self.graph.resume(self._decide(waiting))
                    else:
                        outcome = self.graph.proceed()
                while outcome.waiting is not None:
                    outcome = self.graph.resume(self._decide(outcome.waiting))
        except _Park as park:
            return self._parked(park.request, awaiting=True)
        except (StopRequestedError, KeyboardInterrupt):
            self.events.emit(EventType.LOG, message="the engine stopped; the run is parked")
            return self._parked(self.graph.pending(), awaiting=False)
        state = outcome.state
        artifacts = self.session.artifacts
        return RunResult(
            run_id=self.run_id,
            status=RunStatus(state.get("status", RunStatus.FAILED.value)),
            reason=state.get("reason"),
            branch=worktree.branch,
            head=worktree.head(),
            report_md=artifacts.report_md,
            report_json=artifacts.report_json,
            baseline_failed=state.get("stop") == "baseline_failed",
            notes=tuple(self.notes),
        )

    def _parked(self, waiting: dict[str, Any] | None, *, awaiting: bool) -> RunResult:
        worktree = self.session.workspace.worktree
        record = self.store.get_run(self.run_id)
        return RunResult(
            run_id=self.run_id,
            status=record.status if record else RunStatus.MIGRATING,
            reason=None if awaiting else "stopped",
            branch=worktree.branch,
            head=worktree.head(),
            parked=True,
            waiting=waiting,
            awaiting=awaiting,
            notes=tuple(self.notes),
        )

    def _decide(self, request: dict[str, Any]) -> int | str:
        """The ID of the approval that answers ``request``: one already in the store, the
        user's answer to the prompt, or one written while the engine waits (FR-11.4). A host
        agent's run parks instead of waiting; the unit waiting for the agent's edits goes on
        only with the agent's command."""
        if request.get("gate") == AGENT_GATE:
            if self._agent_action is None:
                raise _Park(request)
            return AGENT_GATE  # ignored: the node takes the agent's action from the engine
        gate = ApprovalGate(str(request["gate"]))
        unit_id = request.get("unit")
        asked = False
        while True:
            found = self._answer(gate, unit_id if isinstance(unit_id, str) else None)
            if found is not None:
                return found
            if gate is ApprovalGate.PLAN and self.options.yes:  # `resume --yes` at the gate
                stored = self.store.add_approval(
                    ApprovalRecord(
                        run_id=self.run_id,
                        gate=gate,
                        decision=ApprovalDecision.APPROVE,
                        source="cli --yes",
                        created_at=_now(),
                    )
                )
                if stored.id is not None:
                    return stored.id
            if self.options.host:  # the host agent asks the user, then runs `resume`
                raise _Park(request)
            if self.options.ask is not None and not asked:
                asked = True
                answer = self.options.ask(request)
                if answer is None:
                    raise StopRequestedError("stopped at the gate", abort=False)
                stored = self.store.add_approval(answer)
                if stored.id is None:  # the store always assigns one
                    raise RuntimeError("the store didn't assign an approval ID")
                return stored.id
            if self.control.interrupted:
                raise StopRequestedError("stopped at the gate", abort=False)
            time.sleep(self.options.poll_s)

    def _answer(self, gate: ApprovalGate, unit_id: str | None) -> int | None:
        for approval in self.store.approvals(self.run_id, pending=True):
            if approval.id is None:
                continue
            if approval.gate is ApprovalGate.RUN and approval.decision is ApprovalDecision.ABORT:
                return approval.id
            if approval.gate is not gate:
                continue
            if gate is ApprovalGate.UNIT and approval.unit_id not in (None, unit_id):
                continue
            return approval.id
        return None


# --------------------------------------------------------------------------- shared steps


def reject_unit(
    worktree: Worktree,
    artifacts: RunArtifacts,
    store: Store,
    unit: WorkUnit,
    result: UnitResult | None,
    approval: ApprovalRecord,
) -> None:
    """Take a gated unit's commit back off the branch; the unit fails. Needs no sandbox."""
    run_id = approval.run_id
    if result is not None and result.commit is not None and worktree.head() == result.commit:
        worktree.rollback(worktree.parent(result.commit))
    why = "rejected at the unit gate" + (f": {approval.reason}" if approval.reason else "")
    if approval.decision is ApprovalDecision.ABORT:
        why = "the run was aborted while the unit waited for approval"
    if result is not None:
        write_json(
            unit_result_path(artifacts, unit.id),
            result.model_copy(update={"status": UnitStatus.FAILED, "reason": why, "commit": None}),
        )
    attempts = store.attempts(run_id, unit.id)
    if attempts:
        store.put_attempt(attempts[-1].model_copy(update={"outcome": AttemptOutcome.GATE_REJECTED}))
    store.put_units(
        run_id, [unit.model_copy(update={"status": UnitStatus.FAILED, "note": why})], _now()
    )
    EventWriter(store, run_id).emit(
        EventType.UNIT_STATUS, unit_id=unit.id, status=UnitStatus.FAILED.value
    )


def finish_report(
    store: Store,
    artifacts: RunArtifacts,
    run_id: str,
    *,
    project: str,
    branch: str,
    head: str | None,
    status: RunStatus,
    reason: str | None,
    finished: datetime | None,
) -> tuple[Path, Path]:
    report = build_report(
        store,
        artifacts,
        run_id,
        project=project,
        branch=branch,
        head=head,
        status=status,
        reason=reason,
        finished_at=finished,
    )
    return write_report(report, artifacts)


def new_run_record(session: RunSession) -> RunRecord:
    now = _now()
    return RunRecord(
        id=session.run_id,
        status=RunStatus.CREATED,
        created_at=now,
        updated_at=now,
        repo_root=session.workspace.repository.root,
        base_commit=session.workspace.base_commit,
        config_hash=hashlib.sha256(session.config_path.read_bytes()).hexdigest(),
        codecoremorph_version=__version__,
    )


def run_environment(
    session: RunSession, llm: LLMService, *, host: bool = False
) -> dict[str, JsonValue]:
    """What the run runs with (NFR-6.1); the index node adds the tool versions. A host-agent
    run (D-44) records ``agent: host`` and no model routes, since it calls no model."""
    routes: dict[str, JsonValue] = {role.value: llm.route(role).name for role in Role}
    found: dict[str, JsonValue] = {
        "codecoremorph": __version__,
        "config_path": str(session.config_path.resolve()),
        "sandbox": session.executor.kind,
        "agent": "host" if host else "model",
        "prompts": PROMPTS_VERSION,
        "build_standard": session.ctx.lang.standard,
    }
    if not host:
        found["models"] = routes
    image = getattr(getattr(session.executor, "settings", None), "image", None)
    if isinstance(image, str):
        found["image"] = image
    return found
