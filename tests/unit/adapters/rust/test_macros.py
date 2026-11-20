"""A crate's own procedural macros as a port's source (D-93): the macro packages and what they
define, the sites SCIP finds (rust-analyzer's index of ``fixtures/rust/wire``, recorded), what
each expansion defines, and the index that records them with rust-analyzer's expansions
(recorded too), which a second index takes from the cache while nothing they depend on
changed."""

from __future__ import annotations

import json
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from codecoremorph.adapters.base import AdapterContext
from codecoremorph.adapters.rust import RustAdapter
from codecoremorph.adapters.rust.scip import Document, Occurrence, macro_symbol, read_index
from codecoremorph.adapters.rust.source import (
    cfg_test_lines,
    expansion_items,
    macro_definitions,
    macro_sites,
    proc_macro_package,
)
from codecoremorph.core.models import ExecResult, LanguageConfig, MacroKind, MacroSite
from codecoremorph.store.index import IndexStore

ROOT = Path(__file__).resolve().parents[4]
WIRE = ROOT / "fixtures" / "rust" / "wire"
DATA = Path(__file__).with_name("data")
MACROS = {
    "Encode": MacroKind.DERIVE,
    "Decode": MacroKind.DERIVE,
    "throws": MacroKind.ATTRIBUTE,
    "crc_table": MacroKind.FUNCTION,
}


def _texts(base: Path) -> dict[str, str]:
    return {
        p.relative_to(base).as_posix(): p.read_text(encoding="utf-8")
        for p in sorted(base.rglob("*.rs"))
        if "macros" not in p.relative_to(base).parts
    }


def test_a_macro_package_and_what_it_defines() -> None:
    assert proc_macro_package((WIRE / "macros" / "Cargo.toml").read_text(encoding="utf-8"))
    assert not proc_macro_package((WIRE / "Cargo.toml").read_text(encoding="utf-8"))
    assert proc_macro_package('[package]\nname = "m"\n[lib]\nproc_macro = true\n')
    assert not proc_macro_package('[package]\nname = "m"\n')
    text = (WIRE / "macros" / "src" / "lib.rs").read_text(encoding="utf-8")
    assert macro_definitions({"src/lib.rs": text}) == MACROS


def test_a_macro_symbol() -> None:
    assert macro_symbol("rust-analyzer cargo wire_macros 0.3.0 Encode:") == (
        "wire_macros",
        "Encode",
    )
    assert macro_symbol("rust-analyzer cargo wire_macros 0.3.0 crc_table!") == (
        "wire_macros",
        "crc_table",
    )
    assert macro_symbol("rust-analyzer cargo wire 0.3.0 msg/Header#") is None
    assert macro_symbol("rust-analyzer cargo wire_macros 0.3.0 crate/") is None
    assert macro_symbol("local 3") is None


def test_the_sites_scip_names() -> None:
    """Every derive, attribute and call of the crate's own macros, in the library and its
    tests; not the ``use`` lines, nor the derive's helper attribute ``#[wire(skip)]``."""
    documents = read_index((DATA / "wire.scip").read_bytes())
    sites = macro_sites(documents, {"wire_macros": MACROS}, prefix="", files=_texts(WIRE))
    found = [(s.file, s.line, s.macro, s.kind) for s in sites]
    derive, attribute, function = MacroKind.DERIVE, MacroKind.ATTRIBUTE, MacroKind.FUNCTION
    assert found == [
        ("src/crc.rs", 5, "crc_table", function),
        ("src/msg.rs", 7, "Encode", derive),
        ("src/msg.rs", 7, "Decode", derive),
        ("src/msg.rs", 13, "Encode", derive),
        ("src/msg.rs", 13, "Decode", derive),
        ("src/msg.rs", 23, "Encode", derive),
        ("src/msg.rs", 23, "Decode", derive),
        ("src/msg.rs", 42, "throws", attribute),
        ("src/msg.rs", 67, "throws", attribute),
        ("tests/frames.rs", 6, "Encode", derive),
        ("tests/frames.rs", 6, "Decode", derive),
    ]
    assert not any(s.reexport for s in sites)


