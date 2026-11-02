"""Work units and plans (SPEC §7.5.1, §9)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from codecoremorph.core.models.base import CoreModel, UnitId, WorktreePath
from codecoremorph.core.models.rules import RuleKind


class UnitStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    AWAITING_APPROVAL = "awaiting_approval"
    COMMITTED = "committed"
    FAILED = "failed"
    SKIPPED = "skipped"
    TOO_LARGE = "too_large"
    EXCLUDED = "excluded"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_UNIT_STATUSES


_TERMINAL_UNIT_STATUSES = frozenset(
    {
        UnitStatus.COMMITTED,
        UnitStatus.FAILED,
        UnitStatus.SKIPPED,
        UnitStatus.TOO_LARGE,
        UnitStatus.EXCLUDED,
    }
)


class UnitFlag(StrEnum):
    BOUNDARY = "boundary"
    PUBLIC_API = "public_api"
    C_COMPATIBLE = "c_compatible"
    TOUCHES_TESTS = "touches_tests"
    TOO_LARGE = "too_large"


class UnitEstimate(CoreModel):
    context_tokens: int = Field(ge=0, description="The unit's context for the editor model")
    expected_attempts: float = Field(
        ge=0, description="Expected agent attempts; below 1 when codemods may finish the unit"
    )
    tokens: int = Field(default=0, ge=0, description="Expected tokens over every LLM call")
    cost_usd: float | None = Field(
        default=None, ge=0, description="None for free providers or unknown prices"
    )


class WorkUnit(CoreModel):
    """The smallest set of files changed and verified together; one commit when it succeeds."""

    id: UnitId
    seq: int = Field(ge=1, description="Position in the plan, 1-based")
    rules: list[str] = Field(min_length=1)
    files: list[WorktreePath] = Field(min_length=1)
    rule_sites: dict[str, int] = Field(default_factory=dict[str, int])
    flags: list[UnitFlag] = Field(default_factory=list[UnitFlag])
    status: UnitStatus = UnitStatus.PENDING
    estimate: UnitEstimate | None = None
    note: str | None = None

    @field_validator("flags")
    @classmethod
    def _canonical_flags(cls, flags: list[UnitFlag]) -> list[UnitFlag]:
        # Sorted and unique, so identical plans serialize identically (FR-5.6).
        return sorted(set(flags))


class PlanRule(CoreModel):
    """One rule of the plan, with its detector results (FR-5.5)."""

    id: str
    kind: RuleKind
    automatic: bool = Field(
        default=False, description="Runs without being listed: the standard bump (D-38)"
    )
    sites: int | None = Field(
        default=None, description="Detector sites; None when the rule has no detector"
    )
    files: int = Field(default=0, ge=0, description="Files the rule applies to")
    skipped_files: int = Field(default=0, ge=0, description="Excluded by the rule's skip list")
    units: int = Field(default=0, ge=0)


class PlanGraph(CoreModel):
    """The dependency graph the plan was ordered by (FR-2.1)."""

    files: int = Field(ge=0)
    edges: int = Field(ge=0)
    clusters: int = Field(ge=0)
    cyclic_clusters: int = Field(ge=0, description="Clusters condensed from a cycle")
    largest_cluster: int = Field(ge=0, description="Files in the largest cluster")


class PlanEstimate(CoreModel):
    model: str = Field(description='The editor model, "<provider>/<model>"')
    context_window: int | None = Field(default=None, description="None when unknown")
    unit_token_limit: int = Field(ge=1, description="A unit's context may not exceed this")
    tokens: int = Field(ge=0)
    cost_usd: float | None = Field(default=None, ge=0, description="None when not priced")


class Plan(CoreModel):
    """The ordered list of work units. Deterministic for identical inputs (FR-5.6): it holds
    no timestamps, run IDs, absolute paths or measured durations."""

    language: str | None = None
    source_standard: str | None = None
    target_standard: str | None = None
    base_commit: str | None = None
    rules: list[PlanRule] = Field(default_factory=list[PlanRule])
    graph: PlanGraph | None = None
    units: list[WorkUnit] = Field(default_factory=list[WorkUnit])
    estimate: PlanEstimate | None = None
    warnings: list[str] = Field(default_factory=list[str])

    @model_validator(mode="after")
    def _check_order(self) -> Plan:
        ids = [u.id for u in self.units]
        if len(set(ids)) != len(ids):
            raise ValueError("unit IDs in a plan must be unique")
        seqs = [u.seq for u in self.units]
        if seqs != sorted(seqs) or len(set(seqs)) != len(seqs):
            raise ValueError("units must be listed in strictly increasing seq order")
        return self

    def unit(self, unit_id: str) -> WorkUnit:
        for u in self.units:
            if u.id == unit_id:
                return u
        raise KeyError(unit_id)

    @property
    def total_estimated_cost_usd(self) -> float:
        return sum(u.estimate.cost_usd or 0.0 for u in self.units if u.estimate)

    def units_with_status(self, status: UnitStatus) -> list[WorkUnit]:
        return [u for u in self.units if u.status is status]
