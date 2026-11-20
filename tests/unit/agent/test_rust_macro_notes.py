"""What a port from Rust's units tell the agent of the crate's own procedural macros (D-93):
each site's expansion with what to do with it, in a module's unit and a test unit; the macro
packages' tests at the retirement; and nothing for a unit without sites, whose message stays
as it was (the recorded runs of crates without macros replay unchanged)."""

from __future__ import annotations

from pathlib import Path

from codecoremorph.agent.context import (
    macro_sites_note,
    macro_tests_note,
    rust_macro_sites,
    rust_macro_tests,
    unit_message,
)
from codecoremorph.core.models import (
    BuildTarget,
    MacroKind,
    MacroSite,
    PortUnit,
    PortUnits,
    TargetKind,
    TestEntry,
    WorkUnit,
)
from codecoremorph.store.index import IndexStore

DERIVE = MacroSite(
    file="src/msg.rs",
    line=7,
    column=45,
    macro="Encode",
    kind=MacroKind.DERIVE,
    package="wire_macros",
    expansion="impl ::wire::codec::Encode for Header {\n    fn encode(&self) {}\n}",
    items=["wire::msg::Header::encode"],
)
TABLE = MacroSite(
    file="src/crc.rs",
    line=5,
    column=27,
    macro="crc_table",
    kind=MacroKind.FUNCTION,
    package="wire_macros",
    expansion="[0x0000u16, 0xC0C1u16]",
)


def test_a_modules_sites_and_what_to_do_with_them() -> None:
    note = macro_sites_note([DERIVE, TABLE], PortUnits.RUST_MODULE)
    assert note.startswith("The unit has 2 sites of the crate's own procedural macros.")
    assert "src/msg.rs:7, the derive `Encode` of wire_macros:" in note
    assert "```rust\nimpl ::wire::codec::Encode for Header {" in note
    assert "src/crc.rs:5, the macro `crc_table!` of wire_macros:" in note
    assert "not from the macros' code" in note
    assert "The façade uses none of the crate's own macros" in note
    assert "c/symbol-map.toml with the module's items: wire::msg::Header::encode." in note
    assert macro_sites_note([], PortUnits.RUST_MODULE) == ""


def test_a_test_units_sites() -> None:
    probe = DERIVE.model_copy(update={"file": "tests/frames.rs", "items": []})
    note = macro_sites_note([probe], PortUnits.TESTS)
    assert "The unit has 1 site of the crate's own procedural macros. As the compiler" in note
    assert "static functions of the program" in note
    assert "symbol-map" not in note


def test_a_long_expansion_is_cut() -> None:
    long = TABLE.model_copy(update={"expansion": "\n".join(["0u16,"] * 450)})
    note = macro_sites_note([long], PortUnits.RUST_MODULE)
    assert "// [50 more lines]" in note


def test_the_retirements_note_on_the_macro_packages_tests() -> None:
    note = macro_tests_note(["nextest:wire_macros::tests::the_arc_table"])
    assert "deleted the crate's procedural macro packages" in note
    assert "- nextest:wire_macros::tests::the_arc_table" in note
    assert macro_tests_note([]) == ""


def _store(tmp_path: Path, sites: list[MacroSite]) -> Path:
    path = tmp_path / "index.sqlite"
    with IndexStore.open(path, reset=True) as store:
        store.set_macro_sites(sites)
        store.set_build_targets(
            [
                BuildTarget(name="wire", kind=TargetKind.LIBRARY, files=["src/msg.rs"]),
                BuildTarget(
                    name="wire_macros",
                    kind=TargetKind.LIBRARY,
                    files=["macros/src/lib.rs"],
                    proc_macro=True,
                ),
            ]
        )
        store.set_test_entries(
            [
                TestEntry(test_id="nextest:wire::msg::tests::a", runs="wire", file="src/msg.rs"),
                TestEntry(
                    test_id="nextest:wire_macros::tests::the_arc_table",
                    runs="wire_macros",
                    file="macros/src/lib.rs",
                ),
            ]
        )
    return path


def test_read_from_the_index(tmp_path: Path) -> None:
    unexpanded = TABLE.model_copy(update={"expansion": None})
    reexport = DERIVE.model_copy(update={"line": 2, "reexport": True, "expansion": None})
    path = _store(tmp_path, [DERIVE, unexpanded, reexport])
    assert rust_macro_sites(path, ["src/msg.rs", "src/crc.rs"]) == [DERIVE]
    assert rust_macro_sites(path, ["src/codec.rs"]) == []
    assert rust_macro_tests(path) == ["nextest:wire_macros::tests::the_arc_table"]
    assert rust_macro_sites(None, ["src/msg.rs"]) == []
    assert rust_macro_tests(tmp_path / "none.sqlite") == []


def test_in_the_units_message(tmp_path: Path) -> None:
    """A module unit with sites gets them after its port note; one without gets the same
    message as before; the retirement names the macro package's tests."""
    path = _store(tmp_path, [DERIVE])
    (tmp_path / "src").mkdir()
    for name in ("msg.rs", "codec.rs"):
        (tmp_path / "src" / name).write_text("// the module\n", encoding="utf-8")

    def message(module: str, rule: str = "port_rust_module", **kw: object) -> str:
        port = PortUnit(build_target="wire", crate="wire", module=module, **kw)  # type: ignore[arg-type]
        unit = WorkUnit(id="U-0001", seq=1, rules=[rule], files=[f"src/{module}.rs"], port=port)
        return unit_message(
            unit,
            worktree=tmp_path,
            index_path=path,
            codemods=None,
            remaining={},
            build=None,
            port=True,
            port_units=PortUnits.RUST_MODULE,
            rust_port=True,
        )

    with_sites = message("msg")
    assert "The unit has 1 site of the crate's own procedural macros" in with_sites
    assert with_sites.index("Port the module to C") < with_sites.index("The unit has 1 site")
    without = message("codec")
    assert "procedural macros" not in without
    retire = WorkUnit(id="U-0009", seq=9, rules=["retire_rust"], files=["Cargo.toml"])
    text = unit_message(
        retire,
        worktree=tmp_path,
        index_path=path,
        codemods=None,
        remaining={},
        build=None,
        port=True,
        port_units=PortUnits.RETIREMENT,
        rust_port=True,
    )
    assert "- nextest:wire_macros::tests::the_arc_table" in text
