"""The skeleton of an LLM provider (SPEC §7.7).

A provider makes model calls for CodeCoreMorph's own agent loop. It returns refusals as
results, raises ``codecoremorph.core.errors.LLMError`` subclasses (never an SDK's exceptions),
and takes no universal sampling settings: provider-specific options arrive in
``request.provider_options``. The built-in ``codecoremorph.llm.ollama`` shows a complete one.
"""

from __future__ import annotations

from typing import ClassVar, Self

from codecoremorph.config.models import LlmConfig, ProviderConfig
from codecoremorph.core.models import (
    CompletionRequest,
    CompletionResult,
    HealthStatus,
    ModelCapabilities,
)


class ExampleProvider:
    api_version: ClassVar[str] = "1"  # the provider API this class implements (FR-15.3)
    name: ClassVar[str] = "example"  # what llm.roles.<role>.provider names; = the entry point
    local: ClassVar[bool] = False  # True only if no code leaves the machine (FR-8.5)

    def __init__(self, config: ProviderConfig | None) -> None:
        self.config = config  # llm.providers.example: base_url, timeout_s, ...; no credentials

    @classmethod
    def from_config(cls, config: ProviderConfig | None, llm: LlmConfig) -> Self:
        return cls(config)

    def capabilities(self, model: str) -> ModelCapabilities:
        return ModelCapabilities()

    def complete(self, request: CompletionRequest) -> CompletionResult:
        raise NotImplementedError("make one model call and return the whole answer")

    def count_tokens(self, request: CompletionRequest) -> int:
        raise NotImplementedError("count the request's input tokens the way the service does")

    def health_check(self, model: str | None = None) -> HealthStatus:
        return HealthStatus(ok=False, detail="the example provider isn't implemented yet")
