"""Run records and run status (SPEC §9)."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import AwareDatetime, Field

from codecoremorph.core.models.base import CoreModel, RunId


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
