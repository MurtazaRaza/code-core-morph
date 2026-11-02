"""What CodeCoreMorph knows about a model before asking its provider (FR-5.4, FR-5.5).

The package data in ``models.json`` holds context windows and prices for the models
CodeCoreMorph defaults to. The manifest overrides them: ``llm.providers.<p>.prices`` for prices
and a role's ``num_ctx`` for an Ollama context window. Live capability discovery replaces the
context windows in Phase 4. This module imports no provider SDK.
"""

from __future__ import annotations

import json
from functools import cache
from importlib import resources
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field

from codecoremorph.config.models import LlmConfig, ModelPrice, RoleModelConfig

# Providers that run on the user's machine: no cost, and no code leaves it (FR-8.5).
LOCAL_PROVIDERS = frozenset({"ollama"})


class ModelInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str
    model: str
    context_window: int | None = Field(default=None, description="None when unknown")
    max_output_tokens: int | None = None
    prices: ModelPrice | None = Field(default=None, description="None when unknown or free")
    local: bool = False

    @property
    def name(self) -> str:
        return f"{self.provider}/{self.model}"


@cache
def _catalog() -> dict[str, dict[str, Any]]:
    data = resources.files("codecoremorph.llm").joinpath("models.json").read_text("utf-8")
    raw = cast(dict[str, Any], json.loads(data))
    return {k: cast(dict[str, Any], v) for k, v in raw.items() if not k.startswith("_")}


def model_info(role: RoleModelConfig, llm: LlmConfig) -> ModelInfo:
    """The context window and prices of the model serving ``role``."""
    entry = _catalog().get(role.provider, {}).get(role.model)
    context: int | None = None
    max_output: int | None = None
    prices: ModelPrice | None = None
    if entry is not None:
        context = int(entry["context_window"])
        max_output = int(entry["max_output_tokens"])
        prices = ModelPrice.model_validate(entry["prices"])
    if role.num_ctx is not None:
        context = role.num_ctx
    provider = llm.providers.get(role.provider)
    if provider is not None and role.model in provider.prices:
        prices = provider.prices[role.model]
    local = role.provider in LOCAL_PROVIDERS
    return ModelInfo(
        provider=role.provider,
        model=role.model,
        context_window=context,
        max_output_tokens=max_output,
        prices=None if local else prices,
        local=local,
    )


def prices_for(provider: str, model: str, llm: LlmConfig) -> ModelPrice | None:
    """Prices for ``model``: the manifest's override, else package data. None when unknown or
    the provider is local."""
    if provider in LOCAL_PROVIDERS:
        return None
    configured = llm.providers.get(provider)
    if configured is not None and model in configured.prices:
        return configured.prices[model]
    entry = _catalog().get(provider, {}).get(model)
    return None if entry is None else ModelPrice.model_validate(entry["prices"])


def cost_usd(
    prices: ModelPrice | None,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float | None:
    """The cost of one call, or None without prices. Cache reads and writes fall back to the
    input price when the table has no separate rate."""
    if prices is None:
        return None
    read = prices.cache_read_usd_per_mtok
    write = prices.cache_write_usd_per_mtok
    total = (
        input_tokens * prices.input_usd_per_mtok
        + output_tokens * prices.output_usd_per_mtok
        + cache_read_tokens * (prices.input_usd_per_mtok if read is None else read)
        + cache_write_tokens * (prices.input_usd_per_mtok if write is None else write)
    )
    return round(total / 1_000_000, 6)


def package_capabilities(provider: str, model: str) -> tuple[int, int] | None:
    """``(context window, max output tokens)`` from package data, or None."""
    entry = _catalog().get(provider, {}).get(model)
    if entry is None:
        return None
    return int(entry["context_window"]), int(entry["max_output_tokens"])
