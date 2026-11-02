"""Model context windows and prices before live discovery (FR-5.4, FR-5.5)."""

from __future__ import annotations

from codecoremorph.config.models import LlmConfig, ModelPrice, ProviderConfig, RoleModelConfig
from codecoremorph.core.models import Role
from codecoremorph.llm.catalog import model_info


def test_model_info_from_package_data_and_config() -> None:
    llm = LlmConfig()
    editor = llm.for_role(Role.EDITOR)  # the default: claude-opus-5-5
    info = model_info(editor, llm)
    assert info.name == "anthropic/claude-opus-5-5"
    assert info.context_window == 1_000_000
    assert info.prices is not None
    assert info.prices.input_usd_per_mtok == 4.0
    assert not info.local

    cheaper = ModelPrice(input_usd_per_mtok=1.0, output_usd_per_mtok=2.0)
    overridden = LlmConfig(
        providers={"anthropic": ProviderConfig(prices={"claude-opus-5-5": cheaper})}
    )
    assert model_info(editor, overridden).prices == cheaper

    unknown = model_info(RoleModelConfig(provider="anthropic", model="not-listed"), llm)
    assert (unknown.context_window, unknown.prices) == (None, None)

    ollama = model_info(RoleModelConfig(provider="ollama", model="coder", num_ctx=32_768), llm)
    assert (ollama.context_window, ollama.prices, ollama.local) == (32_768, None, True)
