"""Provider discovery (FR-15.1) and the doctor's model checks (FR-1.5)."""

from __future__ import annotations

from importlib.metadata import EntryPoint
from pathlib import Path
from typing import ClassVar, Self

import pytest

from codecoremorph.cli.doctor import check_providers, pull_command
from codecoremorph.config.models import LlmConfig, ProviderConfig
from codecoremorph.core.errors import UsageError
from codecoremorph.core.models import (
    CompletionRequest,
    CompletionResult,
    HealthStatus,
    ModelCapabilities,
)
from codecoremorph.llm.anthropic import AnthropicProvider
from codecoremorph.llm.base import LLMProvider, ProviderInfo, ProviderRegistry
from codecoremorph.llm.ollama import OllamaProvider


class FakeOllama:
    api_version: ClassVar[str] = "1"
    name: ClassVar[str] = "ollama"
    local: ClassVar[bool] = True
    pulled: ClassVar[set[str]] = {"qwen3:4b"}

    @classmethod
    def from_config(cls, config: ProviderConfig | None, llm: LlmConfig) -> Self:
        return cls()

    def capabilities(self, model: str) -> ModelCapabilities:
        return ModelCapabilities()

    def complete(self, request: CompletionRequest) -> CompletionResult:
        raise NotImplementedError

    def count_tokens(self, request: CompletionRequest) -> int:
        return 0

    def health_check(self, model: str | None = None) -> HealthStatus:
        if model in self.pulled:
            return HealthStatus(ok=True, detail=f"{model} is pulled")
        return HealthStatus(ok=False, detail=f"{model} isn't pulled; run: ollama pull {model}")


def test_builtin_providers_are_discovered() -> None:
    registry = ProviderRegistry.discover()
    assert registry.problems == []
    assert registry.providers["anthropic"].plugin is AnthropicProvider
    assert registry.providers["ollama"].plugin is OllamaProvider
    created = registry.create("ollama", LlmConfig())
    assert isinstance(created, LLMProvider)
    with pytest.raises(UsageError, match="no LLM provider named 'nope'"):
        registry.create("nope", LlmConfig())


def test_broken_plugins_are_reported_not_raised() -> None:
    group = "codecoremorph.providers"
    eps = [
        EntryPoint("missing", "codecoremorph.no_such_module:Provider", group),
        EntryPoint("wrongname", "codecoremorph.llm.ollama:OllamaProvider", group),
        EntryPoint("notaclass", "codecoremorph.llm.base:SAMPLING_OPTIONS", group),
    ]
    registry = ProviderRegistry.discover(eps)
    assert registry.providers == {}
    assert [p.split(":", 1)[0] for p in registry.problems] == [
        "missing = codecoremorph.no_such_module",
        "notaclass = codecoremorph.llm.base",
        "wrongname = codecoremorph.llm.ollama",
    ]


class FutureOllama(FakeOllama):
    api_version: ClassVar[str] = "2"


def test_providers_declare_the_api_they_implement(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys
    import types

    module = types.ModuleType("ccm_test_provider_plugins")
    module.FutureOllama = FutureOllama  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "ccm_test_provider_plugins", module)
    eps = [
        EntryPoint("ollama", "ccm_test_provider_plugins:FutureOllama", "codecoremorph.providers")
    ]
    registry = ProviderRegistry.discover(eps)
    assert registry.providers == {}
    assert (
        "implements provider API 2, but this CodeCoreMorph supports provider API 1"
        in (registry.problems[0])
    )
    with pytest.raises(UsageError, match="the ollama provider can't be used"):
        registry.info("ollama")


MANIFEST = """\
version: "1.0"
project: {name: demo}
migration:
  source: {language: cpp, standard: cpp11}
  target: {language: cpp, standard: cpp20}
llm:
  roles:
    editor: {provider: ollama, model: qwen3:4b, num_ctx: 32768}
    fixer: {provider: ollama, model: coder:7b, num_ctx: 32768}
    summarizer: {provider: ollama, model: tiny:1b}
"""


def test_doctor_checks_each_configured_model(tmp_path: Path) -> None:
    config = tmp_path / "migration_config.yaml"
    config.write_text(MANIFEST, encoding="utf-8")
    registry = ProviderRegistry(
        providers={"ollama": ProviderInfo("ollama", FakeOllama, "ollama = fake", None)}
    )
    results = check_providers(config, registry)
    assert [(r.name, r.status) for r in results] == [
        ("model ollama/qwen3:4b", "ok"),
        ("model ollama/coder:7b", "fail"),
        ("model ollama/tiny:1b", "fail"),
        ("models", "info"),
    ]
    assert results[1].hint == f"Run: {pull_command(['coder:7b'])}"
    assert results[-1].hint == f"Run: {pull_command(['coder:7b', 'tiny:1b'])}"


def test_doctor_skips_models_when_the_manifest_configures_none(tmp_path: Path) -> None:
    """Regression: with no llm section (a host agent makes the edits, D-41), doctor reported
    the default Anthropic role's missing credentials as a failure."""
    config = tmp_path / "migration_config.yaml"
    config.write_text(MANIFEST.split("llm:")[0], encoding="utf-8")
    results = check_providers(config, ProviderRegistry())  # no provider is ever created
    assert [(r.name, r.status) for r in results] == [("models", "info")]
    assert "Claude Code" in (results[0].hint or "")


def test_pull_command_per_platform() -> None:
    assert pull_command(["a:1", "b:2"], "win32") == r"scripts\pull-models.bat a:1 b:2"
    assert pull_command(["a:1"], "linux") == "./scripts/pull-models.sh a:1"
