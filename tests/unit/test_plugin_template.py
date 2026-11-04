"""The plugin template (templates/plugin) loads through the real registries (FR-15.3)."""

from __future__ import annotations

import tomllib
from importlib.metadata import EntryPoint
from pathlib import Path

import pytest

from codecoremorph.adapters.registry import AdapterRegistry
from codecoremorph.core.plugins import ADAPTER_API, PROVIDER_API, RULE_PACK_API
from codecoremorph.llm.base import ProviderRegistry
from codecoremorph.rules.detectors import find_pattern_sites
from codecoremorph.rules.registry import RuleRegistry

TEMPLATE = Path(__file__).resolve().parents[2] / "templates" / "plugin"


def entry_points(group: str) -> list[EntryPoint]:
    project = tomllib.loads((TEMPLATE / "pyproject.toml").read_text(encoding="utf-8"))
    declared: dict[str, str] = project["project"]["entry-points"][group]
    return [EntryPoint(name, value, group) for name, value in declared.items()]


@pytest.fixture(autouse=True)
def template_importable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(TEMPLATE / "src"))


def test_the_rule_pack_loads() -> None:
    registry = RuleRegistry.discover(eps=entry_points("codecoremorph.rules"))
    assert registry.problems == []
    assert registry.packs["example"].api_version == RULE_PACK_API.version
    rule = registry.get("example_bounded_sprintf").rule
    assert rule.detector is not None
    code = 'sprintf(out, "%d", n); // sprintf(x)\nsnprintf(out, sizeof out, "%d", n);\n'
    patterns = [spec.pattern for spec in rule.detector.pattern_specs()]
    sites = find_pattern_sites("a.cpp", code, patterns)
    assert [site.line for site in sites] == [1]


def test_the_adapter_loads() -> None:
    registry = AdapterRegistry.discover(entry_points("codecoremorph.adapters"))
    assert registry.problems == []
    assert registry.adapters["example"].adapter.api_version == ADAPTER_API.version
    assert registry.get("example").capabilities().standards == ["example1", "example2"]


def test_the_provider_loads() -> None:
    registry = ProviderRegistry.discover(entry_points("codecoremorph.providers"))
    assert registry.problems == []
    assert registry.info("example").plugin.api_version == PROVIDER_API.version


def test_the_template_depends_on_the_current_major() -> None:
    from codecoremorph import __version__

    project = tomllib.loads((TEMPLATE / "pyproject.toml").read_text(encoding="utf-8"))
    (requirement,) = project["project"]["dependencies"]
    major = int(__version__.split(".", 1)[0])
    assert requirement == f"codecoremorph>=0.1,<{major + 1}"
