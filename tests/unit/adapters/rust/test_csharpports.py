"""The Rust side of a C# → Rust port (SPEC §11.10, D-84): names, the workspace and runtime, the
scaffolds of each unit kind, and what the retirement leaves."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal

import pytest

from codecoremorph.adapters.base import AdapterContext
from codecoremorph.adapters.csharp import ports as csharp
from codecoremorph.adapters.rust import RustAdapter, csharpports
from codecoremorph.core.errors import ConfigError
from codecoremorph.core.models import LanguageConfig
from codecoremorph.invariants.checks import check_build_commands, patch_lines
from codecoremorph.sandbox.executor import ExecResult

OPTIONS = {
    "crate": "shop",
    "build_target": "src/Shop/Shop.csproj",
    "module": "money",
    "types": "csharp:Shop.Money",
}
PROGRAM = {
    "crate": "shop",
    "module": "shop_cli",
    "build_target": "src/Cli/Shop.Cli.csproj",
    "bin": "Shop.Cli",
}


def _shop(tmp_path: Path) -> Path:
    for project in ("src/Shop/Shop.csproj", "src/Cli/Shop.Cli.csproj", "tests/T/T.csproj"):
        (tmp_path / project).parent.mkdir(parents=True)
        (tmp_path / project).write_text("<Project />\n", encoding="utf-8")
    return tmp_path


def _text(root: Path, rel: str) -> str:
    return (root / rel).read_text(encoding="utf-8")


def test_names() -> None:
    adapter = RustAdapter()
    assert adapter.port_names("src/Shop/Shop.csproj", "Shop.Money", ()) == ("shop", "money")
    # A Rust keyword or a name of the crate's layout is no module name.
    assert adapter.port_names("Shop.csproj", "Zoo.Type", ()) == ("shop", "type_mod")
    assert adapter.port_names("Shop.csproj", "Zoo.Bridge", ()) == ("shop", "bridge_mod")
    assert adapter.port_names("Shop.csproj", "Zoo.Item", ("item",)) == ("shop", "item_2")
    # A C++ module port keeps its names (D-58).
    assert adapter.port_names("calc", "rational", ()) == ("calc_rs", "rational")
    assert csharpports.crate_name("order_book") == "order_book_rs"
    assert csharpports.edition("rust2021") == "2021"
    with pytest.raises(ConfigError):
        csharpports.edition("cpp17")


def test_the_first_unit_writes_the_workspace_the_runtime_and_the_support(tmp_path: Path) -> None:
    root = _shop(tmp_path)
    changed = csharpports.scaffold(root, "rust-csharp-type", OPTIONS, [], "rust2021")
    assert set(changed) == {
        "rust/Cargo.toml",
        "rust/codecoremorph_runtime/Cargo.toml",
        "rust/codecoremorph_runtime/src/lib.rs",
        "rust/codecoremorph_runtime/src/decimal.rs",
        "rust/codecoremorph_runtime/src/exceptions.rs",
        "rust/shop_rs/Cargo.toml",
        "rust/shop_rs/cbindgen.toml",
        "rust/shop_rs/src/lib.rs",
        "rust/shop_rs/src/money.rs",
        "rust/shop_rs/src/bridge/mod.rs",
        "rust/shop_rs/src/bridge/ccm.rs",
        "rust/shop_rs/src/bridge/money.rs",
        "rust/shop_rs/include/shop_rs/codecoremorph_bridge.h",
        "rust/symbol-map.toml",
        "src/Shop/CodeCoreMorph.Interop.cs",
        "src/Shop/CodeCoreMorph.Native.g.cs",
    }
    assert csharpports.is_port(root)
    workspace = tomllib.loads(_text(root, "rust/Cargo.toml"))
    assert workspace["workspace"]["members"] == ["codecoremorph_runtime", "shop_rs"]
    crate = tomllib.loads(_text(root, "rust/shop_rs/Cargo.toml"))
    assert crate["lib"]["crate-type"] == ["cdylib", "rlib"]
    assert crate["package"]["edition"] == "2021"
    assert crate["dependencies"] == {"codecoremorph_runtime": {"path": "../codecoremorph_runtime"}}
    # The bridge is the one module that may hold unsafe code (D-84).
    lib = _text(root, "rust/shop_rs/src/lib.rs")
    assert "#![deny(unsafe_code)]" in lib
    assert "#[allow(unsafe_code)]\nmod bridge;" in lib
    assert "pub mod money;" in lib
    bridge = _text(root, "rust/shop_rs/src/bridge/mod.rs")
    assert "#![allow(non_snake_case, non_camel_case_types)]" in bridge
    assert "pub mod ccm;" in bridge
    assert "mod money;" in bridge
    # cbindgen writes the header in the typedef style the reader takes, leaving the bridge's
    # own C ABI to codecoremorph_bridge.h.
    config = tomllib.loads(_text(root, "rust/shop_rs/cbindgen.toml"))
    assert config["style"] == "type"
    assert config["includes"] == ["codecoremorph_bridge.h"]
    assert set(config["export"]["exclude"]) >= {"ccm_status", "ccm_decimal", "malloc", "free"}
    # The C# side is the one a port to C++ writes, the library the crate's.
    assert _text(root, "src/Shop/CodeCoreMorph.Interop.cs") == csharp.runtime_text(
        csharp.SUPPORT_FILE
    )
    generated = _text(root, "src/Shop/CodeCoreMorph.Native.g.cs")
    assert 'internal const string Library = "shop_rs";' in generated
    assert "rust/shop_rs/include/shop_rs/bridge.h" in generated
    assert csharpports.facade_library(root) == ("rust/shop_rs", "shop_rs")
    assert csharpports.bridge_header("rust/shop_rs", "shop_rs") == (
        "rust/shop_rs/include/shop_rs/bridge.h"
    )
    # Again: nothing to do.
    assert csharpports.scaffold(root, "rust-csharp-type", OPTIONS, [], "rust2021") == []


def test_a_later_unit_adds_its_module_and_keeps_the_rest(tmp_path: Path) -> None:
    root = _shop(tmp_path)
    csharpports.scaffold(root, "rust-csharp-type", OPTIONS, [], "rust2021")
    (root / "rust/shop_rs/src/money.rs").write_text("// ported\n", encoding="utf-8")
    item = {**OPTIONS, "module": "item", "types": "csharp:Shop.Item"}
    changed = csharpports.scaffold(root, "rust-csharp-type", item, [], "rust2021")
    assert set(changed) == {
        "rust/shop_rs/src/item.rs",
        "rust/shop_rs/src/bridge/item.rs",
        "rust/shop_rs/src/lib.rs",
        "rust/shop_rs/src/bridge/mod.rs",
    }
    assert _text(root, "rust/shop_rs/src/money.rs") == "// ported\n"
    assert "pub mod item;" in _text(root, "rust/shop_rs/src/lib.rs")
    assert "mod item;" in _text(root, "rust/shop_rs/src/bridge/mod.rs")


def test_the_runtime_is_shipped_whole() -> None:
    lib = csharpports.runtime_text("codecoremorph_runtime/src/lib.rs")
    assert "#![forbid(unsafe_code)]" in lib
    assert "pub use decimal::Decimal;" in lib
    ccm = csharpports.runtime_text("ccm.rs")
    # Edition-neutral exports (2018 to 2024), each named as codecoremorph_bridge.h declares it.
    for export in ("ccm_error_part", "ccm_free", "ccm_strdup"):
        assert f"#[unsafe(no_mangle)]\npub {'unsafe ' if export != 'ccm_error_part' else ''}" in ccm
        assert export in csharp.runtime_text(csharp.BRIDGE_RUNTIME_HEADER)
    assert "catch_unwind" in ccm
    assert "#[no_mangle]" not in ccm


def test_a_program_becomes_a_function_of_the_library_and_a_cargo_program(tmp_path: Path) -> None:
    root = _shop(tmp_path)
    csharpports.scaffold(root, "rust-csharp-type", OPTIONS, [], "rust2021")
    changed = csharpports.scaffold(root, "rust-csharp-program", PROGRAM, [], "rust2021")
    assert set(changed) == {
        "rust/shop_rs/src/shop_cli_main.rs",
        "rust/shop_rs/src/bridge/shop_cli.rs",
        "rust/shop_rs/src/bin/shop_cli.rs",
        "rust/shop_rs/src/lib.rs",
        "rust/shop_rs/src/bridge/mod.rs",
        # The program's façade calls the export: it gets the support too.
        "src/Cli/CodeCoreMorph.Interop.cs",
        "src/Cli/CodeCoreMorph.Native.g.cs",
    }
    assert "pub fn shop_cli_main(args: &[String]) -> i32 {" in _text(
        root, "rust/shop_rs/src/shop_cli_main.rs"
    )
    export = _text(root, "rust/shop_rs/src/bridge/shop_cli.rs")
    assert 'pub extern "C" fn shop_shop_cli_main(' in export
    assert "crate::shop_cli_main::shop_cli_main(&args)" in export
    assert "std::io::stdout().flush()" in export
    entry = _text(root, "rust/shop_rs/src/bin/shop_cli.rs")
    assert "std::process::exit(shop_rs::shop_cli_main::shop_cli_main(&args));" in entry
    assert "pub mod shop_cli_main;" in _text(root, "rust/shop_rs/src/lib.rs")
    assert csharp.facade_projects(root) == ["src/Cli", "src/Shop"]


def test_a_test_file_becomes_an_integration_test(tmp_path: Path) -> None:
    root = _shop(tmp_path)
    options = {"crate": "shop", "module": "cart_tests", "build_target": "tests/T/T.csproj"}
    with pytest.raises(ConfigError):  # the types come first
        csharpports.scaffold(root, "rust-csharp-tests", options, [], "rust2021")
    csharpports.scaffold(root, "rust-csharp-type", OPTIONS, [], "rust2021")
    changed = csharpports.scaffold(
        root, "rust-csharp-tests", options, ["tests/T/CartTests.cs"], "rust2021"
    )
    assert set(changed) == {"rust/test-map.toml", "rust/shop_rs/tests/cart_tests.rs"}
    stub = _text(root, "rust/shop_rs/tests/cart_tests.rs")
    assert stub.startswith("//! The tests of tests/T/CartTests.cs, translated from C#")
    assert _text(root, "rust/test-map.toml") == csharpports.TEST_MAP_TEXT


def _ported(tmp_path: Path) -> tuple[Path, list[str]]:
    root = _shop(tmp_path)
    csharpports.scaffold(root, "rust-csharp-type", OPTIONS, [], "rust2021")
    csharpports.scaffold(root, "rust-csharp-program", PROGRAM, [], "rust2021")
    options = {"crate": "shop", "module": "cart_tests", "build_target": "tests/T/T.csproj"}
    csharpports.scaffold(root, "rust-csharp-tests", options, ["tests/T/CartTests.cs"], "rust2021")
    files = {
        "src/Shop/Money.cs": "namespace Shop { }\n",
        "src/Shop/CcmExceptions.cs": "namespace CodeCoreMorph.Interop { }\n",
        "src/Cli/Program.cs": "class Program { }\n",
        "tests/T/CartTests.cs": "class CartTests { }\n",
        "Shop.sln": "Microsoft Visual Studio Solution File, Format Version 12.00\n",
        "global.json": "{}\n",
    }
    for rel, text in files.items():
        (root / rel).write_text(text, encoding="utf-8")
    unit = [
        "Shop.sln",
        "global.json",
        "src/Cli/Program.cs",
        "src/Cli/Shop.Cli.csproj",
        "src/Shop/Money.cs",
        "src/Shop/Shop.csproj",
        "tests/T/CartTests.cs",
        "tests/T/T.csproj",
    ]
    return root, unit


def test_the_retirement_leaves_a_cargo_workspace_without_unsafe_code(tmp_path: Path) -> None:
    root, files = _ported(tmp_path)
    csharpports.scaffold(root, "rust-csharp-retire", {"keep": "false"}, files, "rust2021")
    left = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
    assert left == [
        "rust/Cargo.toml",
        "rust/codecoremorph_runtime/Cargo.toml",
        "rust/codecoremorph_runtime/src/decimal.rs",
        "rust/codecoremorph_runtime/src/exceptions.rs",
        "rust/codecoremorph_runtime/src/lib.rs",
        "rust/shop_rs/Cargo.toml",
        "rust/shop_rs/src/bin/shop_cli.rs",
        "rust/shop_rs/src/lib.rs",
        "rust/shop_rs/src/money.rs",
        "rust/shop_rs/src/shop_cli_main.rs",
        "rust/shop_rs/tests/cart_tests.rs",
        "rust/symbol-map.toml",
        "rust/test-map.toml",
    ]
    lib = _text(root, "rust/shop_rs/src/lib.rs")
    assert lib.startswith("//! The C# library `src/Shop/Shop.csproj`, ported to Rust")
    assert "#![forbid(unsafe_code)]" in lib
    assert "mod bridge" not in lib
    assert "pub mod money;" in lib
    crate = tomllib.loads(_text(root, "rust/shop_rs/Cargo.toml"))
    assert "lib" not in crate  # Cargo's default rlib
    assert "metadata" not in crate["package"]
    assert crate["dependencies"]["codecoremorph_runtime"] == {"path": "../codecoremorph_runtime"}


def test_keep_source_api_keeps_the_bridge_for_the_facades(tmp_path: Path) -> None:
    root, _ = _ported(tmp_path)
    files = ["Shop.sln", "src/Cli/Program.cs", "src/Cli/Shop.Cli.csproj"]
    csharpports.scaffold(root, "rust-csharp-retire", {"keep": "true"}, files, "rust2021")
    assert not (root / "src/Cli").exists()
    for kept in (
        "src/Shop/Shop.csproj",
        "src/Shop/CodeCoreMorph.Interop.cs",
        "src/Shop/CodeCoreMorph.Native.g.cs",
        "rust/shop_rs/src/bridge/ccm.rs",
        "rust/shop_rs/cbindgen.toml",
        "global.json",
    ):
        assert (root / kept).is_file(), kept
    crate = tomllib.loads(_text(root, "rust/shop_rs/Cargo.toml"))
    assert crate["lib"]["crate-type"] == ["cdylib", "rlib"]


def _as_added(root: Path) -> str:
    patch: list[str] = []
    for file in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = file.relative_to(root).as_posix()
        lines = file.read_text(encoding="utf-8").splitlines()
        patch.append(
            f"diff --git a/{rel} b/{rel}\nnew file mode 100644\n--- /dev/null\n+++ b/{rel}\n"
            f"@@ -0,0 +1,{len(lines)} @@\n" + "".join(f"+{line}\n" for line in lines)
        )
    return "".join(patch)


def test_the_scaffolds_add_no_build_time_command(tmp_path: Path) -> None:
    """I-12: no build script, build dependency or procedural macro, at any stage."""
    root, files = _ported(tmp_path)
    assert check_build_commands(patch_lines(_as_added(root))).passed
    csharpports.scaffold(root, "rust-csharp-retire", {"keep": "false"}, files, "rust2021")
    assert check_build_commands(patch_lines(_as_added(root))).passed


def test_bad_options_are_refused(tmp_path: Path) -> None:
    root = _shop(tmp_path)
    for options in (
        {**OPTIONS, "crate": "Shop"},
        {**OPTIONS, "module": "../x"},
        {**OPTIONS, "build_target": "src/Shop/Missing.csproj"},
    ):
        with pytest.raises(ConfigError):
            csharpports.scaffold(root, "rust-csharp-type", options, [], "rust2021")
    with pytest.raises(ConfigError):
        csharpports.scaffold(root, "rust-csharp-nothing", OPTIONS, [], "rust2021")


def test_the_rust_adapter_names_the_port_image_and_scaffolds() -> None:
    from codecoremorph.core.models import LanguageConfig

    adapter = RustAdapter()
    spec = adapter.toolchain_requirements(
        LanguageConfig(language="rust", standard="rust2021", port_source="csharp")
    )
    assert spec.image == "codecoremorph-csharp-port"
    assert {"cargo", "cbindgen", "clang", "dotnet"} <= {t.name for t in spec.tools}
    assert set(adapter.capabilities().codemod_tools) >= csharpports.SCAFFOLDS


def test_the_csharp_test_host_loads_the_cdylib() -> None:
    env = csharpports.test_environment("/cache/rust/cargo-target/debug")
    assert env["LD_LIBRARY_PATH"] == "/cache/rust/cargo-target/debug"
    assert csharpports.cargo_target_dir(Path("/cache")).as_posix() == "/cache/rust/cargo-target"


# --------------------------------------------------------------------------- native libraries

NATIVE = {**OPTIONS, "native": "libsodium"}


def test_a_native_librarys_link_name() -> None:
    assert csharpports.native_link_name("libsodium") == "sodium"
    assert csharpports.native_link_name("libsodium.so.23") == "sodium"
    assert csharpports.native_link_name("kernel32.dll") == "kernel32"
    assert csharpports.native_link_name("/usr/lib/libz.so") == "z"
    assert csharpports.native_link_name("Lib.Foo") == "foo"


def test_a_unit_calling_a_native_library_gets_the_native_module(tmp_path: Path) -> None:
    """D-84 (9): the unit's types call libsodium through P/Invoke, so the crate gets its native
    module, bindgen's place for sodium.h's declarations, the library in the manifest, and the
    module in the root, beside the bridge the one that may hold unsafe code."""
    root = _shop(tmp_path)
    crate = "rust/shop_rs"
    changed = csharpports.scaffold(root, "rust-csharp-type", NATIVE, [], "rust2021")
    assert {f"{crate}/src/native/mod.rs", f"{crate}/src/native/sodium_sys.rs"} <= set(changed)
    module = _text(root, f"{crate}/src/native/mod.rs")
    assert module.startswith(csharpports.NATIVE_MOD_DOC)
    # Included, so cbindgen (which expands no macro) leaves libsodium out of the bridge's header.
    assert '\npub mod sodium_sys {\n    include!("sodium_sys.rs");\n}\n' in module
    assert "/// cbindgen:ignore\n" in module
    assert '#[link(name = "sodium")]\nunsafe extern "C" {}\n' in module
    lib = _text(root, f"{crate}/src/lib.rs")
    assert "#[allow(unsafe_code)]\nmod native;\n#[allow(unsafe_code)]\nmod bridge;\n" in lib
    manifest = tomllib.loads(_text(root, f"{crate}/Cargo.toml"))
    metadata = manifest["package"]["metadata"]["codecoremorph"]
    assert metadata == {"csharp-project": "src/Shop/Shop.csproj", "native": ["sodium"]}
    assert csharpports.native_crates(root) == [(crate, ["sodium"], "2021")]
    # A later unit calling it too, and another library: each declared and linked once.
    later = {**NATIVE, "module": "cart", "types": "csharp:Shop.Cart", "native": "libsodium,libz"}
    csharpports.scaffold(root, "rust-csharp-type", later, [], "rust2021")
    module = _text(root, f"{crate}/src/native/mod.rs")
    assert module.count("pub mod sodium_sys {") == 1
    assert '#[link(name = "z")]' in module
    assert _text(root, f"{crate}/src/lib.rs").count("mod native;") == 1
    assert csharpports.native_crates(root) == [(crate, ["sodium", "z"], "2021")]
    # A unit calling none changes nothing of it.
    plain = {**OPTIONS, "module": "till", "types": "csharp:Shop.Till"}
    changed = csharpports.scaffold(root, "rust-csharp-type", plain, [], "rust2021")
    assert not [c for c in changed if "/native/" in c]


def test_the_native_list_goes_under_its_section_header() -> None:
    manifest = (
        '[package]\nname = "shop_rs"\n\n[package.metadata.codecoremorph]\n'
        'csharp-project = "src/Shop/Shop.csproj"\n\n[features]\nx = []\n'
    )
    updated = csharpports.with_native_libraries(manifest, ["sodium"])
    parsed = tomllib.loads(updated)
    assert parsed["package"]["metadata"]["codecoremorph"]["native"] == ["sodium"]
    assert "native" not in parsed["features"]
    assert csharpports.with_native_libraries(updated, ["sodium"]) == updated
    bare = csharpports.with_native_libraries('[package]\nname = "a"\n', ["z"])
    assert tomllib.loads(bare)["package"]["metadata"]["codecoremorph"] == {"native": ["z"]}


def test_bindgen_reads_the_librarys_own_headers_for_the_crates_edition() -> None:
    argv = csharpports.native_bindgen_argv("/c/w.h", "/w/sodium_sys.rs", "sodium", "2024")
    assert argv[:4] == ["bindgen", "/c/w.h", "--output", "/w/sodium_sys.rs"]
    allow = argv[argv.index("--allowlist-file") + 1]
    assert allow == r".*/sodium(\.h|/.*)"
    assert argv[argv.index("--rust-edition") + 1] == "2024"
    assert argv[argv.index("--rust-target") + 1] == "1.85"  # bindgen's default predates 2024
    older = csharpports.native_bindgen_argv("/c/w.h", "/w/sodium_sys.rs", "sodium", "2021")
    assert "--rust-target" not in older
    assert csharpports.native_wrapper("sodium") == "#include <sodium.h>\n"


class _Bindgen:
    """Writes bindgen's output where it is asked to, and remembers each command line."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    @property
    def kind(self) -> Literal["native"]:
        return "native"

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: str,
        env: Mapping[str, str] | None = None,
        timeout_s: float,
        stdin: bytes | None = None,
        log_path: Path | None = None,
    ) -> ExecResult:
        self.commands.append(list(argv))
        if argv[0] == "bindgen":
            out = Path(argv[argv.index("--output") + 1])
            out.write_text('unsafe extern "C" {\n    pub fn sodium_init() -> i32;\n}\n')
        return ExecResult(
            argv=list(argv), exit_code=0, stdout="", stderr="", duration_s=0,
            log_path=log_path or Path("x.log"),
        )  # fmt: skip

    def to_sandbox_path(self, host_path: Path) -> str:
        return host_path.as_posix()


