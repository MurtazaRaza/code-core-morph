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
