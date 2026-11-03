"""Run records and run status (SPEC §9)."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import AwareDatetime, Field

from codecoremorph.core.models.base import CoreModel, Role, RunId, UnitId


class RunStatus(StrEnum):
    CREATED = "created"
    PROVISIONING = "provisioning"
    INDEXING = "indexing"
    BASELINING = "baselining"
    CHARACTERIZING = "characterizing"
    PLANNING = "planning"
    AWAITING_PLAN_APPROVAL = "awaiting_plan_approval"
    MIGRATING = "migrating"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    COMPLETED_WITH_FAILURES = "completed_with_failures"
    BUDGET_EXHAUSTED = "budget_exhausted"
    ABORTED = "aborted"
    REJECTED = "rejected"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_RUN_STATUSES


_TERMINAL_RUN_STATUSES = frozenset(
    {
        RunStatus.COMPLETED,
        RunStatus.COMPLETED_WITH_FAILURES,
        RunStatus.BUDGET_EXHAUSTED,
        RunStatus.ABORTED,
        RunStatus.REJECTED,
        RunStatus.FAILED,
    }
)


class RunRecord(CoreModel):
    """What the store keeps about a run (NFR-6.1 fields grow with later phases)."""

    id: RunId
    status: RunStatus = RunStatus.CREATED
    created_at: AwareDatetime
    updated_at: AwareDatetime
    repo_root: Path
    base_commit: str | None = None
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    codecoremorph_version: str


class ApprovalGate(StrEnum):
    PLAN = "plan"
    UNIT = "unit"
    RUN = "run"  # an abort request (D-27)


class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    ABORT = "abort"


class ApprovalRecord(CoreModel):
    """A decision written by the CLI or the dashboard, which the engine acts on (SPEC §7.12).
    At the plan gate, ``exclude`` lists units to leave out (FR-5.8)."""

    id: int | None = Field(default=None, description="Assigned by the store")
    run_id: RunId
    gate: ApprovalGate
    unit_id: UnitId | None = None
    decision: ApprovalDecision
    reason: str | None = None
    exclude: list[UnitId] = Field(default_factory=list[str])
    source: str = Field(min_length=1, description='Who decided: "cli", "dashboard", "auto"')
    created_at: AwareDatetime
    consumed_at: AwareDatetime | None = None


class LlmCallRecord(CoreModel):
    """One model call in the usage ledger (FR-8.3), stored so caps hold across a resume."""

    run_id: RunId
    unit_id: UnitId | None = None
    attempt: int | None = Field(default=None, ge=1)
    role: Role
    provider: str
    model: str
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0)
    latency_s: float = Field(ge=0)
    stop_reason: str | None = None
    created_at: AwareDatetime

    @property
    def tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )
