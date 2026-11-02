"""The usage ledger: every call's tokens, cost and latency, rolled up per unit, role and run,
with the run's hard caps (FR-8.3, FR-14.1, NFR-7.1).

Caps are checked before every call, so a run overshoots by at most the one call in flight.
Tokens count everything a call processed: uncached input, output, and cache reads and writes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from codecoremorph.core.errors import BudgetExhaustedError
from codecoremorph.core.models import Role, StopReason, Usage


@dataclass(frozen=True)
class LedgerEntry:
    role: Role
    provider: str
    model: str
    usage: Usage
    cost_usd: float | None
    latency_s: float
    stop_reason: StopReason
    unit_id: str | None = None
    attempt: int | None = None


@dataclass
class Totals:
    calls: int = 0
    usage: Usage = field(default_factory=Usage)
    cost_usd: float = 0.0
    latency_s: float = 0.0

    def add(self, entry: LedgerEntry) -> None:
        self.calls += 1
        self.usage = self.usage + entry.usage
        self.cost_usd = round(self.cost_usd + (entry.cost_usd or 0.0), 6)
        self.latency_s = round(self.latency_s + entry.latency_s, 3)


@dataclass
class UsageLedger:
    max_cost_usd: float | None = None
    max_tokens: int | None = None
    sink: Callable[[LedgerEntry], None] | None = None
    entries: list[LedgerEntry] = field(default_factory=list[LedgerEntry])
    total: Totals = field(default_factory=Totals)

    def check(self) -> None:
        """Raise :class:`BudgetExhaustedError` when a cap is reached."""
        if self.max_cost_usd is not None and self.total.cost_usd >= self.max_cost_usd:
            raise BudgetExhaustedError(
                f"the run's cost cap of ${self.max_cost_usd:.2f} is reached",
                reason=f"${self.total.cost_usd:.2f} spent over {self.total.calls} calls",
                hint="Raise guardrails.max_run_cost_usd, or resume with a higher cap.",
            )
        if self.max_tokens is not None and self.total.usage.total >= self.max_tokens:
            raise BudgetExhaustedError(
                f"the run's token cap of {self.max_tokens:,} is reached",
                reason=f"{self.total.usage.total:,} tokens over {self.total.calls} calls",
                hint="Raise guardrails.max_run_tokens, or resume with a higher cap.",
            )

    def record(self, entry: LedgerEntry) -> None:
        self.entries.append(entry)
        self.total.add(entry)
        if self.sink is not None:
            self.sink(entry)

    def by_role(self) -> dict[Role, Totals]:
        return self._group(lambda e: e.role)

    def by_unit(self) -> dict[str | None, Totals]:
        return self._group(lambda e: e.unit_id)

    def by_model(self) -> dict[str, Totals]:
        return self._group(lambda e: f"{e.provider}/{e.model}")

    def _group[K](self, key: Callable[[LedgerEntry], K]) -> dict[K, Totals]:
        groups: dict[K, Totals] = {}
        for entry in self.entries:
            groups.setdefault(key(entry), Totals()).add(entry)
        return groups

    def providers_used(self) -> list[str]:
        """Providers that received code, for the report (FR-8.5)."""
        return sorted({e.provider for e in self.entries})
