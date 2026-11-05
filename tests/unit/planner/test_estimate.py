"""Token, cost and time estimates (FR-5.4, FR-5.5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from codecoremorph.config.models import ModelPrice
from codecoremorph.core.models import (
    Baseline,
    CodemodSpec,
    LlmGuidance,
    Plan,
    Rule,
    RuleKind,
    TestBaseline,
    UnitEstimate,
    UnitStatus,
    WorkUnit,
)
from codecoremorph.planner.estimate import (
    FALLBACK_CONTEXT_WINDOW,
    Estimator,
    agent_probability,
    estimate_duration_s,
    estimate_tokens,
    unit_token_limit,
)

PRICES = ModelPrice(
    input_usd_per_mtok=4.0,
    output_usd_per_mtok=20.0,
    cache_read_usd_per_mtok=0.2,
    cache_write_usd_per_mtok=5.0,
)


def _rule(*, codemods: bool, llm: bool) -> Rule:
    return Rule(
        id="r",
        language="cpp",
        kind=RuleKind.API_PRESERVING,
        description="r",
        codemods=[CodemodSpec(tool="clang-tidy")] if codemods else [],
        llm=LlmGuidance(guidance="g.md") if llm else None,
    )


def test_tokens_and_limits() -> None:
    assert estimate_tokens(0) == 0
    assert estimate_tokens(300) == 125  # 300 / 3 * 1.25
    assert unit_token_limit(1_000_000) == 500_000
    assert unit_token_limit(None) == FALLBACK_CONTEXT_WINDOW // 2


def test_agent_probability_by_rule_shape() -> None:
    assert agent_probability(_rule(codemods=False, llm=True)) == 1.0
    assert agent_probability(_rule(codemods=True, llm=True)) == 0.6
    assert agent_probability(_rule(codemods=True, llm=False)) == 0.2


def test_unit_estimate_arithmetic() -> None:
    estimator = Estimator(prices=PRICES, guidance_chars={"r": 600})
    estimate = estimator.unit(2_400, [_rule(codemods=False, llm=True)])
    context = estimate_tokens(3_000) + 3_000  # files + guidance, plus the prompt overhead
    assert estimate.context_tokens == context == 4_250
    assert estimate.expected_attempts == 1.5
    output = estimate_tokens(2_400) // 2 + 4_000
    per_attempt = (context * 5.0 + 3 * context * 0.2 + 3 * 2_000 * 4.0 + output * 20.0) / 1_000_000
    assert estimate.cost_usd == pytest.approx(round(per_attempt * 1.5, 4))
    assert estimate.tokens == int((context * 4 + 6_000 + output) * 1.5)
    free = Estimator(prices=None, guidance_chars={}).unit(100, [_rule(codemods=True, llm=False)])
    assert free.cost_usd is None
    assert free.expected_attempts == 0.3


def test_duration_counts_pending_units_only() -> None:
    def unit(seq: int, status: UnitStatus) -> WorkUnit:
        return WorkUnit(
            id=f"U-{seq:04d}",
            seq=seq,
            rules=["r"],
            files=["a.cpp"],
            status=status,
            estimate=UnitEstimate(context_tokens=10, expected_attempts=1.0),
        )

    plan = Plan(units=[unit(1, UnitStatus.PENDING), unit(2, UnitStatus.SKIPPED)])
    baseline = Baseline(
        base_commit="0" * 40,
        standard="cpp11",
        tests=TestBaseline(runs=2),
        configure_s=1.0,
        build_s=10.0,
        test_s=5.0,
        logs={"build": Path("build.log")},
    )
    assert estimate_duration_s(plan, baseline) == 15.0 + 1.0 * (15.0 + 100.0)
    # A unit that knows its model time (from its lines) uses it.
    timed = plan.units[0].model_copy(
        update={"estimate": UnitEstimate(context_tokens=10, expected_attempts=2.0, agent_s=40.0)}
    )
    assert estimate_duration_s(Plan(units=[timed]), baseline) == 15.0 + 2.0 * (15.0 + 40.0)


def test_model_time_grows_with_lines_and_more_for_a_port() -> None:
    """The M1 fit (0.12 s per line, at least 20 s), and a port's larger output and time."""
    estimator = Estimator(prices=None, guidance_chars={})
    upgrade = _rule(codemods=False, llm=True)
    assert estimator.unit(2_400, [upgrade], lines=50).agent_s == 20.0
    assert estimator.unit(2_400, [upgrade], lines=1_000).agent_s == 120.0
    assert estimator.unit(2_400, [upgrade]).agent_s == 100.0  # no lines: the M1 average
    port = Rule(
        id="p",
        language="cpp",
        kind=RuleKind.PORT,
        target_language="rust",
        description="p",
        llm=LlmGuidance(guidance="g.md"),
    )
    ported = estimator.unit(2_400, [port], lines=1_000)
    assert ported.agent_s == 360.0
    assert ported.tokens > estimator.unit(2_400, [upgrade], lines=1_000).tokens
