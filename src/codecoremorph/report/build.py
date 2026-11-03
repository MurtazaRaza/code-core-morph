"""Assemble a run's report from the store and the run's artifacts (FR-13.1).

The report can be built at any time, from any process: at the end of a run, on abort or
budget exhaustion, and later by ``codecoremorph report``.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ValidationError

from codecoremorph.core.models import (
    Baseline,
    FinalVerification,
    Plan,
    ReviewItem,
    ReviewKind,
    RuleSummary,
    RunReport,
    RunStatus,
    UnitResult,
    UnitStatus,
    UsageTotals,
)
from codecoremorph.report.render import render_markdown
from codecoremorph.store.db import Store
from codecoremorph.store.layout import RunArtifacts

log = logging.getLogger(__name__)

UNIT_RESULT_FILE = "result.json"
FINAL_FILE = "final.json"
BASELINE_FILE = "baseline.json"


def _load[M: BaseModel](model: type[M], path: Path) -> M | None:
    if not path.is_file():
        return None
    try:
        return model.model_validate_json(path.read_bytes())
    except ValidationError:
        log.warning("ignoring an unreadable %s", path)
        return None


def write_json(path: Path, model: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(model.model_dump_json(indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def unit_result_path(artifacts: RunArtifacts, unit_id: str) -> Path:
    return artifacts.unit_dir(unit_id) / UNIT_RESULT_FILE


def load_unit_result(artifacts: RunArtifacts, unit_id: str) -> UnitResult | None:
    return _load(UnitResult, unit_result_path(artifacts, unit_id))


def load_final(artifacts: RunArtifacts) -> FinalVerification | None:
    return _load(FinalVerification, artifacts.root / FINAL_FILE)


def build_report(
    store: Store,
    artifacts: RunArtifacts,
    run_id: str,
    *,
    project: str,
    branch: str,
    head: str | None,
    status: RunStatus,
    reason: str | None,
    finished_at: datetime | None,
) -> RunReport:
    record = store.get_run(run_id)
    if record is None:
        raise KeyError(run_id)
    plan = _load(Plan, artifacts.plan_json) or Plan()
    baseline = _load(Baseline, artifacts.root / BASELINE_FILE)
    units = store.units(run_id)
    results = {u.id: r for u in units if (r := load_unit_result(artifacts, u.id)) is not None}
    final = load_final(artifacts)

    counts = Counter(u.status.value for u in units)
    rules: list[RuleSummary] = []
    for plan_rule in plan.rules:
        # A unit's rule_sites count its sites per file; a plan unit has one rule.
        mine = [u for u in units if plan_rule.id in u.rules]
        committed = [u for u in mine if u.status is UnitStatus.COMMITTED]
        planned = sum(sum(u.rule_sites.values()) for u in mine)
        left = sum(
            results[u.id].remaining_sites.get(plan_rule.id, 0) for u in committed if u.id in results
        )
        converted = sum(sum(u.rule_sites.values()) for u in committed) - left
        rules.append(
            RuleSummary(
                id=plan_rule.id,
                units=len(mine),
                committed=len(committed),
                planned_sites=planned,
                converted_sites=max(converted, 0),
            )
        )

    calls = store.llm_calls(run_id)

    def totals(role: str | None) -> UsageTotals:
        mine = [c for c in calls if role is None or c.role.value == role]
        return UsageTotals(
            calls=len(mine),
            tokens=sum(c.tokens for c in mine),
            cost_usd=round(sum(c.cost_usd for c in mine), 6),
            latency_s=round(sum(c.latency_s for c in mine), 3),
        )

    by_role = {role: totals(role) for role in sorted({c.role.value for c in calls})}
    total = totals(None)
    providers = sorted({c.provider for c in calls})

    review: list[ReviewItem] = []
    for unit in units:
        result = results.get(unit.id)
        if unit.status is UnitStatus.FAILED:
            why = (result.reason if result else None) or unit.note or "no reason recorded"
            review.append(ReviewItem(kind=ReviewKind.FAILED_UNIT, unit_id=unit.id, text=why))
        elif unit.status in (UnitStatus.SKIPPED, UnitStatus.TOO_LARGE, UnitStatus.EXCLUDED):
            text = f"{unit.status.value}: {unit.note or 'left out of the run'}"
            review.append(ReviewItem(kind=ReviewKind.SKIPPED_UNIT, unit_id=unit.id, text=text))
        if result is None:
            continue
        for approval in result.approvals:
            review.append(ReviewItem(kind=ReviewKind.API_CHANGE, unit_id=unit.id, text=approval))
        if result.test_files_changed and unit.status is UnitStatus.COMMITTED:
            text = f"test files changed: {', '.join(result.test_files_changed)}"
            review.append(ReviewItem(kind=ReviewKind.TEST_EDIT, unit_id=unit.id, text=text))
        for note in result.review_notes:
            review.append(ReviewItem(kind=ReviewKind.AGENT_NOTE, unit_id=unit.id, text=note))
        for warning in result.warnings:
            review.append(ReviewItem(kind=ReviewKind.WARNING, unit_id=unit.id, text=warning))
    if final is not None and final.ran and not final.passed:
        review.append(
            ReviewItem(
                kind=ReviewKind.VERIFICATION,
                text=f"the final verification failed: {final.reason or 'see the log'}",
            )
        )

    tests = baseline.tests if baseline else None
    started = record.created_at
    return RunReport(
        run_id=run_id,
        project=project,
        status=status,
        reason=reason,
        language=plan.language or "",
        source_standard=plan.source_standard or "",
        target_standard=plan.target_standard or "",
        base_commit=record.base_commit or "",
        branch=branch,
        head=head,
        started_at=started,
        finished_at=finished_at,
        duration_s=round((finished_at - started).total_seconds(), 1) if finished_at else None,
        unit_counts=dict(sorted(counts.items())),
        units=[results[u.id] for u in units if u.id in results],
        unit_notes={u.id: u.note for u in units if u.id not in results and u.note},
        rules=rules,
        baseline_tests={
            "passing": len(tests.passing),
            "failing": len(tests.failing),
            "flaky": len(tests.flaky),
            "skipped": len(tests.skipped),
        }
        if tests
        else {},
        final=final,
        usage_by_role=dict(sorted(by_role.items())),
        usage_total=total,
        providers=providers,
        review=review,
        environment=store.run_environment(run_id),
    )


def write_report(report: RunReport, artifacts: RunArtifacts) -> tuple[Path, Path]:
    """``report.json`` and ``report.md`` in the run's directory."""
    write_json(artifacts.report_json, report)
    temporary = artifacts.report_md.with_name(artifacts.report_md.name + ".tmp")
    temporary.write_text(render_markdown(report), encoding="utf-8")
    temporary.replace(artifacts.report_md)
    return artifacts.report_md, artifacts.report_json
