"""What a run produced: per-unit results, the final verification and the report (FR-13.1).

The engine writes a :class:`UnitResult` per finished unit and a :class:`FinalVerification`;
``codecoremorph.report`` turns them, with the store's records, into a :class:`RunReport`
(``report.json``) and its Markdown rendering (``report.md``).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, Field, JsonValue

from codecoremorph.core.models.base import CoreModel, RunId, UnitId
from codecoremorph.core.models.planning import UnitStatus
from codecoremorph.core.models.runs import RunStatus


class UnitResult(CoreModel):
    """How a unit ended. Written to ``attempts/<unit-id>/result.json``."""

    unit_id: UnitId
    status: UnitStatus
    commit: str | None = Field(default=None, description="The unit's commit on the run branch")
    attempts: int = Field(default=0, ge=0)
    agent_skipped: bool = False
    models: list[str] = Field(default_factory=list[str])
    summary: str | None = None
    reason: str | None = Field(default=None, description="Why the unit failed or was skipped")
    review_notes: list[str] = Field(default_factory=list[str])
    warnings: list[str] = Field(default_factory=list[str])
    approvals: list[str] = Field(
        default_factory=list[str], description="Why the unit needed a human's approval"
    )
    approved_by: str | None = None
    changed_files: list[str] = Field(default_factory=list[str])
    test_files_changed: list[str] = Field(default_factory=list[str])
    codemod_files: list[str] = Field(default_factory=list[str])
    remaining_sites: dict[str, int] = Field(
        default_factory=dict[str, int], description="Rule sites left after the unit, per rule"
    )
    file_warnings: dict[str, int] = Field(
        default_factory=dict[str, int],
        description="Distinct compiler warnings in the changed files after the unit (I-10)",
    )
    started_at: AwareDatetime
    finished_at: AwareDatetime
    duration_s: float = Field(ge=0)


class FinalVerification(CoreModel):
    """The clean build and full test run on the branch's last commit (SPEC §7.5)."""

    ran: bool
    passed: bool = False
    commit: str | None = None
    clean: bool = Field(default=False, description="The build started from an empty build tree")
    build_errors: list[str] = Field(default_factory=list[str])
    tests_run: int = Field(default=0, ge=0)
    regressions: list[str] = Field(
        default_factory=list[str], description="Baseline-passing tests that don't pass"
    )
    reason: str | None = None
    duration_s: float = Field(default=0.0, ge=0)


class ReviewKind(StrEnum):
    FAILED_UNIT = "failed_unit"
    SKIPPED_UNIT = "skipped_unit"
    API_CHANGE = "api_change"
    TEST_EDIT = "test_edit"
    AGENT_NOTE = "agent_note"
    WARNING = "warning"
    VERIFICATION = "verification"


class ReviewItem(CoreModel):
    """Something a human should look at, gated or not (FR-10.4)."""

    kind: ReviewKind
    unit_id: UnitId | None = None
    text: str


class UsageTotals(CoreModel):
    calls: int = 0
    tokens: int = 0
    cost_usd: float = 0.0
    latency_s: float = 0.0


class RuleSummary(CoreModel):
    id: str
    units: int = 0
    committed: int = 0
    planned_sites: int = 0
    converted_sites: int = Field(
        default=0, description="Planned sites of committed units, minus the sites left"
    )


class RunReport(CoreModel):
    """``report.json`` (FR-13.1, FR-13.2)."""

    run_id: RunId
    project: str
    status: RunStatus
    reason: str | None = None
    language: str
    source_standard: str
    target_standard: str
    base_commit: str
    branch: str
    head: str | None = Field(default=None, description="The branch's last commit")
    started_at: AwareDatetime
    finished_at: AwareDatetime | None = None
    duration_s: float | None = None
    unit_counts: dict[str, int] = Field(default_factory=dict[str, int])
    units: list[UnitResult] = Field(default_factory=list[UnitResult])
    unit_notes: dict[str, str] = Field(
        default_factory=dict[str, str], description="Why units without a result were left out"
    )
    rules: list[RuleSummary] = Field(default_factory=list[RuleSummary])
    baseline_tests: dict[str, int] = Field(default_factory=dict[str, int])
    final: FinalVerification | None = None
    usage_by_role: dict[str, UsageTotals] = Field(default_factory=dict[str, UsageTotals])
    usage_total: UsageTotals = Field(default_factory=UsageTotals)
    providers: list[str] = Field(
        default_factory=list[str], description="Providers that received code (FR-8.5)"
    )
    review: list[ReviewItem] = Field(default_factory=list[ReviewItem])
    environment: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])
