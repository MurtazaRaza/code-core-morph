"""What a port from Rust's units tell the agent (D-75): where the C goes, the public items to
map, the tests to translate, the program's entry point, the retirement."""

from __future__ import annotations

from codecoremorph.agent.context import rust_port_note
from codecoremorph.core.models import PortUnit, PortUnits, WorkUnit


def _unit(port: PortUnit | None, rule: str = "port_rust_module") -> WorkUnit:
    return WorkUnit(
        id="U-0001", seq=1, rules=[rule], files=["Cargo.toml", "src/stats.rs"], port=port
    )


def test_a_modules_note_names_its_c_and_what_to_map() -> None:
    port = PortUnit(
        build_target="stats",
        crate="stats",
        module="stats",
        callers=["src/text.rs"],
        exports=["stats::stats::mean", "stats::stats::Histogram"],
    )
    note = rust_port_note(_unit(port), PortUnits.RUST_MODULE)
    assert "c/src/stats.c" in note
    assert "c/include/stats/stats.h" in note
    assert "don't change: src/text.rs" in note
    assert "- stats::stats::mean\n- stats::stats::Histogram" in note
    assert "c/symbol-map.toml" in note


def test_tests_programs_and_the_retirement() -> None:
    tests = PortUnit(
        build_target="stats",
        crate="stats",
        module="test_stats",
        tests=["nextest:stats::stats::tests::mean_of_three"],
    )
    note = rust_port_note(_unit(tests, "port_rust_tests"), PortUnits.TESTS)
    assert "c/tests/test_stats.c" in note
    assert "- nextest:stats::stats::tests::mean_of_three" in note
    assert "c/test-map.toml" in note
    program = PortUnit(build_target="stats", crate="stats", module="tool", bin="tool")
    note = rust_port_note(_unit(program, "port_rust_program"), PortUnits.EXECUTABLE)
    assert "int stats_tool_main(int argc, char **argv)" in note
    note = rust_port_note(_unit(None, "retire_rust"), PortUnits.RETIREMENT)
    assert "CMake build now builds and tests the C alone" in note
