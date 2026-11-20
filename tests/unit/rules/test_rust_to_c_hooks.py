"""The ``rust_to_c`` pack's hook post-condition (D-93): a ported module's façade uses none of
the crate's own procedural macros, so the C, not their Rust, does what they write."""

from __future__ import annotations

import shutil
from pathlib import Path

from codecoremorph.planner.sites import load_hook
from codecoremorph.rules.packs.rust_to_c.hooks import macro_sites_left
from codecoremorph.rules.registry import RuleRegistry

ROOT = Path(__file__).resolve().parents[3]
WIRE = ROOT / "fixtures" / "rust" / "wire"
FILES = ["Cargo.toml", "macros/Cargo.toml", "src/lib.rs", "src/crc.rs", "src/msg.rs"]


def _copy(tmp_path: Path) -> Path:
    work = tmp_path / "wire"
    shutil.copytree(WIRE, work)
    return work


def test_every_use_of_the_crates_own_macros_is_a_site(tmp_path: Path) -> None:
    work = _copy(tmp_path)
    found = [(s.file, s.line, s.text.split(":")[0]) for s in macro_sites_left(work, FILES)]
    assert found == [
        ("src/crc.rs", 3, "uses wire_macros, the crate's macro package"),
        ("src/crc.rs", 5, "crc_table!, a macro of the crate's own"),
        ("src/msg.rs", 5, "uses wire_macros, the crate's macro package"),
        ("src/msg.rs", 7, "derives Decode, a macro of the crate's own"),
        ("src/msg.rs", 7, "derives Encode, a macro of the crate's own"),
        ("src/msg.rs", 13, "derives Decode, a macro of the crate's own"),
        ("src/msg.rs", 13, "derives Encode, a macro of the crate's own"),
        ("src/msg.rs", 23, "derives Decode, a macro of the crate's own"),
        ("src/msg.rs", 23, "derives Encode, a macro of the crate's own"),
        ("src/msg.rs", 42, "#[throws], a macro of the crate's own"),
        ("src/msg.rs", 67, "#[throws], a macro of the crate's own"),
    ]


def test_a_facade_without_them_passes(tmp_path: Path) -> None:
    """The standard derives, the trait implemented by hand, a comment or a string naming a
    macro, and the crate root's ``extern crate`` line are no site."""
    work = _copy(tmp_path)
    (work / "src" / "msg.rs").write_text(
        "use crate::codec::{Decode, DecodeError, Encode};\n"
        "// derive(Encode) and #[throws] wrote this; the C does it now (wire_macros::Encode)\n"
        "#[derive(Debug, Clone, PartialEq)]\n"
        "pub struct Header { pub kind: u8 }\n"
        "impl Encode for Header {\n"
        '    fn encode(&self, out: &mut Vec<u8>) { let _ = "crc_table!(1)"; }\n'
        "    fn encoded_len(&self) -> usize { 3 }\n"
        "}\n",
        encoding="utf-8",
    )
    # The module's tests stay Rust until their own unit, with what they derive.
    (work / "src" / "crc.rs").write_text(
        "pub fn crc16(_: &[u8]) -> u16 { 0 }\n"
        "#[cfg(test)]\nmod tests {\n    use wire_macros::Encode;\n"
        "    #[derive(Encode)]\n    struct Probe { id: u32 }\n}\n",
        encoding="utf-8",
    )
    lib = work / "src" / "lib.rs"
    lib.write_text(
        "#[macro_use]\nextern crate wire_macros;\n" + lib.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    assert macro_sites_left(work, FILES) == []


def test_paths_and_no_macro_package(tmp_path: Path) -> None:
    work = _copy(tmp_path)
    (work / "src" / "msg.rs").write_text(
        "#[wire_macros::throws(E)]\npub fn f() -> u8 { 1 }\n"
        "#[derive(wire_macros::Decode)]\npub struct A;\n",
        encoding="utf-8",
    )
    found = {s.text.split(":")[0] for s in macro_sites_left(work, ["src/msg.rs"])}
    assert found == {
        "#[throws], a macro of the crate's own",
        "derives Decode, a macro of the crate's own",
        "uses wire_macros, the crate's macro package",
    }
    shutil.rmtree(work / "macros")
    assert macro_sites_left(work, ["src/msg.rs"]) == []


def test_the_module_rule_runs_it() -> None:
    rule = RuleRegistry.discover([]).get("port_rust_module").rule
    hooks = [p.hook for p in rule.postconditions if p.hook]
    assert hooks == ["codecoremorph.rules.packs.rust_to_c.hooks:macro_sites_left"]
    assert load_hook(hooks[0]) is macro_sites_left
