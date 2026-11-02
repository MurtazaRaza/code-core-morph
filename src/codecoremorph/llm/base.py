"""The LLM provider interface and its plugin registry (SPEC §7.7, FR-8.1, FR-15.1).

Every LLM call goes through an :class:`LLMProvider`; provider SDKs are imported only inside
``codecoremorph.llm``. Providers register under the ``codecoremorph.providers`` entry point
as a class with a ``from_config`` constructor, like the built-in ``anthropic`` and ``ollama``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from importlib.metadata import EntryPoint, entry_points
from typing import ClassVar, Protocol, Self, runtime_checkable

from codecoremorph.config.models import LlmConfig, ProviderConfig
from codecoremorph.core.errors import UsageError
from codecoremorph.core.models import (
    CompletionRequest,
    CompletionResult,
    HealthStatus,
    ModelCapabilities,
)

log = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "codecoremorph.providers"
PROVIDER_API_VERSION = "1"

# Universal sampling knobs are not part of the contract (SPEC §7.7, D-18). Anthropic rejects
# them outright; a provider that takes them reads them from its own provider_options.
SAMPLING_OPTIONS = frozenset({"temperature", "top_p", "top_k"})


@runtime_checkable
class LLMProvider(Protocol):
    """A model provider.

    ``complete`` makes one call (streamed where the provider supports it) and returns the
    whole answer. It raises the ``codecoremorph.core.errors.LLMError`` subclasses, never an
    SDK exception. A refusal is a result (``StopReason.REFUSAL``), not an exception.
    """

    name: str

    def capabilities(self, model: str) -> ModelCapabilities: ...

    def complete(self, request: CompletionRequest) -> CompletionResult: ...

    def count_tokens(self, request: CompletionRequest) -> int: ...

    def health_check(self, model: str | None = None) -> HealthStatus: ...


def close_provider(provider: object) -> None:
    """Release a provider's connections when it has a ``close()`` (optional in the API)."""
    close = getattr(provider, "close", None)
    if callable(close):
        close()


class ProviderPlugin(Protocol):
    """What a ``codecoremorph.providers`` entry point names."""

    api_version: ClassVar[str]
    name: ClassVar[str]
    local: ClassVar[bool]  # runs on this machine: no code leaves it (FR-8.5)

    @classmethod
    def from_config(cls, config: ProviderConfig | None, llm: LlmConfig) -> Self: ...


@dataclass(frozen=True)
class ProviderInfo:
    name: str
    plugin: type[ProviderPlugin]
    entry_point: str
    distribution: str | None


@dataclass
class ProviderRegistry:
    providers: dict[str, ProviderInfo] = field(default_factory=dict[str, ProviderInfo])
    problems: list[str] = field(default_factory=list[str])

    @classmethod
    def discover(cls, eps: list[EntryPoint] | None = None) -> ProviderRegistry:
        """Load every registered provider. Broken plugins are reported, not raised."""
        registry = cls()
        found = list(entry_points(group=ENTRY_POINT_GROUP)) if eps is None else eps
        for ep in sorted(found, key=lambda e: e.name):
            registry._load(ep)
        return registry

    def _load(self, ep: EntryPoint) -> None:
        where = f"{ep.name} = {ep.value}"
        try:
            loaded: object = ep.load()
        except Exception as exc:  # noqa: BLE001 - a broken plugin must not break the CLI
            self.problems.append(f"{where}: can't be imported ({type(exc).__name__}: {exc})")
            return
        if not isinstance(loaded, type) or not callable(getattr(loaded, "from_config", None)):
            self.problems.append(f"{where}: is not a provider class with from_config()")
            return
        api = getattr(loaded, "api_version", None)
        if api != PROVIDER_API_VERSION:
            self.problems.append(
                f"{where}: implements provider API {api}, but this CodeCoreMorph supports "
                f"API {PROVIDER_API_VERSION}"
            )
            return
        name = getattr(loaded, "name", None)
        if name != ep.name:
            self.problems.append(f"{where}: its name {name!r} doesn't match the entry point")
            return
        distribution = ep.dist.name if ep.dist is not None else None
        plugin: type[ProviderPlugin] = loaded  # pyright: ignore[reportAssignmentType]
        self.providers[ep.name] = ProviderInfo(ep.name, plugin, where, distribution)

    def info(self, name: str) -> ProviderInfo:
        found = self.providers.get(name)
        if found is None:
            available = ", ".join(sorted(self.providers)) or "none"
            raise UsageError(
                f"no LLM provider named {name!r} is installed",
                reason=f"installed providers: {available}",
                hint="Check the provider of each role under llm.roles, or install the plugin.",
            )
        return found

    def create(self, name: str, llm: LlmConfig) -> LLMProvider:
        provider = self.info(name).plugin.from_config(llm.providers.get(name), llm)
        if not isinstance(provider, LLMProvider):
            raise UsageError(f"the {name} provider plugin doesn't implement LLMProvider")
        return provider
