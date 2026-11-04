"""Checks that CodeCoreMorph finds and loads this plugin. Install it first: `pip install -e .`"""

from __future__ import annotations

from codecoremorph.adapters.registry import AdapterRegistry
from codecoremorph.llm.base import ProviderRegistry
from codecoremorph.rules.registry import RuleRegistry


def test_the_rule_pack_loads() -> None:
    registry = RuleRegistry.discover()
    assert registry.problems == []
    assert registry.get("example_bounded_sprintf").pack == "example"


def test_the_adapter_loads() -> None:
    registry = AdapterRegistry.discover()
    assert registry.problems == []
    assert registry.get("example").capabilities().standards == ["example1", "example2"]


def test_the_provider_loads() -> None:
    registry = ProviderRegistry.discover()
    assert registry.problems == []
    assert registry.info("example").plugin.local is False
