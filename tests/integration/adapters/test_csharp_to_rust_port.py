"""A C# → Rust port with the real toolchains (D-84), on the Phase 17 spike's ``Shop`` library: the
scaffold of the unit that ports ``Shop.Money``, a hand port (the Rust, its bridge, and the C#
struct a façade over it, the same façade a port to C++ writes), the project's own C# tests
through the façade into the ``cdylib``, the C# declarations CodeCoreMorph writes from cbindgen's
header, the units' checks, and a planted panic that fails a C# test with the exception it
became; then a program, a test file and the retirement, after which Cargo builds and tests the
Rust alone. Marked ``docker``: the run uses the image for ports from C#
(``scripts/build-image csharp-port``)."""

from __future__ import annotations

import shutil
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from codecoremorph.adapters.base import AdapterContext
from codecoremorph.adapters.csharp import CSharpAdapter
from codecoremorph.adapters.rust import RustAdapter, csharpports
from codecoremorph.core.errors import SandboxError
from codecoremorph.core.ids import new_run_id
from codecoremorph.core.models import CodemodSpec, LanguageConfig, Symbol, TestStatus
from codecoremorph.invariants.checks import api_changes, api_symbols
from codecoremorph.sandbox.executor import full_output
from codecoremorph.store.index import IndexStore

pytestmark = pytest.mark.docker

ROOT = Path(__file__).resolve().parents[3]
SHOP = ROOT / "tests" / "unit" / "adapters" / "csharp" / "data" / "shop"
DATA = Path(__file__).with_name("data")
CSHARP = DATA / "csharp_to_cpp"  # the façades are the same for every target (D-84)
PORT = DATA / "csharp_to_rust"
CRATE = "rust/shop_rs"
SCAFFOLD = CodemodSpec(
    tool="rust-csharp-type",
    options={
        "crate": "shop",
        "build_target": "src/Shop/Shop.csproj",
        "module": "money",
        "types": "csharp:Shop.Money",
    },
)
PROGRAM = CodemodSpec(
    tool="rust-csharp-program",
    options={
        "crate": "shop",
        "module": "shop_cli",
        "build_target": "src/Shop.Cli/Shop.Cli.csproj",
        "bin": "Shop.Cli",
    },
)
TESTS = CodemodSpec(
    tool="rust-csharp-tests",
    options={
        "crate": "shop",
        "module": "money_tests",
        "build_target": "tests/Shop.Tests/Shop.Tests.csproj",
    },
)
RETIRE = CodemodSpec(tool="rust-csharp-retire", options={"keep": "false"})


@dataclass
class Contexts:
    csharp: AdapterContext
    rust: AdapterContext


@pytest.fixture
def ctx(tmp_path: Path) -> Iterator[Contexts]:
    from codecoremorph.sandbox.docker.executor import DockerExecutor, DockerSettings
    from codecoremorph.sandbox.provisioning import resolve_image

    work = tmp_path / "shop"
    shutil.copytree(SHOP, work)
    # NuGet's deepest files pass Windows' 260-character limit under pytest's temporary
    # directory: keep the cache's path short.
    short = Path(tempfile.mkdtemp(prefix="ccm"))
    for path in (tmp_path / "build", short / "cache", tmp_path / "logs"):
        path.mkdir()
    csharp_lang = LanguageConfig(
        language="csharp",
        standard="csharp7.3",
        port_target="rust",
        build_system="dotnet",
        test_dirs=["tests"],
        build_timeout_s=900,
        test_timeout_s=600,
    )
    rust_lang = LanguageConfig(
        language="rust",
        standard="rust2021",
        target_standard="rust2021",
        port_source="csharp",
        build_system="cargo",
        build_timeout_s=900,
        test_timeout_s=300,
    )
    spec = RustAdapter().toolchain_requirements(rust_lang)
    assert spec.image == "codecoremorph-csharp-port"  # the Rust of a C# port needs .NET too
    try:
        image = resolve_image(spec, None)
    except SandboxError as exc:
        pytest.skip(f"no image for ports from C#: {exc}")
    executor = DockerExecutor(
        DockerSettings(
            image=image,
            run_id=new_run_id(),
            worktree=work,
            build_dir=tmp_path / "build",
            cache_dir=short / "cache",
            log_dir=tmp_path / "exec",
            toolchain_env="/opt/codecoremorph/toolchains/csharp-port.env",
        )
    )
    executor.start()
    try:
        csharp = AdapterContext(
            worktree=work,
            build_dir=tmp_path / "build",
            cache_dir=short / "cache",
            log_dir=tmp_path / "logs",
            executor=executor,
            lang=csharp_lang,
        )
        rust = replace(
            csharp,
            cache_dir=short / "cache" / "rust",
            log_dir=tmp_path / "logs" / "rust",
            lang=rust_lang,
        )
        yield Contexts(csharp, rust)
    finally:
        executor.stop()
        shutil.rmtree(
            f"\\\\?\\{short.resolve()}" if sys.platform == "win32" else short, ignore_errors=True
        )


