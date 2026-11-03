"""The Markdown report (FR-13.1): what the run didn't verify is named, not only counted."""

from __future__ import annotations

from datetime import UTC, datetime

from codecoremorph.core.models import RunReport, RunStatus
from codecoremorph.report.render import render_markdown


def _report(**fields: object) -> RunReport:
    base: dict[str, object] = {
        "run_id": "20261103-100000-ab12",
        "project": "geo",
        "status": RunStatus.COMPLETED,
        "language": "cpp",
        "source_standard": "cpp11",
        "target_standard": "cpp20",
        "base_commit": "0" * 40,
        "branch": "codecoremorph/20261103-100000-ab12",
        "started_at": datetime(2026, 11, 3, tzinfo=UTC),
    }
    return RunReport.model_validate(base | fields)


def test_excused_tests_and_plan_warnings_are_named() -> None:
    excused = [f"gtest:Suite.Case{i:02d}" for i in range(23)]
    markdown = render_markdown(
        _report(
            baseline_tests={"passing": 5, "failing": 20, "flaky": 3, "skipped": 0},
            excused_tests=excused,
            plan_warnings=["2 code file(s) aren't compiled by this build configuration"],
        )
    )
    assert "Not checked, because they failed or were flaky before the run: " in markdown
    assert "`gtest:Suite.Case19` and 3 more." in markdown
    assert "`gtest:Suite.Case20`" not in markdown
    assert "### Plan warnings" in markdown
    assert "- 2 code file(s) aren't compiled by this build configuration" in markdown
    assert "Nothing needs review." not in markdown


def test_a_clean_report_needs_no_review() -> None:
    markdown = render_markdown(_report())
    assert "Nothing needs review." in markdown
    assert "Not checked" not in markdown
    assert "Plan warnings" not in markdown
