"""Attempts at a work unit and their safety-invariant results (SPEC §7.10, §9)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, Field

from codecoremorph.core.models.base import CoreModel, Role, RunId, UnitId, WorktreePath
from codecoremorph.core.models.verification import Diagnostic, Severity


class AttemptOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    EDITS_REJECTED = "edits_rejected"
    NO_SUBMISSION = "no_submission"
    REFUSED = "refused"
    BUILD_FAILED = "build_failed"
    TESTS_FAILED = "tests_failed"
    INVARIANT_FAILED = "invariant_failed"
    GATE_REJECTED = "gate_rejected"
    BUDGET_EXHAUSTED = "budget_exhausted"
    TIMED_OUT = "timed_out"
    ABORTED = "aborted"
    ERROR = "error"


class InvariantResult(CoreModel):
    invariant: str = Field(pattern=r"^I-\d+$", description='Invariant ID, e.g. "I-3"')
    passed: bool
    severity: Severity = Severity.ERROR
    message: str = ""


class Attempt(CoreModel):
    """One try at a unit: codemods, agent edits, verification."""

    run_id: RunId
    unit_id: UnitId
    number: int = Field(ge=1)
    role: Role
    model: str | None = Field(default=None, description='"<provider>/<model>"; None if no LLM ran')
    started_at: AwareDatetime
    finished_at: AwareDatetime | None = None
    outcome: AttemptOutcome | None = Field(default=None, description="None while running")
    changed_files: list[WorktreePath] = Field(default_factory=list[str])
    diff_lines: int | None = Field(default=None, ge=0)
    diagnostics: list[Diagnostic] = Field(default_factory=list[Diagnostic])
    regressions: list[str] = Field(
        default_factory=list[str], description="IDs of baseline-passing tests that now fail"
    )
    invariant_results: list[InvariantResult] = Field(default_factory=list[InvariantResult])
    summary: str | None = None
    review_notes: list[str] = Field(default_factory=list[str])