def test_uses_and_re_exports() -> None:
    """A ``use`` of a macro, over several lines too, is no site; a ``pub use`` is a re-export,
    which code outside the crate applies; a ``pub(crate) use`` stays inside."""
    text = (
        "use wire_macros::{\n"  # 1
        "    Encode,\n"  # 2
        "};\n"  # 3
        "pub use wire_macros::Decode;\n"  # 4
        "pub(crate) use wire_macros::throws;\n"  # 5
        "#[derive(Encode)]\n"  # 6
        "pub struct A;\n"  # 7
    )
    symbol = "rust-analyzer cargo wire_macros 0.3.0 {}"
    occurrences = (
        Occurrence(symbol.format("Encode:"), 2, 5, 0),
        Occurrence(symbol.format("Decode:"), 4, 22, 0),
        Occurrence(symbol.format("throws:"), 5, 30, 0),
        Occurrence(symbol.format("Encode:"), 6, 10, 0),
    )
    sites = macro_sites(
        [Document("src/lib.rs", occurrences)],
        {"wire_macros": MACROS},
        prefix="core/",
        files={"src/lib.rs": text},
    )
    assert [(s.file, s.line, s.macro, s.reexport) for s in sites] == [
        ("core/src/lib.rs", 4, "Decode", True),
        ("core/src/lib.rs", 6, "Encode", False),
    ]


def test_a_site_in_the_tests_is_theirs() -> None:
    """A site in a ``#[cfg(test)]`` item (a test-only type's derive) is the tests', which stay
    Rust until their own unit."""
    text = (
        "#[derive(Encode)]\n"  # 1
        "pub struct A;\n"  # 2
        "#[cfg(test)]\n"  # 3
        "mod tests {\n"  # 4
        "    #[derive(Encode)]\n"  # 5
        '    struct P { s: &\'static str }  // "}"\n'  # 6
        "}\n"  # 7
        "#[cfg(test)]\n"  # 8
        "fn helper() {}\n"  # 9
        "#[cfg(test)] use x;\n"  # 10
    )
    assert cfg_test_lines(text) == [(3, 7), (8, 9), (10, 10)]
    symbol = "rust-analyzer cargo wire_macros 0.3.0 Encode:"
    sites = macro_sites(
        [Document("src/a.rs", (Occurrence(symbol, 1, 10, 0), Occurrence(symbol, 5, 14, 0)))],
        {"wire_macros": MACROS},
        prefix="",
        files={"src/a.rs": text},
    )
    assert [(s.line, s.test_only) for s in sites] == [(1, False), (5, True)]


def test_what_an_expansion_defines() -> None:
    expansions = json.loads((DATA / "wire-expansions.json").read_text(encoding="utf-8"))
    derive = expansions["src/msg.rs:23:39"]["expansion"]
    assert expansion_items("wire::msg", derive) == [
        "wire::msg::Command::encode",
        "wire::msg::Command::encoded_len",
    ]
    attribute = expansions["src/msg.rs:42:3"]["expansion"]
    assert expansion_items("wire::msg", attribute) == ["wire::msg::read_frame"]
    table = expansions["src/crc.rs:5:27"]["expansion"]
    assert expansion_items("wire::crc", table) == []
    assert expansion_items(None, derive) == []  # a test's site defines nothing to map
    builder = (
        "pub struct FooBuilder { x: u8 }\n"
        "impl<T: Clone> Foo<T> where T: Copy {\n"
        '    pub fn builder() -> FooBuilder { let s = "{ fn not_a_method"; FooBuilder { x: 0 } }\n'
        "    fn private() { fn nested() {} }\n"
        "}\n"
        "impl ::core::fmt::Display for Foo {\n"
        '    fn fmt(&self, f: &mut Formatter) -> Result { write!(f, "}}{{") }\n'
        "}\n"
    )
    assert expansion_items("m", builder) == ["m::Foo::builder", "m::Foo::fmt", "m::FooBuilder"]


