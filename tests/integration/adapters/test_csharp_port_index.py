"""The C# index of a port's source with the real toolchain (D-78): on a project written for it
(``data/csharp_port_index``), the Roslyn helper records the types with their hierarchies,
members and state, the uses of the generics with their type arguments, what a port refuses, the
exceptions each member throws, and the test methods of NUnit 3, xUnit 2, MSTest 2 and MSTest 3
with the IDs of their cases, which the projects' own TRX reports then give, one for one.

Native runs are marked ``toolchain`` (``codecoremorph toolchain install csharp``); Docker runs
are marked ``docker`` (``scripts/build-image csharp``). The restore needs the network.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from codecoremorph.adapters.base import AdapterContext
from codecoremorph.adapters.cpp import CppAdapter
from codecoremorph.adapters.csharp import CSharpAdapter
from codecoremorph.core.errors import SandboxError
from codecoremorph.core.ids import new_run_id
from codecoremorph.core.models import (
    Boundary,
    LanguageConfig,
    Plan,
    PortNoticeKind,
    PortRefusal,
    RuleSite,
    TestStatus,
    UnitStatus,
)
from codecoremorph.llm.catalog import ModelInfo
from codecoremorph.planner.estimate import Estimator
from codecoremorph.planner.planner import PlannerSettings, build_plan
from codecoremorph.planner.project import ProjectModel
from codecoremorph.planner.sites import scan_rule
from codecoremorph.rules.applicability import StandardOrder
from codecoremorph.rules.registry import RuleRegistry
from codecoremorph.sandbox.executor import Executor
from codecoremorph.sandbox.native import NativeExecutor
from codecoremorph.store.index import IndexStore

DATA = Path(__file__).with_name("data") / "csharp_port_index"
LANG = LanguageConfig(
    language="csharp",
    standard="csharp7.3",
    port_target="cpp",
    build_system="dotnet",
    test_dirs=["tests"],
    build_timeout_s=900,
    test_timeout_s=600,
)


PACK = (
    "port_csharp_type_to_cpp",
    "port_csharp_program_to_cpp",
    "port_csharp_tests_to_cpp",
    "retire_csharp_to_cpp",
)


def _cut(adapter: CSharpAdapter, ctx: AdapterContext, index: Path) -> Plan:
    """The planner's units over the index, by the csharp_to_cpp pack's rules and the C#
    adapter's queries (D-79)."""
    project = ProjectModel.load(index, ctx.worktree, test_dirs=["tests"])
    registry = RuleRegistry.discover()

    def query(name: str, files: list[str]) -> list[RuleSite]:
        return adapter.query(ctx, name, files)

    order = StandardOrder("csharp", ("csharp7.3", "csharp14"))
    scans = [
        scan_rule(registry.get(r), project, order=order, source="csharp7.3", target="cpp17",
                  query=query)
        for r in PACK
    ]  # fmt: skip
    return build_plan(
        project,
        scans,
        estimator=Estimator(prices=None, guidance_chars={}),
        editor=ModelInfo(provider="ollama", model="replay", context_window=200_000, local=True),
        settings=PlannerSettings(token_limit=500_000),
        language="csharp",
        source="csharp7.3",
        target="cpp17",
        target_language="cpp",
        port_names=CppAdapter().port_names,
    )


@pytest.fixture(params=[pytest.param("native", marks=pytest.mark.toolchain),
                        pytest.param("docker", marks=pytest.mark.docker)])  # fmt: skip
