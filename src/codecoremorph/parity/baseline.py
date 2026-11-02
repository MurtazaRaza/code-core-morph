"""The baseline: configure, build and test the base commit before any change (FR-7.1).

The tests run ``parity.baseline_runs`` times. A test whose result differs between runs is
flaky: it is excluded from parity and reported. A test that fails in every run is excluded too.
If the baseline doesn't build, the run stops: there is no green starting point.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

from codecoremorph.adapters.base import AdapterContext, LanguageAdapter
from codecoremorph.core.errors import BaselineError
from codecoremorph.core.models import (
    Baseline,
    BuildResult,
    Severity,
    TestBaseline,
    TestReport,
    TestStatus,
)

log = logging.getLogger(__name__)

_FAILED = frozenset({TestStatus.FAILED, TestStatus.ERROR, TestStatus.TIMEOUT})


def merge_test_runs(reports: Sequence[TestReport]) -> TestBaseline:
    """Classify every test ID seen in any run."""
    if not reports:
        raise ValueError("merge_test_runs needs at least one report")
    statuses: dict[str, list[TestStatus | None]] = {}
    for n, report in enumerate(reports):
        for case in report.cases:
            history = statuses.setdefault(case.test_id, [None] * len(reports))
            history[n] = case.status
    passing: list[str] = []
    failing: list[str] = []
    flaky: list[str] = []
    skipped: list[str] = []
    for test_id, history in sorted(statuses.items()):
        if any(s is None for s in history) or len(set(history)) > 1:
            # Failed, errored and timed out all count as failing, but a test that sometimes
            # passes or is sometimes missing is flaky.
            if all(s in _FAILED for s in history):
                failing.append(test_id)
            else:
                flaky.append(test_id)
        elif history[0] is TestStatus.PASSED:
            passing.append(test_id)
        elif history[0] is TestStatus.SKIPPED:
            skipped.append(test_id)
        else:
            failing.append(test_id)
    return TestBaseline(
        runs=len(reports), passing=passing, failing=failing, flaky=flaky, skipped=skipped
    )


def _failure(step: str, result: BuildResult, base: str) -> BaselineError:
    errors = result.errors
    shown = [
        f"{d.file_path}:{d.line}: {d.message}" if d.file_path else d.message for d in errors[:3]
    ]
    more = f" (and {len(errors) - 3} more errors)" if len(errors) > 3 else ""
    return BaselineError(
        f"the project doesn't {step} at the base commit {base[:12]}",
        reason="; ".join(shown) + more if shown else None,
        hint="CodeCoreMorph migrates only projects that build and pass their tests before the "
        f"migration. Fix the {step} on the base commit, or check the manifest's build options. "
        f"Full log: {result.log_path}",
    )


def _warnings_by_file(result: BuildResult) -> dict[str, int]:
    counts: dict[str, int] = {}
    for d in result.diagnostics:
        if d.severity is Severity.WARNING and d.file_path is not None:
            counts[d.file_path] = counts.get(d.file_path, 0) + 1
    return dict(sorted(counts.items()))


def capture_baseline(
    adapter: LanguageAdapter,
    ctx: AdapterContext,
    *,
    base_commit: str,
    runs: int,
    require_tests: bool,
    on_step: Callable[[str], None] | None = None,
) -> Baseline:
    """Configure, build and run the tests ``runs`` times on the base commit."""
    step = on_step or (lambda _: None)
    step("configure")
    configured = adapter.configure(ctx)
    if not configured.success:
        raise _failure("configure", configured, base_commit)
    step("build")
    built = adapter.build(ctx)
    if not built.success:
        raise _failure("build", built, base_commit)
    reports: list[TestReport] = []
    for n in range(1, runs + 1):
        step(f"test run {n} of {runs}")
        reports.append(adapter.run_tests(ctx))
    tests = merge_test_runs(reports)
    if tests.flaky:
        log.warning(
            "%d flaky tests are excluded from parity: %s",
            len(tests.flaky),
            ", ".join(tests.flaky[:5]) + (" ..." if len(tests.flaky) > 5 else ""),
        )
    if require_tests and not tests.passing:
        plural = "s" if tests.total != 1 else ""
        found = (
            "no tests" if not tests.total else f"{tests.total} test{plural}, none passing every run"
        )
        raise BaselineError(
            f"the project has no passing tests at the base commit {base_commit[:12]}",
            reason=f"the baseline found {found}, so parity can't be checked",
            hint="Fix or add tests, or set guardrails.require_passing_tests: false to migrate "
            f"without a parity check. Test log: {reports[-1].log_path}",
        )
    return Baseline(
        base_commit=base_commit,
        standard=ctx.lang.standard,
        tests=tests,
        build_warnings=_warnings_by_file(built),
        configure_s=round(configured.duration_s, 3),
        build_s=round(built.duration_s, 3),
        test_s=round(sum(r.duration_s for r in reports) / len(reports), 3),
        logs={
            "configure": configured.log_path,
            "build": built.log_path,
            "test": reports[-1].log_path,
        },
    )
