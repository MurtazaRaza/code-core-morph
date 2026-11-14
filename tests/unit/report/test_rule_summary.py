"""The report's rule table (FR-13.1): planned sites, and the ones committed units converted."""

from __future__ import annotations

from datetime import UTC, datetime

from codecoremorph.core.models import PlanRule, RuleKind, UnitResult, UnitStatus, WorkUnit
from codecoremorph.report.build import rule_summary


def _units(rule: str) -> list[WorkUnit]:
    return [
        WorkUnit(id="U-0001", seq=1, rules=[rule], files=["src/Money.cs"],
                 rule_sites={"src/Money.cs": 3}, status=UnitStatus.COMMITTED),
        WorkUnit(id="U-0002", seq=2, rules=[rule], files=["src/Cart.cs"],
                 rule_sites={"src/Cart.cs": 2}, status=UnitStatus.FAILED),
    ]  # fmt: skip


def _results(rule: str) -> dict[str, UnitResult]:
    at = datetime(2026, 11, 14, tzinfo=UTC)
    left = UnitResult(
        unit_id="U-0001",
        status=UnitStatus.COMMITTED,
        remaining_sites={rule: 1},
        started_at=at,
        finished_at=at,
        duration_s=0,
    )
    return {"U-0001": left}


def test_an_upgrades_sites_left_after_a_committed_unit_are_not_converted() -> None:
    rule = PlanRule(id="use_override", kind=RuleKind.API_PRESERVING, sites=5)
    summary = rule_summary(rule, _units(rule.id), _results(rule.id))
    assert (summary.units, summary.committed) == (2, 1)
    assert (summary.planned_sites, summary.converted_sites) == (5, 2)


def test_a_committed_port_unit_converted_all_its_sites() -> None:
    """D-85 (4): a ported C# type stays declared as its façade until the retirement, so the
    detector still finds it after its unit; the unit's post-conditions judged the port."""
    rule = PlanRule(id="port_csharp_type_to_rust", kind=RuleKind.PORT, sites=5)
    summary = rule_summary(rule, _units(rule.id), _results(rule.id))
    assert (summary.units, summary.committed) == (2, 1)
    assert (summary.planned_sites, summary.converted_sites) == (5, 3)


def test_a_rule_without_units_has_an_empty_row() -> None:
    rule = PlanRule(id="port_csharp_type_to_cpp", kind=RuleKind.PORT, sites=0)
    summary = rule_summary(rule, _units("other"), {})
    assert (summary.units, summary.committed, summary.planned_sites) == (0, 0, 0)
    assert summary.converted_sites == 0