def _verify(csharp: CSharpAdapter, rust: RustAdapter, ctx: Contexts) -> dict[str, TestStatus]:
    """One verification as a port run makes it: the target configures (cbindgen's header, the
    C# declarations), then the source configures, builds (the cdylib first) and tests."""
    configured = rust.configure(ctx.rust)
    assert configured.success, configured.diagnostics
    configured = csharp.configure(ctx.csharp)
    assert configured.success, configured.diagnostics
    built = csharp.build(ctx.csharp)
    assert built.success, built.diagnostics
    return {t.test_id: t.status for t in csharp.run_tests(ctx.csharp).cases}


def _api(csharp: CSharpAdapter, ctx: Contexts) -> list[Symbol]:
    with IndexStore.open(csharp.index(ctx.csharp).path) as store:
        return [s for s in api_symbols(store.symbols()) if s.file == "src/Shop/Money.cs"]


def _port_money(work: Path) -> None:
    shutil.copyfile(PORT / "money.rs", work / CRATE / "src/money.rs")
    shutil.copyfile(PORT / "bridge_money.rs", work / CRATE / "src/bridge/money.rs")
    shutil.copyfile(CSHARP / "Money.cs", work / "src/Shop/Money.cs")


def test_a_csharp_type_ported_to_rust_by_hand(ctx: Contexts) -> None:
    csharp, rust = CSharpAdapter(), RustAdapter()
    work = ctx.csharp.worktree
    baseline = _verify(csharp, rust, ctx)
    assert len(baseline) == 22
    assert set(baseline.values()) == {TestStatus.PASSED}
    api = _api(csharp, ctx)

    # The scaffold of the unit that ports Shop.Money: the workspace and its lockfile, the
    # runtime, the crate, its bridge, and the C# support beside the project.
    files = ["src/Shop/Money.cs"]
    scaffold = rust.run_codemods(ctx.rust, [SCAFFOLD], files)
    assert not scaffold.diagnostics, scaffold.diagnostics
    assert {
        "rust/Cargo.toml",
        "rust/Cargo.lock",
        "rust/codecoremorph_runtime/src/decimal.rs",
        f"{CRATE}/src/money.rs",
        f"{CRATE}/src/bridge/money.rs",
        f"{CRATE}/src/bridge/ccm.rs",
        "src/Shop/CodeCoreMorph.Interop.cs",
        "src/Shop/CodeCoreMorph.Native.g.cs",
    } <= set(scaffold.changed_files)
    lock = (work / "rust/Cargo.lock").read_text(encoding="utf-8")
    assert 'name = "codecoremorph_runtime"' in lock
    assert 'name = "shop_rs"' in lock
    assert csharpports.is_port(work)
    # The unit's first verification: the empty bridge builds, and nothing changed yet.
    assert _verify(csharp, rust, ctx) == baseline
    assert (work / CRATE / "include/shop_rs/bridge.h").is_file()

    # The port: the Rust, its bridge, and Money a façade over it.
    _port_money(work)
    assert _verify(csharp, rust, ctx) == baseline  # every test, through the façade
    header = (work / CRATE / "include/shop_rs/bridge.h").read_text(encoding="utf-8")
    assert '#include "codecoremorph_bridge.h"' in header
    assert "ccm_status shop_Money_add(ccm_decimal amount," in header
    assert "ccm_free" not in header  # the runtime header's own
    declarations = (work / "src/Shop/CodeCoreMorph.Native.g.cs").read_text(encoding="utf-8")
    assert 'internal const string Library = "shop_rs";' in declarations
    assert "internal static int shop_Money_add(CcmDecimal amount, string currency," in declarations
    assert api_changes(api, _api(csharp, ctx), files=files) == []
    # The units' checks: clippy is quiet, and unsafe code is the bridge's alone.
    assert rust.lint(ctx.rust) == []
    assert rust.unsafe_sites(ctx.rust) == []
    money = work / CRATE / "src/money.rs"
    good = money.read_text(encoding="utf-8")
    money.write_text("#![allow(unsafe_code)]\n" + good, encoding="utf-8")
    assert [s.file for s in rust.unsafe_sites(ctx.rust)] == [f"{CRATE}/src/money.rs"]
    money.write_text(good, encoding="utf-8")

    # A panic planted in the Rust fails the C# test that reaches it, with the exception it
    # became, and the test host lives on.
    planted = good.replace(
        "    pub fn times(&self, quantity: i32) -> Result<Money> {\n",
        "    pub fn times(&self, quantity: i32) -> Result<Money> {\n"
        "        let probe: Vec<i32> = Vec::new();\n"
        "        let _ = probe[quantity as usize];\n",
    )
    assert planted != good
    money.write_text(planted, encoding="utf-8")
    assert rust.configure(ctx.rust).success
    assert csharp.configure(ctx.csharp).success
    built = csharp.build(ctx.csharp)
    assert built.success, built.diagnostics
    report = {t.test_id: t for t in csharp.run_tests(ctx.csharp).cases}
    assert "dotnet:(run)" not in report  # the test host lived on
    times = report["dotnet:Shop.Tests.MoneyTests#TimesMultipliesTheAmount"]
    assert times.status is TestStatus.FAILED
    assert "IndexOutOfRangeException" in (times.message or "")
    failed = sorted(i for i, t in report.items() if t.status is not TestStatus.PASSED)
    assert all("Times" in i or "Cart" in i for i in failed), failed
    money.write_text(good, encoding="utf-8")


