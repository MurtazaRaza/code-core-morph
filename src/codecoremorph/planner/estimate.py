"""Token, cost and time estimates for work units (FR-5.4, FR-5.5, SPEC §7.5.1 steps 6-7).

The numbers are deliberately simple and provisional: NFR-7.2 asks for plan costs within ±50%
of actual cost, measured after M1, when these constants get re-set from data.

- Tokens: characters / 3 (code tokenizes denser than prose), plus a 25% margin, plus a fixed
  prompt overhead for role instructions, the edit format and out-of-unit signatures.
- An agent attempt is a few calls in one conversation: the first writes the unit's context to
  the prompt cache, later calls read it back and add tool results.
- How likely the agent runs depends on the rule: a rule with codemods often finishes without it.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

from codecoremorph.config.models import ModelPrice
from codecoremorph.core.models import Baseline, Plan, Rule, UnitEstimate

CHARS_PER_TOKEN = 3.0
TOKEN_MARGIN = 1.25
PROMPT_OVERHEAD_TOKENS = 3_000
CONTEXT_FRACTION = 0.5  # a unit's context may use half of the editor model's window
CALLS_PER_ATTEMPT = 4
TOKENS_ADDED_PER_CALL = 2_000  # tool results and feedback
THINKING_TOKENS_PER_ATTEMPT = 4_000
OUTPUT_SHARE = 0.5  # edits rewrite about half of the unit's text
ATTEMPTS_WHEN_THE_AGENT_RUNS = 1.5
LLM_SECONDS_PER_ATTEMPT = 120.0
FALLBACK_CONTEXT_WINDOW = 32_768  # when the editor model's window is unknown


def estimate_tokens(chars: int) -> int:
    return math.ceil(chars / CHARS_PER_TOKEN * TOKEN_MARGIN)


def unit_token_limit(context_window: int | None) -> int:
    return int((context_window or FALLBACK_CONTEXT_WINDOW) * CONTEXT_FRACTION)


def agent_probability(rule: Rule) -> float:
    """How likely a unit of ``rule`` needs the agent after its codemods."""
    if rule.llm is None:
        return 0.2  # only to repair what the codemods broke
    return 0.6 if rule.codemods else 1.0


@dataclass(frozen=True)
class Estimator:
    prices: ModelPrice | None
    guidance_chars: dict[str, int]  # by rule ID

    def context_tokens(self, file_chars: int, rules: Iterable[str]) -> int:
        guidance = sum(self.guidance_chars.get(r, 0) for r in rules)
        return estimate_tokens(file_chars + guidance) + PROMPT_OVERHEAD_TOKENS

    def unit(self, file_chars: int, rules: list[Rule]) -> UnitEstimate:
        context = self.context_tokens(file_chars, [r.id for r in rules])
        attempts = round(max(agent_probability(r) for r in rules) * ATTEMPTS_WHEN_THE_AGENT_RUNS, 2)
        output = int(estimate_tokens(file_chars) * OUTPUT_SHARE) + THINKING_TOKENS_PER_ATTEMPT
        later_calls = CALLS_PER_ATTEMPT - 1
        added = later_calls * TOKENS_ADDED_PER_CALL
        per_attempt_tokens = context * CALLS_PER_ATTEMPT + added + output
        cost: float | None = None
        if self.prices is not None:
            p = self.prices
            write = p.cache_write_usd_per_mtok or p.input_usd_per_mtok
            read = p.cache_read_usd_per_mtok or p.input_usd_per_mtok
            per_attempt = (
                context * write
                + later_calls * context * read
                + added * p.input_usd_per_mtok
                + output * p.output_usd_per_mtok
            ) / 1_000_000
            cost = round(per_attempt * attempts, 4)
        return UnitEstimate(
            context_tokens=context,
            expected_attempts=attempts,
            tokens=math.ceil(per_attempt_tokens * attempts),
            cost_usd=cost,
        )


def estimate_duration_s(plan: Plan, baseline: Baseline) -> float:
    """Wall-clock estimate for running ``plan``: every unit is verified at least once, and each
    agent attempt adds model time plus another build and test run. Measured durations vary
    between runs, so this stays out of ``plan.json``."""
    verify = baseline.build_s + baseline.test_s
    total = 0.0
    for unit in plan.units:
        if unit.status.is_terminal or unit.estimate is None:
            continue
        total += verify + unit.estimate.expected_attempts * (verify + LLM_SECONDS_PER_ATTEMPT)
    return round(total, 1)
