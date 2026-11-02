"""Build and test results returned by language adapters (SPEC §7.1)."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import ClassVar

from pydantic import Field

from codecoremorph.core.models.base import CoreModel


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    NOTE = "note"


class DiagnosticOrigin(StrEnum):
    COMPILER = "compiler"
    LINKER = "linker"
    BUILD_SYSTEM = "build_system"
    ANALYZER = "analyzer"


class Diagnostic(CoreModel):
    """One compiler, linker, build-system or analyzer message."""

    file_path: str | None = Field(
        description="Worktree-relative path; None for link or configuration errors"
    )
    line: int | None = Field(default=None, ge=1)
    column: int | None = Field(default=None, ge=1)
    severity: Severity
    code: str | None = Field(
        default=None, description='e.g. "-Wunused-variable", "CS0246", "undefined-reference"'
    )
    message: str
    # A lambda, because the class name doesn't exist yet while its body runs.
    notes: list[Diagnostic] = Field(default_factory=lambda: list[Diagnostic]())
    origin: DiagnosticOrigin


class BuildResult(CoreModel):
    """Outcome of a configure or build step. Warnings never make ``success`` false."""

    success: bool
    diagnostics: list[Diagnostic] = Field(default_factory=list[Diagnostic])
    duration_s: float = Field(ge=0)
    log_path: Path = Field(description="Full raw log")

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity is Severity.WARNING]


class TestStatus(StrEnum):
    __test__: ClassVar[bool] = False  # not a pytest test class

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"
    TIMEOUT = "timeout"


class TestCaseResult(CoreModel):
    __test__: ClassVar[bool] = False

    test_id: str = Field(
        pattern=r"^[a-z][a-z0-9_]*:.+$",
        description='Stable, framework-qualified ID, e.g. "gtest:VectorTest.Add"',
    )
    status: TestStatus
    duration_s: float = Field(ge=0)
    message: str | None = None
    stack: str | None = None


class TestReport(CoreModel):
    __test__: ClassVar[bool] = False

    cases: list[TestCaseResult] = Field(default_factory=list[TestCaseResult])
    duration_s: float = Field(ge=0)
    log_path: Path

    def ids_with_status(self, status: TestStatus) -> set[str]:
        return {c.test_id for c in self.cases if c.status is status}
