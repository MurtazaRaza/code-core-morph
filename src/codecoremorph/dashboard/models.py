"""What the dashboard API returns (SPEC §7.13). The views are built from the core models the
engine writes; the browser's TypeScript types in ``dashboard-ui/src/types.ts`` mirror them."""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, Field, JsonValue

from codecoremorph.core.models import (
    ApprovalRecord,
    Attempt,
    CoreModel,
    Event,
    Plan,
    RunStatus,
    UnitResult,
    UnitStatus,
    WorkUnit,
)


class SessionInfo(CoreModel):
    project: str
    repository: str
    config_path: str
    version: str


class RunSummary(CoreModel):
    id: str
    status: RunStatus
    created_at: AwareDatetime
    updated_at: AwareDatetime
    base_commit: str | None
    branch: str
    unit_counts: dict[str, int]
    waiting: list[str] = Field(description='What waits for a decision: "plan", or unit IDs')
    engine_alive: bool = Field(description="An engine process is driving the run right now")
    agent: str | None = Field(description='"host" (a coding agent makes the edits) or "model"')
    sandbox: str | None


class Budget(CoreModel):
    tokens: int
    cost_usd: float
    max_tokens: int | None
    max_cost_usd: float | None


class Activity(CoreModel):
    ts: AwareDatetime
    message: str


class RunDetail(RunSummary):
    language: str | None
    source_standard: str | None
    target_standard: str | None
    units_total: int
    units_done: int
    current_unit: str | None
    activity: Activity | None = Field(description="The engine's latest step")
    agent_waiting: dict[str, JsonValue] | None = Field(
        description="The unit or job waiting for its host agent's edits (D-44)"
    )
    budget: Budget
    plan_ready: bool
    graph_ready: bool
    report_ready: bool
    events: list[Event] = Field(description="The latest events, oldest first")
    last_event_id: int


class PlanView(CoreModel):
    plan: Plan = Field(description="plan.json, with each unit's status from the store")
    gate_open: bool = Field(description="A plan decision can still be given")
    pending: ApprovalRecord | None = Field(description="A decision the engine hasn't acted on")


class GraphFileView(CoreModel):
    path: str
    cluster: int
    units: list[str]
    status: UnitStatus | None = Field(description="The most pressing status of its units")


class GraphUnitView(CoreModel):
    id: str
    seq: int
    status: UnitStatus
    rules: list[str]
    files: list[str]


class GraphView(CoreModel):
    files: list[GraphFileView]
    edges: list[tuple[str, str]] = Field(description="(file, a file it depends on)")
    units: list[GraphUnitView]
    unit_edges: list[tuple[str, str]] = Field(description="(unit, a unit it depends on)")


class UnitGate(CoreModel):
    waiting: bool
    reasons: list[str]
    commit: str | None
    pending: ApprovalRecord | None


class AttemptFiles(CoreModel):
    number: int
    files: list[str] = Field(description="The files kept in the attempt's directory")


class UnitView(CoreModel):
    unit: WorkUnit
    result: UnitResult | None
    attempts: list[Attempt]
    attempt_files: list[AttemptFiles]
    gate: UnitGate


class TextFile(CoreModel):
    name: str
    text: str
    size: int = Field(description="The file's full size in bytes")
    truncated: bool = Field(description="Only the end of the file is included")


class PortFile(CoreModel):
    path: str
    text: str
    truncated: bool = Field(default=False, description="Only the file's start is included")


class PortView(CoreModel):
    """A module port's unit (D-58) in two halves: its C++ as it was before the unit, and the
    code in the target language the unit committed."""

    unit_id: str
    crate: str
    module: str
    build_target: str
    before: str | None = Field(description="The commit the C++ comes from")
    after: str | None = Field(description="The unit's commit; None until it has one")
    sources: list[PortFile]
    targets: list[PortFile]


class AttemptView(CoreModel):
    unit_id: str
    number: int
    attempt: Attempt | None
    diff: TextFile | None
    feedback: TextFile | None
    build_log: TextFile | None
    test_log: TextFile | None
    transcript: list[dict[str, JsonValue]] | None
    transcript_truncated: bool = False
    files: list[str]


class DecisionBody(CoreModel):
    decision: Literal["approve", "reject"]
    reason: str | None = Field(default=None, max_length=2000)
    exclude: list[str] = Field(default_factory=list[str], max_length=1000)


class AbortBody(CoreModel):
    reason: str | None = Field(default=None, max_length=2000)


class ActionResult(CoreModel):
    run_id: str
    approval_id: int | None
    engine_alive: bool
    message: str


class ErrorBody(CoreModel):
    error: str
    reason: str | None = None
    hint: str | None = None


__all__ = [
    "AbortBody",
    "ActionResult",
    "Activity",
    "AttemptFiles",
    "AttemptView",
    "Budget",
    "DecisionBody",
    "ErrorBody",
    "GraphFileView",
    "GraphUnitView",
    "GraphView",
    "PlanView",
    "RunDetail",
    "RunSummary",
    "SessionInfo",
    "TextFile",
    "UnitGate",
    "UnitView",
]
