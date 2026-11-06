"""A run's session pieces: each side of a port (D-56, D-57), the pair's toolchain, and the
worktree's checkout (D-68)."""

from __future__ import annotations

import pytest

from codecoremorph.adapters.config import is_port, language_config, target_language_config
from codecoremorph.adapters.cpp import CppAdapter
from codecoremorph.adapters.rust import RustAdapter
from codecoremorph.config import ConfigValidationError, parse_config
from codecoremorph.orchestrator.session import checkout_for, toolchain_spec
from codecoremorph.sandbox import ExecutorChoice

PORT = parse_config(
    'version: "1.0"\nproject: {name: demo, exclude: ["third_party/**"]}\nmigration:\n'
    "  source: {language: cpp, standard: cpp17}\n"
    "  target: {language: rust, standard: rust2021, rules: [port_c_abi_module]}\n"
).config
UPGRADE = parse_config(
    'version: "1.0"\nproject: {name: demo}\nmigration:\n'
    "  source: {language: cpp, standard: cpp11}\n"
    "  target: {language: cpp, standard: cpp20}\n"
).config


def test_each_side_of_a_port_has_its_own_language() -> None:
    assert is_port(PORT)
    assert not is_port(UPGRADE)
    source = language_config(PORT)
    assert (source.language, source.standard, source.target_standard) == ("cpp", "cpp17", None)
    assert source.build_system == "cmake"
    target = target_language_config(PORT)
    assert (target.language, target.standard, target.target_standard) == (
        "rust",
        "rust2021",
        "rust2021",
    )
    assert target.build_system == "cargo"
    assert target.exclude == ["third_party/**"]
    assert language_config(UPGRADE).target_standard == "cpp20"


def test_a_port_runs_with_the_target_toolchain_and_both_tools() -> None:
    spec = toolchain_spec(PORT, CppAdapter(), RustAdapter())
    assert (spec.language, spec.image) == ("rust", "codecoremorph-rust")
    names = [t.name for t in spec.tools]
    assert {"clang++", "cmake", "ctest", "cargo", "cargo-nextest", "cbindgen"} <= set(names)
    assert len(names) == len(set(names))
    assert toolchain_spec(UPGRADE, CppAdapter()).image == "codecoremorph-cpp"


def test_keeping_the_source_api_is_for_ports() -> None:
    kept = parse_config(
        'version: "1.0"\nproject: {name: demo}\nmigration:\n'
        "  source: {language: cpp, standard: cpp98}\n"
        "  target: {language: rust, standard: rust2021, rules: [port_module, retire_cpp],\n"
        "           keep_source_api: true}\n"
    ).config
    assert kept.migration.target.keep_source_api
    assert not PORT.migration.target.keep_source_api
    with pytest.raises(ConfigValidationError) as raised:
        parse_config(
            'version: "1.0"\nproject: {name: demo}\nmigration:\n'
            "  source: {language: cpp, standard: cpp11}\n"
            "  target: {language: cpp, standard: cpp20, keep_source_api: true}\n"
        )
    assert any("keep_source_api is for a port" in i.message for i in raised.value.issues)


def test_a_linux_container_gets_a_linux_checkout() -> None:
    """D-68: a CRLF `configure` fails in the container; natively the machine's settings hold."""
    assert checkout_for(ExecutorChoice(kind="docker", reason="Docker is available")) == "lf"
    assert checkout_for(ExecutorChoice(kind="native", reason="sandbox.mode is native")) == "native"
