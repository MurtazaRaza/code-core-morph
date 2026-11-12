"""Where a port's unsafe code may be (D-58, D-61): the bridge of a crate that has one, and
nowhere else in the port's packages: not their programs, not their tests, not a crate without a
bridge."""

from __future__ import annotations

from pathlib import Path

from codecoremorph.adapters.base import AdapterContext
from codecoremorph.adapters.rust import RustAdapter, ports
from codecoremorph.core.models import LanguageConfig


def _ctx(root: Path) -> AdapterContext:
    return AdapterContext(
        worktree=root,
        build_dir=root / "build",
        cache_dir=root / "cache",
        log_dir=root / "logs",
        executor=None,  # type: ignore[arg-type]  # reading files runs no command
        lang=LanguageConfig(language="rust", standard="rust2021"),
    )


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_unsafe_code_outside_the_bridge(tmp_path: Path) -> None:
    _write(tmp_path, "rust/Cargo.toml", ports.workspace_manifest(["calc_rs", "app_rs"]))
    _write(tmp_path, "rust/calc_rs/Cargo.toml", ports.package_manifest("calc_rs", "2021", "c"))
    _write(tmp_path, "rust/calc_rs/src/lib.rs", ports.lib_rs("calc_rs", "c") + "pub mod m;\n")
    _write(tmp_path, "rust/calc_rs/src/m.rs", "pub fn f() {}\n")
    _write(tmp_path, "rust/calc_rs/src/bridge/mod.rs", "pub unsafe fn g() {}\n")  # allowed
    _write(tmp_path, "rust/calc_rs/tests/t.rs", "#[test]\nfn t() { unsafe { x() } }\n")
    _write(tmp_path, "rust/app_rs/Cargo.toml", '[package]\nname = "app_rs"\n')
    _write(tmp_path, "rust/app_rs/src/main.rs", "fn main() {\n    unsafe { y() }\n}\n")
    _write(tmp_path, "tools/gen/Cargo.toml", '[package]\nname = "gen"\n')
    _write(tmp_path, "tools/gen/src/main.rs", "fn main() { unsafe { z() } }\n")  # not the port's
    adapter = RustAdapter()
    sites = [(s.file, s.line) for s in adapter.unsafe_sites(_ctx(tmp_path))]
    assert sites == [("rust/app_rs/src/main.rs", 2), ("rust/calc_rs/tests/t.rs", 2)]
    uses = [(s.file, s.line) for s in adapter.unsafe_uses(_ctx(tmp_path))]
    assert uses == [
        ("rust/app_rs/src/main.rs", 2),
        ("rust/calc_rs/src/bridge/mod.rs", 1),
        ("rust/calc_rs/tests/t.rs", 2),
    ]


def test_a_csharp_ports_native_module_may_hold_unsafe_code(tmp_path: Path) -> None:
    """A C# port's crate whose Rust calls a native library (D-84 (9)): after the retirement, no
    bridge, and the root's `mod native` the one declaration that may allow unsafe code."""
    lib = (
        "//! The C# library `src/Shop/Shop.csproj`, ported to Rust (CodeCoreMorph, D-84).\n"
        "#![deny(unsafe_code)]\n\n#[allow(unsafe_code)]\nmod native;\npub mod money;\n"
    )
    _write(tmp_path, "rust/Cargo.toml", ports.workspace_manifest(["shop_rs"]))
    _write(tmp_path, "rust/shop_rs/Cargo.toml", '[package]\nname = "shop_rs"\n')
    _write(tmp_path, "rust/shop_rs/src/lib.rs", lib)
    _write(tmp_path, "rust/shop_rs/src/money.rs", "pub fn f() {}\n")
    _write(tmp_path, "rust/shop_rs/src/native/mod.rs", "pub fn g() {\n    unsafe { h() }\n}\n")
    adapter = RustAdapter()
    assert adapter.unsafe_sites(_ctx(tmp_path)) == []
    uses = [(s.file, s.line) for s in adapter.unsafe_uses(_ctx(tmp_path))]
    assert uses == [("rust/shop_rs/src/native/mod.rs", 2)]
    # Only `mod native` and `mod bridge` may allow it.
    _write(tmp_path, "rust/shop_rs/src/lib.rs", lib + "#[allow(unsafe_code)]\nmod other;\n")
    sites = [(s.file, s.line, s.text) for s in adapter.unsafe_sites(_ctx(tmp_path))]
    assert sites == [("rust/shop_rs/src/lib.rs", 7, "allows unsafe code")]
