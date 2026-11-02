"""Adapter discovery through entry points (FR-15.1)."""

from __future__ import annotations

import sys
import types
from importlib.metadata import EntryPoint

import pytest

from codecoremorph.adapters.base import AdapterNotSupportedError, LanguageAdapter
from codecoremorph.adapters.cpp import CppAdapter
from codecoremorph.adapters.registry import AdapterRegistry
from codecoremorph.core.errors import UsageError

MODULE = "codecoremorph_test_plugins"


class OldApi(CppAdapter):
    api_version = "0"


class SecondCpp(CppAdapter):
    pass


class NoLanguage(LanguageAdapter):  # abstract methods unimplemented; never instantiated
    pass


@pytest.fixture(autouse=True)
def plugin_module() -> types.ModuleType:
    module = types.ModuleType(MODULE)
    module.OldApi = OldApi  # type: ignore[attr-defined]
    module.SecondCpp = SecondCpp  # type: ignore[attr-defined]
    module.NoLanguage = NoLanguage  # type: ignore[attr-defined]
    module.not_a_class = 42  # type: ignore[attr-defined]
    sys.modules[MODULE] = module
    return module


def _ep(name: str, value: str) -> EntryPoint:
    return EntryPoint(name=name, value=value, group="codecoremorph.adapters")


def test_builtin_cpp_adapter_is_registered() -> None:
    registry = AdapterRegistry.discover()
    assert "cpp" in registry.adapters
    assert isinstance(registry.get("cpp"), CppAdapter)


def test_broken_plugins_are_reported_not_raised() -> None:
    registry = AdapterRegistry.discover(
        [
            _ep("a", "codecoremorph.adapters.cpp:CppAdapter"),
            _ep("b", f"{MODULE}:SecondCpp"),
            _ep("c", f"{MODULE}:OldApi"),
            _ep("d", f"{MODULE}:not_a_class"),
            _ep("e", "no_such_module_anywhere:Thing"),
            _ep("f", f"{MODULE}:NoLanguage"),
        ]
    )
    assert list(registry.adapters) == ["cpp"]
    problems = "\n".join(registry.problems)
    assert "already provided by a = codecoremorph.adapters.cpp:CppAdapter" in problems
    assert "implements adapter API 0" in problems
    assert "is not a LanguageAdapter subclass" in problems
    assert "can't be imported (ModuleNotFoundError" in problems
    assert "doesn't set `language`" in problems


def test_unknown_language_is_a_usage_error() -> None:
    with pytest.raises(UsageError, match="no adapter is installed for language 'cobol'"):
        AdapterRegistry.discover([]).get("cobol")


def test_optional_methods_default_to_not_supported() -> None:
    class Minimal(CppAdapter):
        pass

    adapter = Minimal()
    with pytest.raises(AdapterNotSupportedError):
        LanguageAdapter.parse_check(adapter, None, [])  # type: ignore[arg-type]
    with pytest.raises(AdapterNotSupportedError):
        adapter.query(None, "no_such_query", [])  # type: ignore[arg-type]
