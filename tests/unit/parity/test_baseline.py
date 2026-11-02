"""Baseline capture and flaky-test detection (FR-7.1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from codecoremorph.adapters.base import AdapterContext, LanguageAdapter
from codecoremorph.core.errors import BaselineError, ExitCode
from codecoremorph.core.models import (
    AdapterCapabilities,
    BuildResult,
    CodemodResult,
    CodemodSpec,
    Diagnostic,
    DiagnosticOrigin,
    LanguageConfig,
    Severity,
    SymbolIndex,
    TestCaseResult,
    TestReport,
    TestSelection,
    TestStatus,
    ToolchainSpec,
)
from codecoremorph.parity.baseline import capture_baseline, merge_test_runs
from codecoremorph.sandbox.executor import Executor

P, F, S, E, T = (
    TestStatus.PASSED,
    TestStatus.FAILED,
    TestStatus.SKIPPED,
    TestStatus.ERROR,
    TestStatus.TIMEOUT,
)


def _report(**statuses: TestStatus) -> TestReport:
    cases = [
        TestCaseResult(test_id=f"gtest:{name}", status=status, duration_s=0.1)
        for name, status in statuses.items()
    ]
    return TestReport(cases=cases, duration_s=2.0, log_path=Path("test.log"))


def test_merge_classifies_every_test() -> None:
    runs = [
        _report(Stable=P, Flips=P, Broken=F, Mixed=E, Skip=S, Gone=P),
        _report(Stable=P, Flips=F, Broken=F, Mixed=T, Skip=S),
    ]
    merged = merge_test_runs(runs)
    assert merged.runs == 2
    assert merged.passing == ["gtest:Stable"]
    assert merged.failing == ["gtest:Broken", "gtest:Mixed"]
    assert merged.flaky == ["gtest:Flips", "gtest:Gone"]
    assert merged.skipped == ["gtest:Skip"]
    assert merged.total == 6
    with pytest.raises(ValueError, match="at least one"):
        merge_test_runs([])


def _diag(severity: Severity, file: str | None, message: str) -> Diagnostic:
    return Diagnostic(
        file_path=file,
        line=3 if file else None,
        severity=severity,
        message=message,
        origin=DiagnosticOrigin.COMPILER,
    )


class FakeAdapter(LanguageAdapter):
    """Scripted build and test results."""

    language = "cpp"

    def __init__(
        self,
        *,
        configure_ok: bool = True,
        build: list[Diagnostic] | None = None,
        reports: list[TestReport] | None = None,
    ):
        self.configure_ok = configure_ok
        self.build_diagnostics = build or []
        self.reports = list(reports or [_report(A=P)])
        self.calls: list[str] = []

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities()

    def toolchain_requirements(self, lang: LanguageConfig) -> ToolchainSpec:
        return ToolchainSpec(language="cpp", image="x")

    def configure(self, ctx: AdapterContext) -> BuildResult:
        self.calls.append("configure")
        diagnostics = [] if self.configure_ok else [_diag(Severity.ERROR, None, "no CMake")]
        return BuildResult(
            success=self.configure_ok,
            diagnostics=diagnostics,
            duration_s=1.0,
            log_path=Path("configure.log"),
        )

    def build(self, ctx: AdapterContext, targets: list[str] | None = None) -> BuildResult:
        self.calls.append("build")
        ok = not any(d.severity is Severity.ERROR for d in self.build_diagnostics)
        return BuildResult(
            success=ok,
            diagnostics=self.build_diagnostics,
            duration_s=4.0,
            log_path=Path("build.log"),
        )

    def run_tests(self, ctx: AdapterContext, selection: TestSelection | None = None) -> TestReport:
        self.calls.append("test")
        return self.reports.pop(0) if len(self.reports) > 1 else self.reports[0]

    def index(self, ctx: AdapterContext, files: list[str] | None = None) -> SymbolIndex:
        raise NotImplementedError

    def run_codemods(
        self, ctx: AdapterContext, codemods: list[CodemodSpec], files: list[str]
    ) -> CodemodResult:
        raise NotImplementedError


@pytest.fixture
def ctx(tmp_path: Path) -> AdapterContext:
    executor: Executor = None  # type: ignore[assignment]  # the fake adapter never runs commands
    return AdapterContext(
        worktree=tmp_path,
        build_dir=tmp_path,
        cache_dir=tmp_path,
        log_dir=tmp_path,
        executor=executor,
        lang=LanguageConfig(language="cpp", standard="cpp11"),
    )


def test_a_flaky_test_is_excluded_from_parity(ctx: AdapterContext) -> None:
    adapter = FakeAdapter(
        build=[
            _diag(Severity.WARNING, "src/a.cpp", "unused"),
            _diag(Severity.WARNING, "src/a.cpp", "shadow"),
            _diag(Severity.WARNING, None, "linker note"),
        ],
        reports=[_report(Solid=P, Seeded=P), _report(Solid=P, Seeded=F)],
    )
    steps: list[str] = []
    baseline = capture_baseline(
        adapter, ctx, base_commit="a" * 40, runs=2, require_tests=True, on_step=steps.append
    )
    assert steps == ["configure", "build", "test run 1 of 2", "test run 2 of 2"]
    assert baseline.tests.passing == ["gtest:Solid"]
    assert baseline.tests.flaky == ["gtest:Seeded"]
    assert baseline.build_warnings == {"src/a.cpp": 2}
    assert (baseline.configure_s, baseline.build_s, baseline.test_s) == (1.0, 4.0, 2.0)
    assert baseline.standard == "cpp11"
    assert baseline.logs["build"] == Path("build.log")


def test_a_failing_build_stops_with_an_actionable_message(ctx: AdapterContext) -> None:
    errors = [_diag(Severity.ERROR, f"src/{n}.cpp", f"error {n}") for n in range(5)]
    adapter = FakeAdapter(build=errors)
    with pytest.raises(BaselineError) as caught:
        capture_baseline(adapter, ctx, base_commit="b" * 40, runs=2, require_tests=True)
    error = caught.value
    assert error.exit_code is ExitCode.ENVIRONMENT
    assert error.message == "the project doesn't build at the base commit bbbbbbbbbbbb"
    assert error.reason == (
        "src/0.cpp:3: error 0; src/1.cpp:3: error 1; src/2.cpp:3: error 2 (and 2 more errors)"
    )
    assert error.hint is not None
    assert "Full log: build.log" in error.hint
    assert adapter.calls == ["configure", "build"]  # no tests after a failed build


def test_a_failing_configure_stops_too(ctx: AdapterContext) -> None:
    with pytest.raises(BaselineError, match="doesn't configure"):
        capture_baseline(
            FakeAdapter(configure_ok=False), ctx, base_commit="c" * 40, runs=1, require_tests=True
        )


def test_no_passing_tests(ctx: AdapterContext) -> None:
    empty = FakeAdapter(reports=[TestReport(cases=[], duration_s=0, log_path=Path("t.log"))])
    with pytest.raises(BaselineError, match="no passing tests") as caught:
        capture_baseline(empty, ctx, base_commit="d" * 40, runs=1, require_tests=True)
    assert caught.value.reason == "the baseline found no tests, so parity can't be checked"
    failing = FakeAdapter(reports=[_report(A=F)])
    with pytest.raises(BaselineError) as caught:
        capture_baseline(failing, ctx, base_commit="d" * 40, runs=1, require_tests=True)
    assert caught.value.reason is not None
    assert "1 test, none passing" in caught.value.reason
    allowed = capture_baseline(empty, ctx, base_commit="d" * 40, runs=1, require_tests=False)
    assert allowed.tests.total == 0
