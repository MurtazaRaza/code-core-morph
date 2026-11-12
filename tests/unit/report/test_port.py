"""A port's summary in the report (D-60): the maps' counts and notes, unsafe code, the source
code left with the reason, and how report.md shows it."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from codecoremorph.adapters.base import AdapterContext
from codecoremorph.adapters.rust import RustAdapter
from codecoremorph.core.models import (
    LanguageConfig,
    PortSummary,
    RunReport,
    RunStatus,
    UnitStatus,
    WorkUnit,
)
from codecoremorph.report.port import port_summary
from codecoremorph.report.render import render_markdown

SYMBOLS = """\
[[symbol]]
cpp = "calc::Rational::parse"
rust = "calc_rs::rational::Rational::parse"
note = "Saturates like strtol."

[[symbol]]
cpp = "calc::trim"
rust = "calc_rs::strutil::trim"
"""
TESTS = """\
[[test]]
cpp = "gtest:A.One"
rust = "nextest:calc_rs::a::one"
note = "Through the Rust API."

[[test]]
cpp = "ctest:calc_tests"
note = "only runs a GoogleTest binary"
"""


def test_the_summary_of_a_ports_tree(tmp_path: Path) -> None:
    (tmp_path / "rust" / "calc_rs" / "src").mkdir(parents=True)
    (tmp_path / "rust" / "calc_rs" / "Cargo.toml").write_text(
        '[package]\nname = "calc_rs"\n', encoding="utf-8"
    )
    (tmp_path / "rust" / "calc_rs" / "src" / "lib.rs").write_text(
        "#![forbid(unsafe_code)]\nfn f() {\n    unsafe { g() }\n}\n", encoding="utf-8"
    )
    (tmp_path / "rust" / "symbol-map.toml").write_text(SYMBOLS, encoding="utf-8")
    (tmp_path / "rust" / "test-map.toml").write_text(TESTS, encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "left.cpp").write_text("int x;\n", encoding="utf-8")
    (tmp_path / "src" / "stuck.cpp").write_text("int y;\n", encoding="utf-8")
    ctx = AdapterContext(
        worktree=tmp_path,
        build_dir=tmp_path / "build",
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
        executor=None,  # type: ignore[arg-type]  # reading files runs no command
        lang=LanguageConfig(language="rust", standard="rust2021"),
    )
    units = [
        WorkUnit(id="U-0001", seq=1, rules=["port_module"], files=["src/stuck.cpp"],
                 status=UnitStatus.FAILED),
        WorkUnit(id="U-0002", seq=2, rules=["port_module"], files=["src/gone.cpp"],
                 status=UnitStatus.COMMITTED),
    ]  # fmt: skip
    summary = port_summary(
        worktree=tmp_path,
        target=RustAdapter(),
        target_ctx=ctx,
        source_language="cpp",
        target_language="rust",
        source_files=["src/left.cpp", "src/stuck.cpp", "src/gone.cpp"],
        units=units,
        keep_source_api=False,
        # snappy's port (M8, D-62): the vendored googletest and the unbuilt benchmark stay.
        excluded={"third_party/**": 1412, "snappy_benchmark.cc": 1, "old/**": 0},
    )
    assert summary == PortSummary(
        symbols=2,
        tests_mapped=1,
        tests_without_counterpart=["ctest:calc_tests: only runs a GoogleTest binary"],
        deviations=[
            "calc::Rational::parse: Saturates like strtol.",
            "gtest:A.One: Through the Rust API.",
        ],
        unsafe=["rust/calc_rs/src/lib.rs:3"],
        source_left=[
            "src/left.cpp (no unit of the port removes it)",
            "src/stuck.cpp (U-0001 is failed)",
            "snappy_benchmark.cc (1 file, which project.exclude keeps out of the port)",
            "third_party/** (1412 files, which project.exclude keeps out of the port)",
        ],
    )
    now = datetime(2026, 11, 5, tzinfo=UTC)
    report = RunReport(
        run_id="20261105-000000-abcd",
        project="calc",
        status=RunStatus.COMPLETED,
        language="cpp",
        source_standard="cpp98",
        target_standard="rust2021",
        target_language="rust",
        base_commit="0" * 40,
        branch="codecoremorph/20261105-000000-abcd",
        started_at=now,
        port=summary,
    )
    text = render_markdown(report)
    assert "## Port" in text
    assert (
        "The symbol map has 2 entries; the test map pairs 1 tests. Unsafe code: 1 "
        "(`rust/calc_rs/src/lib.rs:3`)." in text
    )
    assert "- src/stuck.cpp (U-0001 is failed)" in text
    assert "- ctest:calc_tests: only runs a GoogleTest binary" in text


def test_a_csharp_ports_shared_cells_are_counted_by_file(tmp_path: Path) -> None:
    """What a port from C# kept shared and mutable (D-84): each Mutex, RwLock, RefCell and Cell
    of the library, by its unit's file; the bridge, tests and CodeCoreMorph's runtime aside."""
    files = {
        "rust/shop_rs/Cargo.toml": '[package]\nname = "shop_rs"\n',
        "rust/shop_rs/src/lib.rs": "pub mod cart;\nmod bridge;\n",
        "rust/shop_rs/src/cart.rs": (
            "use std::sync::{Arc, Mutex};\n"
            "pub struct Cart {\n    state: Mutex<State>,\n    subs: Mutex<Vec<u8>>,\n}\n"
            "// a Mutex<in a comment> isn't one\n"
            "struct Seen(std::cell::RefCell<u8>);\n"
        ),
        "rust/shop_rs/src/bridge/cart.rs": "static S: std::sync::Mutex<u8> = todo!();\n",
        "rust/shop_rs/tests/cart_tests.rs": "let m = std::sync::Mutex::new(0);\n",
        "rust/codecoremorph_runtime/Cargo.toml": '[package]\nname = "codecoremorph_runtime"\n',
        "rust/codecoremorph_runtime/src/lib.rs": "pub struct X(std::cell::Cell<u8>);\n",
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    ctx = AdapterContext(
        worktree=tmp_path,
        build_dir=tmp_path / "build",
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
        executor=None,  # type: ignore[arg-type]  # reading files runs no command
        lang=LanguageConfig(language="rust", standard="rust2021"),
    )
    summary = port_summary(
        worktree=tmp_path,
        target=RustAdapter(),
        target_ctx=ctx,
        source_language="csharp",
        target_language="rust",
        source_files=[],
        units=[],
        keep_source_api=False,
    )
    assert summary.shared_cells == [
        "rust/shop_rs/src/cart.rs:3: Mutex",
        "rust/shop_rs/src/cart.rs:4: Mutex",
        "rust/shop_rs/src/cart.rs:7: RefCell",
    ]
    now = datetime(2026, 11, 11, tzinfo=UTC)
    report = RunReport(
        run_id="20261111-000000-abcd",
        project="shop",
        status=RunStatus.COMPLETED,
        language="csharp",
        source_standard="csharp7.3",
        target_standard="rust2021",
        target_language="rust",
        base_commit="0" * 40,
        branch="codecoremorph/20261111-000000-abcd",
        started_at=now,
        port=summary,
    )
    text = render_markdown(report)
    assert "Shared mutable cells: 3." in text
    assert "- rust/shop_rs/src/cart.rs: 2 Mutex, 1 RefCell" in text
