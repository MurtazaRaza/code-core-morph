"""The baseline: build and test results on the base commit (FR-7.1, SPEC §3)."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from pydantic import Field

from codecoremorph.core.models.base import CoreModel


class TestBaseline(CoreModel):
    """Test results over ``parity.baseline_runs`` runs. Only ``passing`` tests count for parity;
    flaky and failing tests are excluded from it and reported."""

    __test__: ClassVar[bool] = False  # not a pytest test class

    runs: int = Field(ge=1)
    passing: list[str] = Field(default_factory=list[str], description="Passed in every run")
    failing: list[str] = Field(
        default_factory=list[str], description="Failed, errored or timed out in every run"
    )
    flaky: list[str] = Field(
        default_factory=list[str], description="Different results between runs, or missing"
    )
    skipped: list[str] = Field(default_factory=list[str], description="Skipped in every run")

    @property
    def total(self) -> int:
        return len(self.passing) + len(self.failing) + len(self.flaky) + len(self.skipped)


class Baseline(CoreModel):
    """What the base commit does before any change. Stored as ``baseline.json`` in the run's
    directory; parity (FR-4.3) and the warnings invariant (I-10) compare against it."""

    base_commit: str
    standard: str = Field(description="The standard the baseline was built with")
    tests: TestBaseline
    build_warnings: dict[str, int] = Field(
        default_factory=dict[str, int], description="Warnings per worktree file"
    )
    configure_s: float = Field(ge=0)
    build_s: float = Field(ge=0)
    test_s: float = Field(ge=0, description="Mean duration of one test run")
    logs: dict[str, Path] = Field(default_factory=dict[str, Path], description="Raw logs by step")
    tests_from: str | None = Field(
        default=None,
        description="Set when this baseline didn't run the tests itself: where its test "
        "results come from ('unit <run-id>': that unit's final verification ran them on this "
        "commit; 'baseline <run-id>': an earlier baseline of this commit)",
    )
