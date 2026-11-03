"""Characterization tests: tests that pin down untested code's behavior before the migration
(FR-7.2 to FR-7.6, SPEC §7.11, D-46)."""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar

from pydantic import Field

from codecoremorph.core.models.attempts import Attempt
from codecoremorph.core.models.base import CoreModel, UnitId, WorktreePath
from codecoremorph.core.models.index import SymbolKind


class CharacterizedSymbol(CoreModel):
    """A symbol no test reaches, directly or through one call (SPEC §7.11 step 1)."""

    id: str
    name: str
    qualified_name: str
    kind: SymbolKind
    file: WorktreePath = Field(description="Where it is defined")
    line: int = Field(ge=1)
    signature: str | None = None
    declared_in: list[WorktreePath] = Field(
        default_factory=list[WorktreePath], description="Headers that declare it"
    )


class CharacterizationJob(CoreModel):
    """The tests for one plan unit's untested symbols: one test file, written by the
    ``test_writer`` role or a host agent."""

    id: UnitId = Field(description="C-NNNN")
    plan_unit: UnitId = Field(description="The unit of the plan made before characterization")
    test_file: WorktreePath = Field(description="The one file the writer creates")
    symbols: list[CharacterizedSymbol] = Field(min_length=1)
    sources: list[WorktreePath] = Field(
        default_factory=list[WorktreePath],
        description="The files defining the symbols: the test links what builds them",
    )
    max_tests: int = Field(ge=1, description="parity.characterization_tests.max_tests_per_unit")


class JobStatus(StrEnum):
    ACCEPTED = "accepted"  # every test the writer kept passed the checks
    PARTIAL = "partial"  # some tests were dropped after the regeneration attempt
    DROPPED = "dropped"  # no test was accepted; the file is gone
    ABORTED = "aborted"  # the host agent gave the job up


class RejectedTest(CoreModel):
    test: str = Field(description='"Suite.Case", or the file when the whole file failed')
    reason: str


class JobResult(CoreModel):
    """How a characterization job ended. Written to ``attempts/<job-id>/result.json``."""

    job_id: UnitId
    plan_unit: UnitId
    status: JobStatus
    test_file: WorktreePath
    symbols: int = Field(ge=0)
    tests: list[str] = Field(default_factory=list[str], description="Accepted test IDs")
    rejected: list[RejectedTest] = Field(default_factory=list[RejectedTest])
    attempts: int = Field(default=0, ge=0)
    records: list[Attempt] = Field(
        default_factory=list[Attempt],
        description="The attempts (jobs aren't plan units, so the store's attempts table, "
        "keyed by unit, doesn't hold them)",
    )
    reason: str | None = None


class CharacterizationPlan(CoreModel):
    """The run's characterization jobs, chosen once from the plan of the base index.
    Written to ``characterization.json`` in the run's directory."""

    scope: str
    untested_symbols: int = Field(ge=0)
    jobs: list[CharacterizationJob] = Field(default_factory=list[CharacterizationJob])
    reason: str | None = Field(default=None, description="Why no job runs, when none does")
    removed: str | None = Field(
        default=None, description="The commit that removed the tests again (keep: false)"
    )


class CharacterizationSummary(CoreModel):
    """The report's characterization section (SPEC §7.11)."""

    ran: bool = False
    reason: str | None = Field(default=None, description="Why it didn't run, when it didn't")
    scope: str = "untested_only"
    untested_symbols: int = 0
    jobs: list[JobResult] = Field(default_factory=list[JobResult])
    tests: list[str] = Field(default_factory=list[str], description="Accepted test IDs")
    commit: str | None = None
    removed: bool = Field(default=False, description="keep: false removed them at the end")


class TestAssertions(CoreModel):
    """One test case's assertions, normalized, for the assertion guard (I-4)."""

    __test__: ClassVar[bool] = False  # not a pytest test class

    file: WorktreePath
    test: str = Field(description='The name in the source, e.g. "Suite.Case"')
    test_id: str = Field(description='The ID test reports use, e.g. "gtest:Suite.Case"')
    line: int = Field(ge=1)
    assertions: list[str] = Field(default_factory=list[str])
