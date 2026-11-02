"""The one door to models for the rest of the engine: routing, provider instances, the
ledger and its caps (SPEC §7.7)."""

from __future__ import annotations

import logging
from collections.abc import Mapping

from codecoremorph.config.models import LlmConfig
from codecoremorph.core.models import (
    CompletionRequest,
    CompletionResult,
    ModelCapabilities,
    Role,
)
from codecoremorph.llm.base import LLMProvider, ProviderRegistry, close_provider
from codecoremorph.llm.catalog import package_capabilities
from codecoremorph.llm.ledger import LedgerEntry, UsageLedger
from codecoremorph.llm.routing import Route, Router

log = logging.getLogger(__name__)


class LLMService:
    def __init__(
        self,
        llm: LlmConfig,
        *,
        ledger: UsageLedger | None = None,
        registry: ProviderRegistry | None = None,
        providers: Mapping[str, LLMProvider] | None = None,
    ):
        """``providers`` replaces registry lookups by name (tests, replay, recording)."""
        self.llm = llm
        self.router = Router(llm)
        self.ledger = ledger or UsageLedger()
        self._registry = registry
        self._providers: dict[str, LLMProvider] = dict(providers or {})

    def provider(self, name: str) -> LLMProvider:
        if name not in self._providers:
            if self._registry is None:
                self._registry = ProviderRegistry.discover()
            self._providers[name] = self._registry.create(name, self.llm)
        return self._providers[name]

    def close(self) -> None:
        """Release every provider's connections."""
        for provider in self._providers.values():
            close_provider(provider)

    def route(self, role: Role, *, failed_attempts: int = 0) -> Route:
        return self.router.route(role, failed_attempts=failed_attempts)

    def capabilities(self, route: Route) -> ModelCapabilities:
        """The model's limits; a role's ``num_ctx`` is its context window (Ollama)."""
        num_ctx = route.options.get("num_ctx")
        try:
            caps = self.provider(route.provider).capabilities(route.model)
        except Exception as exc:  # noqa: BLE001 - discovery is best effort; package data stands in
            log.debug("capability discovery for %s failed: %s", route.name, exc)
            known = package_capabilities(route.provider, route.model)
            caps = ModelCapabilities(
                context_window=known[0] if known else None,
                max_output_tokens=known[1] if known else None,
                source="package" if known else "default",
            )
        if isinstance(num_ctx, int):
            caps = caps.model_copy(update={"context_window": num_ctx, "source": "config"})
        return caps

    def complete(
        self,
        route: Route,
        request: CompletionRequest,
        *,
        unit_id: str | None = None,
        attempt: int | None = None,
    ) -> CompletionResult:
        """One call, checked against the run's caps first and recorded after."""
        self.ledger.check()
        result = self.provider(route.provider).complete(request)
        self.ledger.record(
            LedgerEntry(
                role=route.role,
                provider=route.provider,
                model=result.model,
                usage=result.usage,
                cost_usd=result.cost_usd,
                latency_s=result.latency_s,
                stop_reason=result.stop_reason,
                unit_id=unit_id,
                attempt=attempt,
            )
        )
        return result
