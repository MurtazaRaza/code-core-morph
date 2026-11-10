"""A C# → C++ port with the real toolchains (D-78), on the Phase 17 spike's ``Shop`` library: the
scaffold of the unit that ports ``Shop.Money``, a hand port (the C++, its bridge, and the C#
struct a façade over it), the project's own C# tests through the façade with the C++ under
AddressSanitizer and UndefinedBehaviorSanitizer inside the .NET test host, the C# declarations
CodeCoreMorph writes from the bridge header, and a planted overflow that ends the test run with
the sanitizer's report. Marked ``docker``: the run uses the image for ports from C#
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
from codecoremorph.adapters.cpp import CppAdapter, csharpports
from codecoremorph.adapters.csharp import CSharpAdapter
from codecoremorph.core.errors import SandboxError
from codecoremorph.core.ids import new_run_id
from codecoremorph.core.models import CodemodSpec, LanguageConfig, Symbol, TestStatus
from codecoremorph.invariants.checks import api_changes, api_symbols
from codecoremorph.sandbox.executor import full_output
from codecoremorph.store.index import IndexStore

pytestmark = pytest.mark.docker

ROOT = Path(__file__).resolve().parents[3]
SHOP = ROOT / "tests" / "unit" / "adapters" / "csharp" / "data" / "shop"
PORT = Path(__file__).with_name("data") / "csharp_to_cpp"
SCAFFOLD = CodemodSpec(
    tool="cpp-csharp-type",
    options={
        "crate": "shop",
        "build_target": "src/Shop/Shop.csproj",
        "module": "money",
        "types": "csharp:Shop.Money",
    },
)


@dataclass
class Contexts:
    csharp: AdapterContext
    cpp: AdapterContext


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
        port_target="cpp",
        build_system="dotnet",
        test_dirs=["tests"],
        build_timeout_s=900,
        test_timeout_s=600,
    )
    cpp_lang = LanguageConfig(
        language="cpp",
        standard="cpp17",
        target_standard="cpp17",
        port_source="csharp",
        build_system="cmake",
        build_timeout_s=900,
        test_timeout_s=300,
    )
    spec = CppAdapter().toolchain_requirements(cpp_lang)
    assert spec.image == "codecoremorph-csharp-port"  # the C++ of a C# port needs .NET too
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
        cpp = replace(
            csharp,
            cache_dir=short / "cache" / "cpp",
            log_dir=tmp_path / "logs" / "cpp",
            lang=cpp_lang,
        )
        yield Contexts(csharp, cpp)
    finally:
        executor.stop()
        shutil.rmtree(
            f"\\\\?\\{short.resolve()}" if sys.platform == "win32" else short, ignore_errors=True
        )


def _verify(csharp: CSharpAdapter, cpp: CppAdapter, ctx: Contexts) -> dict[str, TestStatus]:
    """One verification as a port run makes it: the target configures, then the source
    configures, builds (the bridge library first) and tests (agent/loop.py)."""
    configured = cpp.configure(ctx.cpp)
    assert configured.success, configured.diagnostics
    configured = csharp.configure(ctx.csharp)
    assert configured.success, configured.diagnostics
    built = csharp.build(ctx.csharp)
    assert built.success, built.diagnostics
    return {t.test_id: t.status for t in csharp.run_tests(ctx.csharp).cases}


def _api(csharp: CSharpAdapter, ctx: Contexts) -> list[Symbol]:
    """The library's API symbols in a port's index: its public and protected members (D-78)."""
    with IndexStore.open(csharp.index(ctx.csharp).path) as store:
        return [s for s in api_symbols(store.symbols()) if s.file == "src/Shop/Money.cs"]


def test_a_csharp_type_ported_to_cpp_by_hand(ctx: Contexts) -> None:
    csharp, cpp = CSharpAdapter(), CppAdapter()
    work = ctx.csharp.worktree
    baseline = _verify(csharp, cpp, ctx)
    assert len(baseline) == 22
    assert set(baseline.values()) == {TestStatus.PASSED}
    api = _api(csharp, ctx)  # Money's API, which its façade keeps (I-6)
    assert "public Shop.Money Times(int quantity)" in {s.signature for s in api}

    # The scaffold of the unit that ports Shop.Money: the CMake build, the runtime, the C#
    # support beside the project, and the unit's C++ and bridge files.
    files = ["src/Shop/Money.cs"]
    scaffold = cpp.run_codemods(ctx.cpp, [SCAFFOLD], files)
    assert set(scaffold.changed_files) == {
        "CMakeLists.txt",
        "cpp/CMakeLists.txt",
        "cpp/include/codecoremorph_decimal.hpp",
        "cpp/include/codecoremorph_exceptions.hpp",
        "cpp/bridge/codecoremorph_bridge.h",
        "cpp/bridge/codecoremorph_bridge.hpp",
        "cpp/bridge/codecoremorph_bridge.cpp",
        "cpp/bridge/money.h",
        "cpp/bridge/money.cpp",
        "cpp/src/money.hpp",
        "cpp/src/money.cpp",
        "cpp/symbol-map.toml",
        "src/Shop/CodeCoreMorph.Interop.cs",
        "src/Shop/CodeCoreMorph.Native.g.cs",
    }
    assert csharpports.is_port(work)
    assert not cpp.run_codemods(ctx.cpp, [SCAFFOLD], files).changed_files
    # The unit's first verification: the empty bridge links, and nothing changed yet.
    assert _verify(csharp, cpp, ctx) == baseline

    # The port: the C++, its bridge, and Money a façade over it.
    shutil.copyfile(PORT / "money.hpp", work / "cpp/src/money.hpp")
    shutil.copyfile(PORT / "money.cpp", work / "cpp/src/money.cpp")
    shutil.copyfile(PORT / "bridge_money.h", work / "cpp/bridge/money.h")
    shutil.copyfile(PORT / "bridge_money.cpp", work / "cpp/bridge/money.cpp")
    shutil.copyfile(PORT / "Money.cs", work / "src/Shop/Money.cs")
    assert _verify(csharp, cpp, ctx) == baseline  # every test, through the façade
    declarations = (work / "src/Shop/CodeCoreMorph.Native.g.cs").read_text(encoding="utf-8")
    assert 'internal const string Library = "shop_native";' in declarations
    assert "private static extern int shop_Money_add_raw(" in declarations  # strings as UTF-8
    assert "internal static int shop_Money_add(CcmDecimal amount, string currency," in declarations
    # I-6 compares the façade with the C# by signature: nothing changed, the struct's own
    # helpers added; a parameter's type changed is a change.
    assert api_changes(api, _api(csharp, ctx), files=files) == []
    facade = (work / "src/Shop/Money.cs").read_text(encoding="utf-8")
    widened = facade.replace("Times(int quantity)", "Times(long quantity)")
    assert widened != facade
    (work / "src/Shop/Money.cs").write_text(widened, encoding="utf-8")
    (change,) = api_changes(api, _api(csharp, ctx), files=files)
    assert (change.qualified_name, change.after) == (
        "Shop.Money.Times",
        ("public Shop.Money Times(long quantity)",),
    )
    (work / "src/Shop/Money.cs").write_text(facade, encoding="utf-8")

    # A heap overflow planted in the C++ ends the test run with AddressSanitizer's report.
    good = (work / "cpp/src/money.cpp").read_text(encoding="utf-8")
    planted = good.replace(
        "Money Money::times(int quantity) const {",
        "Money Money::times(int quantity) const {\n"
        "    std::vector<int> probe(2, 0);\n"
        "    volatile int read = probe.data()[2];\n"
        "    (void)read;",
    ).replace("#include <utility>", "#include <utility>\n#include <vector>")
    assert planted != good
    (work / "cpp/src/money.cpp").write_text(planted, encoding="utf-8")
    cpp.configure(ctx.cpp)
    csharp.configure(ctx.csharp)
    assert csharp.build(ctx.csharp).success
    report = {t.test_id: t for t in csharp.run_tests(ctx.csharp).cases}
    run = report.get("dotnet:(run)")
    assert run is not None
    assert run.status is TestStatus.ERROR
    assert "AddressSanitizer" in (run.message or "")
    times = report.get("dotnet:Shop.Tests.MoneyTests#TimesMultipliesTheAmount")
    assert times is None or times.status is not TestStatus.PASSED
    (work / "cpp/src/money.cpp").write_text(good, encoding="utf-8")


PROGRAM = CodemodSpec(
    tool="cpp-csharp-program",
    options={
        "crate": "shop",
        "module": "shop_cli",
        "build_target": "src/Shop.Cli/Shop.Cli.csproj",
        "bin": "Shop.Cli",
    },
)
TESTS = CodemodSpec(
    tool="cpp-csharp-tests",
    options={
        "crate": "shop",
        "module": "money_tests",
        "build_target": "tests/Shop.Tests/Shop.Tests.csproj",
    },
)
RETIRE = CodemodSpec(tool="cpp-csharp-retire", options={"keep": "false"})


def _run_program(ctx: Contexts, *args: str) -> tuple[int, str]:
    """The CMake program Shop.Cli, as a user runs it."""
    cpp = ctx.cpp
    result = cpp.executor.run(
        [f"{cpp.sandbox(cpp.build_dir)}/cpp/Shop.Cli", *args],
        cwd=cpp.sandbox(cpp.worktree),
        timeout_s=120,
        log_path=cpp.log_dir / "shop-cli.log",
    )
    return result.exit_code, full_output(result)[0]


def test_a_program_and_a_test_file_then_the_retirement(ctx: Contexts) -> None:
    """The units that finish a port: a program (its code in the library, the C# Main a façade,
    and a CMake program), a test file translated to GoogleTest beside the C# tests, and the
    retirement, after which CMake builds and tests the C++ alone."""
    csharp, cpp = CSharpAdapter(), CppAdapter()
    work = ctx.csharp.worktree
    (work / "src/Shop.Cli").mkdir(parents=True)
    shutil.copyfile(PORT / "Shop.Cli.csproj", work / "src/Shop.Cli/Shop.Cli.csproj")
    shutil.copyfile(PORT / "Program.cs", work / "src/Shop.Cli/Program.cs")
    # Shop.Money ported, as the type unit leaves it.
    cpp.run_codemods(ctx.cpp, [SCAFFOLD], ["src/Shop/Money.cs"])
    for name, rel in (("money.hpp", "cpp/src/money.hpp"), ("money.cpp", "cpp/src/money.cpp"),
                      ("bridge_money.h", "cpp/bridge/money.h"),
                      ("bridge_money.cpp", "cpp/bridge/money.cpp"),
                      ("Money.cs", "src/Shop/Money.cs")):  # fmt: skip
        shutil.copyfile(PORT / name, work / rel)
    baseline = _verify(csharp, cpp, ctx)
    assert len(baseline) == 22
    assert set(baseline.values()) == {TestStatus.PASSED}

    # The program: its scaffold, the hand port, and Main a façade over the export.
    changed = cpp.run_codemods(ctx.cpp, [PROGRAM], ["src/Shop.Cli/Program.cs"]).changed_files
    assert {"cpp/programs/shop_cli.cpp", "cpp/bridge/shop_cli.cpp",
            "src/Shop.Cli/CodeCoreMorph.Interop.cs"} <= set(changed)  # fmt: skip
    shutil.copyfile(PORT / "shop_cli_main.cpp", work / "cpp/src/shop_cli_main.cpp")
    shutil.copyfile(PORT / "ProgramFacade.cs", work / "src/Shop.Cli/Program.cs")
    assert _verify(csharp, cpp, ctx) == baseline  # the façade builds against its declaration
    declarations = (work / "src/Shop.Cli/CodeCoreMorph.Native.g.cs").read_text(encoding="utf-8")
    assert (
        "internal static int shop_shop_cli_main(string[] args_data, int args_count, out int result)"
    ) in declarations
    built = cpp.build(ctx.cpp)
    assert built.success, built.diagnostics
    assert _run_program(ctx, "a", "b") == (2, "5.00 EUR\n")

    # A test file: its GoogleTest program, which CTest runs, beside the C# tests.
    changed = cpp.run_codemods(ctx.cpp, [TESTS], ["tests/Shop.Tests/MoneyTests.cs"]).changed_files
    assert set(changed) == {"cpp/test-map.toml", "cpp/tests/CMakeLists.txt",
                            "cpp/tests/money_tests.cpp"}  # fmt: skip
    shutil.copyfile(PORT / "money_tests.cpp", work / "cpp/tests/money_tests.cpp")
    assert _verify(csharp, cpp, ctx) == baseline
    built = cpp.build(ctx.cpp)
    assert built.success, built.diagnostics
    gtests = {t.test_id: t.status for t in cpp.run_tests(ctx.cpp).cases}
    expected = {
        "gtest:MoneyTests.AddingKeepsTheScale",
        "gtest:MoneyTests.TimesMultipliesTheAmount",
        "gtest:MoneyTests.CurrenciesDoNotMix",
        "gtest:Cases/MoneyTests_PercentIsUnrounded.Run/0",
        "gtest:Cases/MoneyTests_PercentIsUnrounded.Run/1",
        "gtest:Cases/MoneyTests_PercentIsUnrounded.Run/2",
    }
    assert {t: gtests.get(t) for t in expected} == dict.fromkeys(expected, TestStatus.PASSED)

    # The units' checks (lint_clean): the hand port is clean, the bridge's casts and handles
    # included; a raw owner, a pointer cast and a warning in the library aren't.
    assert cpp.lint(ctx.cpp) == []
    money = work / "cpp/src/money.cpp"
    good = money.read_text(encoding="utf-8")
    money.write_text(
        good + "\nlong* shop_leak() {\n    int unused = 0;\n"
        "    return reinterpret_cast<long*>(new int(1));\n}\n",
        encoding="utf-8",
    )
    lines = good.count("\n")
    found = sorted((d.file_path, (d.line or 0) - lines, d.code) for d in cpp.lint(ctx.cpp))
    assert found == [
        ("cpp/src/money.cpp", 3, "clang-diagnostic-unused-variable"),
        ("cpp/src/money.cpp", 4, "codecoremorph-owning-memory"),
        ("cpp/src/money.cpp", 4, "cppcoreguidelines-pro-type-reinterpret-cast"),
    ]
    money.write_text(good, encoding="utf-8")
    # What a symbol map names is what the C++ index has (symbol_map).
    with IndexStore.open(cpp.index(ctx.cpp).path) as store:
        known = {s.qualified_name for s in store.symbols()}
    assert {"shop::Money::add", "shop::Money::Money", "shop::Money::to_string",
            "shop::operator+", "shop_cli_main"} <= known  # fmt: skip

    # The retirement: the C# goes, and CMake builds and tests the C++ alone.
    csharp_files = sorted(
        p.relative_to(work).as_posix()
        for p in work.rglob("*")
        if p.suffix in (".cs", ".csproj") and not {"bin", "obj"} & set(p.parts)
    )
    cpp.run_codemods(ctx.cpp, [RETIRE], csharp_files)
    assert not csharp.builds(ctx.csharp)
    assert not (work / "src").exists()
    assert not (work / "cpp/bridge").exists()
    configured = cpp.configure(ctx.cpp)
    assert configured.success, configured.diagnostics
    built = cpp.build(ctx.cpp)
    assert built.success, built.diagnostics
    after = {t.test_id: t.status for t in cpp.run_tests(ctx.cpp).cases}
    assert {t: after.get(t) for t in expected} == dict.fromkeys(expected, TestStatus.PASSED)
    assert _run_program(ctx, "a", "b", "c") == (3, "7.50 EUR\n")
