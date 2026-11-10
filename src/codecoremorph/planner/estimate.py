"""Token, cost and time estimates for work units (FR-5.4, FR-5.5, SPEC §7.5.1 steps 6-7).

The numbers are deliberately simple and provisional: NFR-7.2 asks for plan costs within ±50%
of actual cost, measured after M1, when these constants get re-set from data.

- Tokens: characters / 3 (code tokenizes denser than prose), plus a 25% margin, plus a fixed
  prompt overhead for role instructions, the edit format and out-of-unit signatures.
- An agent attempt is a few calls in one conversation: the first writes the unit's context to
  the prompt cache, later calls read it back and add tool results.
- How likely the agent runs depends on the rule: a rule with codemods often finishes without it.
- Model time per attempt grows with the unit's lines: over the M1 benchmark's 20 agent units it
  was 0.12 s per line (intercept near zero), from 12 lines in 25 s to 4,693 in 663 s. A port
  writes the module again in another language and its façade, so it expects twice the output
  and, until a measured port run re-sets it, three times the time.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

from codecoremorph.config.models import ModelPrice
from codecoremorph.core.models import Baseline, Plan, Rule, RuleKind, UnitEstimate

CHARS_PER_TOKEN = 3.0
TOKEN_MARGIN = 1.25
PROMPT_OVERHEAD_TOKENS = 3_000
CONTEXT_FRACTION = 0.5  # a unit's context may use half of the editor model's window
CALLS_PER_ATTEMPT = 4
TOKENS_ADDED_PER_CALL = 2_000  # tool results and feedback
THINKING_TOKENS_PER_ATTEMPT = 4_000
OUTPUT_SHARE = 0.5  # edits rewrite about half of the unit's text
ATTEMPTS_WHEN_THE_AGENT_RUNS = 1.5
LLM_SECONDS_PER_ATTEMPT = 100.0  # the M1 benchmark: Claude Code averaged 103 s
AGENT_SECONDS_PER_LINE = 0.12  # the M1 benchmark's fit, per attempt
MIN_AGENT_SECONDS = 20.0  # the smallest M1 units still took about this long
PORT_OUTPUT_SHARE = 2.0  # a port writes the module again, and its façade
PORT_TIME_FACTOR = 3.0  # provisional (D-58); C# type units matched it within 3% (M11)
# A port's verification also configures, builds, lints and tests the target, which the source
# baseline doesn't time: 23 to 25 s more per verification in the M8-M10 benchmarks, 5 to 10 s
# in M11's once a unit's lint was its own files, its C# build and tests being most of it (D-83).
PORT_TARGET_VERIFY_S = 20.0
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

    def unit(self, file_chars: int, rules: list[Rule], lines: int | None = None) -> UnitEstimate:
        """``lines``: the unit's files' lines, for the model time; without them, the M1
        average."""
        context = self.context_tokens(file_chars, [r.id for r in rules])
        attempts = round(max(agent_probability(r) for r in rules) * ATTEMPTS_WHEN_THE_AGENT_RUNS, 2)
        port = any(r.kind is RuleKind.PORT for r in rules)
        share = PORT_OUTPUT_SHARE if port else OUTPUT_SHARE
        output = int(estimate_tokens(file_chars) * share) + THINKING_TOKENS_PER_ATTEMPT
        agent_s = (
            LLM_SECONDS_PER_ATTEMPT
            if lines is None
            else max(MIN_AGENT_SECONDS, AGENT_SECONDS_PER_LINE * lines)
        ) * (PORT_TIME_FACTOR if port else 1.0)
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
            agent_s=round(agent_s, 1),
        )


def estimate_duration_s(plan: Plan, baseline: Baseline) -> float:
    """Wall-clock estimate for running ``plan``: every unit is verified at least once, and each
    agent attempt adds model time plus another build and test run (a port's: the target's too).
    Measured durations vary between runs, so this stays out of ``plan.json``."""
    verify = baseline.build_s + baseline.test_s
    if plan.target_language is not None:
        verify += PORT_TARGET_VERIFY_S
    total = 0.0
    for unit in plan.units:
        if unit.status.is_terminal or unit.estimate is None:
            continue
        model_s = unit.estimate.agent_s
        if model_s is None:
            model_s = LLM_SECONDS_PER_ATTEMPT
        total += verify + unit.estimate.expected_attempts * (verify + model_s)
    return round(total, 1)