def test_configure_writes_each_native_module_with_bindgen(tmp_path: Path) -> None:
    root = _shop(tmp_path / "work")
    csharpports.scaffold(root, "rust-csharp-type", NATIVE, [], "rust2024")
    executor = _Bindgen()
    ctx = AdapterContext(
        worktree=root,
        build_dir=tmp_path / "build",
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
        executor=executor,  # pyright: ignore[reportArgumentType]
        lang=LanguageConfig(language="rust", standard="rust2024", port_source="csharp"),
    )
    adapter = RustAdapter()
    written, problems = adapter._native_modules(ctx)  # pyright: ignore[reportPrivateUsage]
    assert problems == []
    assert written == ["rust/shop_rs/src/native/sodium_sys.rs"]
    (command,) = executor.commands
    assert command[command.index("--rust-edition") + 1] == "2024"
    assert Path(command[1]).read_text(encoding="utf-8") == "#include <sodium.h>\n"
    assert "sodium_init" in _text(root, "rust/shop_rs/src/native/sodium_sys.rs")
    # The same declarations again change nothing.
    assert adapter._native_modules(ctx)[0] == []  # pyright: ignore[reportPrivateUsage]


def test_the_retirement_keeps_the_native_module_and_denies_unsafe_code_elsewhere(
    tmp_path: Path,
) -> None:
    root, files = _ported(tmp_path)
    csharpports.scaffold(root, "rust-csharp-type", {**NATIVE, "module": "seal"}, [], "rust2021")
    csharpports.scaffold(root, "rust-csharp-retire", {"keep": "false"}, files, "rust2021")
    for kept in ("rust/shop_rs/src/native/mod.rs", "rust/shop_rs/src/native/sodium_sys.rs"):
        assert (root / kept).is_file(), kept
    assert not (root / "rust/shop_rs/src/bridge").exists()
    lib = _text(root, "rust/shop_rs/src/lib.rs")
    assert lib.startswith(
        "//! The C# library `src/Shop/Shop.csproj`, ported to Rust (CodeCoreMorph, D-84).\n"
        "//! `native` calls the native libraries the C# called, the one module that may use\n"
    )
    assert "#![deny(unsafe_code)]" in lib
    assert "forbid" not in lib
    assert "#[allow(unsafe_code)]\nmod native;\n" in lib
    assert "mod bridge" not in lib
    crate = tomllib.loads(_text(root, "rust/shop_rs/Cargo.toml"))
    assert "metadata" not in crate["package"]  # bindgen's last output stays as it is
    assert csharpports.native_crates(root) == []