def _run_program(ctx: Contexts, *args: str) -> tuple[int, str]:
    """The Cargo program shop_cli, as a user runs it."""
    rust = ctx.rust
    program = csharpports.cargo_target_dir(ctx.csharp.cache_dir) / "debug" / "shop_cli"
    result = rust.executor.run(
        [rust.sandbox(program), *args],
        cwd=rust.sandbox(rust.worktree),
        timeout_s=120,
        log_path=rust.log_dir / "shop-cli.log",
    )
    return result.exit_code, full_output(result)[0]


def test_a_program_and_a_test_file_then_the_retirement(ctx: Contexts) -> None:
    """The units that finish a port: a program (its code in the library, the C# Main a façade,
    and a Cargo program), a test file translated to a Rust integration test beside the C#
    tests, and the retirement, after which Cargo builds and tests the Rust alone, with no
    unsafe code left."""
    csharp, rust = CSharpAdapter(), RustAdapter()
    work = ctx.csharp.worktree
    (work / "src/Shop.Cli").mkdir(parents=True)
    shutil.copyfile(CSHARP / "Shop.Cli.csproj", work / "src/Shop.Cli/Shop.Cli.csproj")
    shutil.copyfile(CSHARP / "Program.cs", work / "src/Shop.Cli/Program.cs")
    rust.run_codemods(ctx.rust, [SCAFFOLD], ["src/Shop/Money.cs"])
    _port_money(work)
    baseline = _verify(csharp, rust, ctx)
    assert len(baseline) == 22
    assert set(baseline.values()) == {TestStatus.PASSED}

    # The program: its scaffold, the hand port, and Main a façade over the export (the C++
    # port's façade, unchanged).
    changed = rust.run_codemods(ctx.rust, [PROGRAM], ["src/Shop.Cli/Program.cs"]).changed_files
    assert {f"{CRATE}/src/bin/shop_cli.rs", f"{CRATE}/src/bridge/shop_cli.rs",
            "src/Shop.Cli/CodeCoreMorph.Interop.cs"} <= set(changed)  # fmt: skip
    shutil.copyfile(PORT / "shop_cli_main.rs", work / CRATE / "src/shop_cli_main.rs")
    shutil.copyfile(CSHARP / "ProgramFacade.cs", work / "src/Shop.Cli/Program.cs")
    assert _verify(csharp, rust, ctx) == baseline
    declarations = (work / "src/Shop.Cli/CodeCoreMorph.Native.g.cs").read_text(encoding="utf-8")
    assert (
        "internal static int shop_shop_cli_main(string[] args_data, int args_count, out int result)"
    ) in declarations
    built = rust.build(ctx.rust)
    assert built.success, built.diagnostics
    assert _run_program(ctx, "a", "b") == (2, "5.00 EUR\n")

    # A test file: the crate's integration test, which nextest runs, beside the C# tests.
    changed = rust.run_codemods(ctx.rust, [TESTS], ["tests/Shop.Tests/MoneyTests.cs"])
    assert set(changed.changed_files) == {"rust/test-map.toml", f"{CRATE}/tests/money_tests.rs"}
    shutil.copyfile(PORT / "money_tests.rs", work / CRATE / "tests/money_tests.rs")
    assert _verify(csharp, rust, ctx) == baseline
    built = rust.build(ctx.rust)
    assert built.success, built.diagnostics
    expected = {
        f"nextest:shop_rs::money_tests::{name}"
        for name in (
            "adding_keeps_the_scale",
            "times_multiplies_the_amount",
            "currencies_do_not_mix",
            "percent_is_unrounded_10",
            "percent_is_unrounded_15",
            "percent_is_unrounded_33",
        )
    }
    tests = {t.test_id: t.status for t in rust.run_tests(ctx.rust).cases}
    assert {t: tests.get(t) for t in expected} == dict.fromkeys(expected, TestStatus.PASSED)
    assert rust.lint(ctx.rust) == []
    # What a symbol map names is what the Rust index has (symbol_map).
    with IndexStore.open(rust.index(ctx.rust).path) as store:
        known = {s.qualified_name for s in store.symbols()}
    assert {
        "shop_rs::money::Money::add",
        "shop_rs::money::Money::new",
        "shop_rs::money::Money::fmt",
        "shop_rs::shop_cli_main::shop_cli_main",
    } <= known

    # The retirement: the C# and the bridge go, and Cargo builds and tests the Rust alone.
    csharp_files = sorted(
        p.relative_to(work).as_posix()
        for p in work.rglob("*")
        if p.suffix in (".cs", ".csproj") and not {"bin", "obj"} & set(p.parts)
    )
    retired = rust.run_codemods(ctx.rust, [RETIRE], csharp_files)
    assert not retired.diagnostics, retired.diagnostics
    assert not csharp.builds(ctx.csharp)
    assert not (work / "src").exists()
    assert not (work / CRATE / "src/bridge").exists()
    assert "#![forbid(unsafe_code)]" in (work / CRATE / "src/lib.rs").read_text(encoding="utf-8")
    configured = rust.configure(ctx.rust)
    assert configured.success, configured.diagnostics
    built = rust.build(ctx.rust)
    assert built.success, built.diagnostics
    after = {t.test_id: t.status for t in rust.run_tests(ctx.rust).cases}
    assert {t: after.get(t) for t in expected} == dict.fromkeys(expected, TestStatus.PASSED)
    assert rust.lint(ctx.rust) == []
    assert rust.unsafe_sites(ctx.rust) == []
    assert _run_program(ctx, "a", "b", "c") == (3, "7.50 EUR\n")