def ctx(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[AdapterContext]:
    work = tmp_path / "zoo"
    shutil.copytree(DATA, work)
    # NuGet's deepest files pass Windows' 260-character limit under pytest's temporary
    # directory: keep the cache's path short.
    short = Path(tempfile.mkdtemp(prefix="ccm"))
    for path in (tmp_path / "build", short / "cache", tmp_path / "logs"):
        path.mkdir()
    executor: Executor
    if request.param == "native":
        env: Path = request.getfixturevalue("csharp_toolchain_env")
        executor = NativeExecutor(log_dir=tmp_path / "exec", toolchain_env=env)
    else:
        from codecoremorph.sandbox.docker.executor import DockerExecutor, DockerSettings
        from codecoremorph.sandbox.provisioning import resolve_image

        try:
            image = resolve_image(CSharpAdapter().toolchain_requirements(LANG), None)
        except SandboxError as exc:
            pytest.skip(f"no C# toolchain image: {exc}")
        executor = DockerExecutor(
            DockerSettings(
                image=image,
                run_id=new_run_id(),
                worktree=work,
                build_dir=tmp_path / "build",
                cache_dir=short / "cache",
                log_dir=tmp_path / "exec",
                toolchain_env="/opt/codecoremorph/toolchains/csharp.env",
            )
        )
    executor.start()
    try:
        yield AdapterContext(
            worktree=work,
            build_dir=tmp_path / "build",
            cache_dir=short / "cache",
            log_dir=tmp_path / "logs",
            executor=executor,
            lang=LANG,
        )
    finally:
        executor.stop()
        shutil.rmtree(
            f"\\\\?\\{short.resolve()}" if sys.platform == "win32" else short, ignore_errors=True
        )


def test_a_port_sources_records_and_its_test_cases(ctx: AdapterContext) -> None:
    adapter = CSharpAdapter()
    assert adapter.configure(ctx).success
    index = adapter.index(ctx)
    assert not index.problems
    with IndexStore.open(index.path) as store:
        types = {t.id: t for t in store.port_types()}
        uses = store.generic_uses()
        methods = store.test_methods()
        entries = store.test_entries()
        symbols = {s.id: s for s in store.symbols()}
        public = {f.path for f in store.files() if f.public_api}

    # The library's public and protected members are the API its façades keep, which I-6
    # compares by signature, without what other assemblies can't see (a private setter); the
    # test projects' aren't (D-78).
    name = symbols["csharp:Zoo.Animal.Name"]
    assert (name.boundary, name.signature) == (Boundary.PUBLIC_API, "public string Name { get; }")
    assert symbols["csharp:Zoo.Animal.#ctor(System.String)"].boundary is Boundary.PUBLIC_API
    assert all(s.boundary is Boundary.NONE for s in symbols.values() if s.file.startswith("tests/"))
    assert "src/Zoo/Animal.cs" in public  # I-6 snapshots a unit's façades
    assert not [f for f in public if f.startswith("tests/")]
    # Hierarchies, virtual members, partial and nested types.
    assert types["csharp:Zoo.Animal"].abstract
    assert types["csharp:Zoo.Dog"].base == "csharp:Zoo.Animal"
    assert types["csharp:Zoo.Bird"].sealed
    assert "csharp:Zoo.Animal.Legs" in types["csharp:Zoo.Animal"].virtual_members
    assert types["csharp:Zoo.Ticket"].files == ["src/Zoo/Ticket.Price.cs", "src/Zoo/Ticket.cs"]
    assert types["csharp:Zoo.Ticket.Stub"].outer == "csharp:Zoo.Ticket"
    assert types["csharp:Zoo.ZooException"].external_bases == ["System.Exception"]
    assert types["csharp:Zoo.Pens"].static
    # Members, public fields, static state and thrown exceptions.
    keeper = types["csharp:Zoo.Keeper"]
    assert "csharp:Zoo.Keeper.Fed" in keeper.members
    assert keeper.instance_fields == ["Rations"]
    assert keeper.static_state == [
        "csharp:Zoo.Keeper.Log",
        "csharp:Zoo.Keeper.hired",
        "csharp:Zoo.Keeper.Current",
    ]
    assert [t.exception for t in keeper.throws] == [
        "System.ArgumentNullException",
        "Zoo.ZooException",
    ]
    # What a port refuses, each in its type.
    refused = {
        name: {r.what for r in types[f"csharp:Zoo.{name}"].refusals}
        for name in ("Loader", "Dynamo", "Mirror", "Query", "Record")
    }
    assert refused == {
        "Loader": {PortRefusal.ASYNC},
        "Dynamo": {PortRefusal.DYNAMIC},
        "Mirror": {PortRefusal.REFLECTION},
        "Query": {PortRefusal.EXPRESSION_TREE},
        "Record": {PortRefusal.SERIALIZATION},
    }
    assert not any(
        types[f"csharp:Zoo.{n}"].refusals for n in ("Animal", "Keeper", "Pen`1", "Pointer")
    )
    # What a port takes over instead (D-82): unsafe code, a platform check, native calls.
    assert {n.what for n in types["csharp:Zoo.Pointer"].notices} == {PortNoticeKind.UNSAFE}
    assert {n.detail for n in types["csharp:Zoo.Clock"].notices} == {
        "System.Runtime.InteropServices.RuntimeInformation.IsOSPlatform"
    }
    assert types["csharp:Zoo.NativeMethods"].native_only
    assert types["csharp:Zoo.Win32"].native_only
    assert not types["csharp:Zoo.Clock"].native_only
    (getpid,) = types["csharp:Zoo.NativeMethods"].native_imports
    assert (getpid.library, getpid.entry_point) == ("libc", "getpid")
    (ticks,) = types["csharp:Zoo.Win32"].native_imports
    assert (ticks.library, ticks.entry_point) == ("kernel32.dll", "GetTickCount")
    # Generics in use with their type arguments; Crate<T> has none.
    instantiations = {(u.symbol, tuple(u.arguments), u.open) for u in uses}
    assert ("csharp:Zoo.Pen`1", ("Zoo.Dog",), False) in instantiations
    assert ("csharp:Zoo.Pen`1", ("Zoo.Bird",), False) in instantiations
    assert ("csharp:Zoo.Pen`1", ("T",), True) in instantiations  # in Pens.Of<T>
    fold = "csharp:Zoo.Pen`1.Fold``1(``0,System.Func{``0,`0,``0})"
    assert (fold, ("int",), False) in instantiations
    assert not [u for u in uses if u.symbol == "csharp:Zoo.Crate`1"]

    # The test methods' cases are the IDs the TRX reports give.
    inexact = {(m.class_name, m.method) for m in methods if not m.exact}
    assert inexact == {
        ("Zoo.NUnit.AnimalTests", "FromASource"),
        ("Zoo.Xunit.AnimalFacts", "FromMembers"),
    }
    assert {m.framework for m in methods} == {"nunit", "xunit", "mstest"}

    # The planner's cut of the library: its types leaves first, the test projects' left out,
    # and what the first version refuses skipped with the reason.
    plan = _cut(adapter, ctx, index.path)
    types_rule = [u for u in plan.units if u.rules == ["port_csharp_type_to_cpp"]]
    ported = {u.port.module: u for u in types_rule if u.port is not None}
    assert {m: u.port.types for m, u in ported.items() if u.port} == {
        "animal": ["csharp:Zoo.Animal", "csharp:Zoo.Bird", "csharp:Zoo.Dog"],
        "clock": ["csharp:Zoo.Clock"],
        "i_feeder": ["csharp:Zoo.IFeeder"],
        "pen": ["csharp:Zoo.Pen`1"],
        "pointer": ["csharp:Zoo.Pointer"],
        "ticket": ["csharp:Zoo.Ticket", "csharp:Zoo.Ticket.Kind", "csharp:Zoo.Ticket.Stub"],
        "zoo_exception": ["csharp:Zoo.ZooException"],
    }
    # The types that only declare native functions have no unit; the plan says what the
    # library depends on (D-82).
    clock = ported["clock"]
    assert "calls libc's getpid through P/Invoke" in (clock.note or "")
    assert "asks which platform it runs on" in (clock.note or "")
    assert clock.port is not None
    assert clock.port.native_libraries == []  # the C library
    assert "unsafe code: unsafe First" in (ported["pointer"].note or "")
    assert {(d.kind.value, d.name, d.ported) for d in plan.dependencies} == {
        ("native_library", "libc", True),
        ("platform_api", "kernel32.dll", True),  # declared, and nothing calls it
    }
    assert all(u.status is UnitStatus.PENDING for u in ported.values())
    order = [u.port.module for u in types_rule if u.port is not None]
    assert order.index("animal") < order.index("i_feeder")
    assert order.index("animal") < order.index("pen")
    assert ported["i_feeder"].needs == [ported["animal"].id]
    assert "Zoo.Pen<T> is generic, used with <Zoo.Bird>, <Zoo.Dog>" in (ported["pen"].note or "")
    skipped = sorted((u.note or "").split(" ", 2)[1] for u in types_rule if u.port is None)
    assert skipped == sorted(["Zoo.Crate<T>", "Zoo.Pens.Of", "Zoo.Keeper", "Zoo.Loader",
                              "Zoo.Dynamo", "Zoo.Mirror", "Zoo.Query", "Zoo.Record"])  # fmt: skip
    assert {u.status for u in types_rule if u.port is None} == {UnitStatus.SKIPPED}
    # A unit per test file, after the types its tests use, its cases the exact ones; then the
    # retirement, after everything (the refused types stay C#, so it waits on them too).
    tests_rule = {u.port.module: u for u in plan.units
                  if u.rules == ["port_csharp_tests_to_cpp"] and u.port}  # fmt: skip
    assert {m: (u.files, len(u.port.tests)) for m, u in tests_rule.items() if u.port} == {
        "animal_checks_tests": (["tests/Zoo.MSTest2/AnimalChecks.cs"], 10),
        "animal_checks_tests_2": (["tests/Zoo.MSTest3/AnimalChecks.cs"], 10),
        "animal_facts_tests": (["tests/Zoo.Xunit/AnimalFacts.cs"], 15),
        "animal_tests": (["tests/Zoo.NUnit/AnimalTests.cs"], 28),
    }
    assert all(ported["animal"].id in u.needs for u in tests_rule.values())
    assert sorted(t for u in tests_rule.values() if u.port for t in u.port.tests) == sorted(
        e.test_id for e in entries
    )
    assert "Zoo.NUnit.AnimalTests.FromASource takes its cases from data" in (
        tests_rule["animal_tests"].note or ""
    )
    (retire,) = [u for u in plan.units if u.rules == ["retire_csharp_to_cpp"]]
    assert set(retire.needs) == {u.id for u in plan.units if u is not retire}
    assert "src/Zoo/Zoo.csproj" in retire.files
    assert adapter.build(ctx).success
    report = adapter.run_tests(ctx)
    assert {c.status for c in report.cases} == {TestStatus.PASSED}
    seen = {c.test_id for c in report.cases}
    predicted = {e.test_id for e in entries}
    assert predicted <= seen
    unpredicted = sorted(seen - predicted)
    assert unpredicted == [
        "dotnet:Zoo.NUnit.AnimalTests#FromASource(1)",
        "dotnet:Zoo.NUnit.AnimalTests#FromASource(2)",
        "dotnet:Zoo.Xunit.AnimalFacts#FromMembers(value: 1)",
    ]