class _Executor:
    """Runs the expansion helper as rust-analyzer would answer it: the recorded expansions."""

    kind = "native"
    toolchain_env: Mapping[str, str] = {}

    def __init__(self) -> None:
        self.runs = 0

    def to_sandbox_path(self, host_path: Path) -> str:
        return str(host_path)

    def run(self, argv: Sequence[str], **_: Any) -> ExecResult:
        self.runs += 1
        Path(argv[4]).write_bytes((DATA / "wire-expansions.json").read_bytes())
        return ExecResult(
            argv=list(argv),
            exit_code=0,
            stdout="",
            stderr="",
            duration_s=0.1,
            log_path=Path("x.log"),
        )


def _ctx(tmp_path: Path, executor: _Executor) -> AdapterContext:
    work = tmp_path / "wire"
    shutil.copytree(WIRE, work)
    return AdapterContext(
        worktree=work,
        build_dir=tmp_path / "build",
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
        executor=executor,  # type: ignore[arg-type]
        lang=LanguageConfig(language="rust", standard="rust2018", port_target="c"),
    )


def _recorded_scip(adapter: RustAdapter, monkeypatch: pytest.MonkeyPatch) -> None:
    def scip(ctx: AdapterContext, package: str, problems: list[str]) -> list[Document]:
        name = "wire_macros" if package == "macros" else "wire"
        return read_index((DATA / f"{name}.scip").read_bytes())

    monkeypatch.setattr(adapter, "_scip", scip)


def test_the_index_records_each_site_with_its_expansion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = RustAdapter()
    _recorded_scip(adapter, monkeypatch)
    executor = _Executor()
    ctx = _ctx(tmp_path, executor)
    index = adapter.index(ctx)
    assert not index.problems
    with IndexStore.open(index.path) as store:
        targets = {t.name: t.proc_macro for t in store.build_targets()}
        sites = store.macro_sites()
    assert targets == {"wire": False, "wire::frames": False, "wire_macros": True}
    assert len(sites) == 11
    assert all(s.expansion for s in sites)
    by_place = {(s.file, s.line, s.macro): s for s in sites}
    header = by_place[("src/msg.rs", 7, "Encode")]
    assert header.package == "wire_macros"
    assert header.items == ["wire::msg::Header::encode", "wire::msg::Header::encoded_len"]
    assert header.expansion is not None
    assert header.expansion.startswith("impl ::wire::codec::Encode for Header {")
    assert by_place[("src/msg.rs", 42, "throws")].items == ["wire::msg::read_frame"]
    assert by_place[("tests/frames.rs", 6, "Decode")].items == []
    assert executor.runs == 1
    # The agent loop indexes again before each query: the expansions come from the cache
    # until a file they depend on changes.
    adapter.index(ctx)
    assert executor.runs == 1
    msg = ctx.worktree / "src" / "msg.rs"
    msg.write_text(msg.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    adapter.index(ctx)
    assert executor.runs == 2


def test_no_macro_package_no_expansion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A crate without a macro package of its own runs no expansion and records no sites."""
    adapter = RustAdapter()
    _recorded_scip(adapter, monkeypatch)
    executor = _Executor()
    ctx = _ctx(tmp_path, executor)
    shutil.rmtree(ctx.worktree / "macros")
    manifest = ctx.worktree / "Cargo.toml"
    text = manifest.read_text(encoding="utf-8")
    manifest.write_text(text.split("[dependencies]")[0], encoding="utf-8")
    index = adapter.index(ctx)
    with IndexStore.open(index.path) as store:
        assert store.macro_sites() == []
        assert store.value("macro_sites") is None
    assert executor.runs == 0


def test_a_failed_expansion_is_a_problem(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = RustAdapter()
    _recorded_scip(adapter, monkeypatch)
    executor = _Executor()

    def fail(argv: Sequence[str], **_: Any) -> ExecResult:
        return ExecResult(
            argv=list(argv),
            exit_code=1,
            stdout="",
            stderr="boom",
            duration_s=0.1,
            log_path=Path("x.log"),
        )

    monkeypatch.setattr(executor, "run", fail)
    index = adapter.index(_ctx(tmp_path, executor))
    assert any("couldn't expand the macro sites" in p and "boom" in p for p in index.problems)
    with IndexStore.open(index.path) as store:
        sites: list[MacroSite] = store.macro_sites()
    assert sites
    assert all(s.expansion is None and not s.items for s in sites)
