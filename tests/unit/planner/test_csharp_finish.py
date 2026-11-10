"""The end of a port from C# (D-78, D-79): a unit per program, after the library types it uses;
a unit per test file, after the types its tests use, translated beside the C# tests; and the
retirement of the C#, after every other unit, or with ``keep_source_api`` of the programs only."""

from __future__ import annotations

from collections.abc import Collection, Sequence

from codecoremorph.adapters.cpp import CppAdapter
from codecoremorph.adapters.csharp.adapter import port_type_sites, program_sites
from codecoremorph.core.models import (
    Plan,
    PortRefusal,
    RefusalSite,
    RuleSite,
    TestMethod,
    UnitStatus,
    WorkUnit,
)
from codecoremorph.planner.estimate import Estimator
from codecoremorph.planner.planner import PlannerSettings, build_plan
from codecoremorph.planner.project import ProjectModel
from codecoremorph.planner.sites import scan_rule
from codecoremorph.rules.applicability import StandardOrder
from codecoremorph.rules.registry import RuleRegistry

from .conftest import FakeProject
from .test_csharp_units import LIB, TESTS, Library, _zoo
from .test_planner import EDITOR

APP = "src/App/App.csproj"
APP_TESTS = "tests/App.Tests/App.Tests.csproj"
_REGISTRY = RuleRegistry.discover()
RULES = [
    _REGISTRY.get(r)
    for r in (
        "port_csharp_type_to_cpp",
        "port_csharp_program_to_cpp",
        "port_csharp_tests_to_cpp",
        "retire_csharp_to_cpp",
    )
]


def _plan(project: ProjectModel, *, keep: bool = False, mapped: Collection[str] = ()) -> Plan:
    def query(name: str, files: list[str]) -> list[RuleSite]:
        if name == "csharp_types":
            return port_type_sites(project.index_path, files)
        if name == "csharp_programs":
            return program_sites(project.index_path, files)
        assert name == "test_cases"  # the C# adapter's reads the sources; these are the same
        wanted = set(files)
        return [
            RuleSite(file=m.file, line=1, text=f"dotnet:{m.class_name}#{m.method}")
            for m in project.test_methods
            if m.file in wanted
        ]

    order = StandardOrder("csharp", ("csharp7.3", "csharp8", "csharp14"))
    scans = [
        scan_rule(r, project, order=order, source="csharp7.3", target="cpp17", query=query)
        for r in RULES
    ]
    return build_plan(
        project,
        scans,
        estimator=Estimator(prices=EDITOR.prices, guidance_chars={}),
        editor=EDITOR,
        settings=PlannerSettings(token_limit=500_000),
        language="csharp",
        source="csharp7.3",
        target="cpp17",
        target_language="cpp",
        port_names=CppAdapter().port_names,
        keep_source_api=keep,
        mapped_tests=mapped,
    )


def _tests(lib: Library, file: str, cls: str, method: str, cases: Sequence[str] = (),
           *, exact: bool = True, project: str = TESTS) -> None:  # fmt: skip
    lib.p.test_methods.append(
        TestMethod(file=file, class_name=cls, method=method, framework="nunit", project=project,
                   cases=list(cases) or ([f"dotnet:{cls}#{method}"] if exact else []),
                   exact=exact)
    )  # fmt: skip


def _solution(p: FakeProject) -> Library:
    """The zoo with a program and test methods: KeeperTests uses the FakeFeeder test double,
    and one of its methods takes its cases from data."""
    lib = _zoo(p).project(APP, "executable", references=[LIB])
    main = lib.type("App.Program", "src/App/Program.cs", project=APP, static=True,
                    members=["Main(System.String[])"])  # fmt: skip
    options = lib.type("App.Options", "src/App/Options.cs", project=APP,
                       members=["Parse(System.String[])"])  # fmt: skip
    lib.uses("src/App/Program.cs", f"{main}.Main(System.String[])", options)
    lib.uses("src/App/Program.cs", f"{main}.Main(System.String[])",
             "csharp:Zoo.Keeper.#ctor(Zoo.IFeeder)")  # fmt: skip
    keeper_tests = "tests/Zoo.Tests/KeeperTests.cs"
    lib.uses(keeper_tests, "csharp:Zoo.Tests.KeeperTests.Rounds", "csharp:Zoo.Tests.FakeFeeder")
    _tests(lib, keeper_tests, "Zoo.Tests.KeeperTests", "Rounds")
    counts = "dotnet:Zoo.Tests.KeeperTests#Counts"
    _tests(lib, keeper_tests, "Zoo.Tests.KeeperTests", "Counts", [f"{counts}(1)", f"{counts}(2)"])
    _tests(lib, keeper_tests, "Zoo.Tests.KeeperTests", "FromData", exact=False)
    prices = lib.type("Zoo.Tests.Prices", "tests/Zoo.Tests/Prices.cs", project=TESTS,
                      members=["Child"])  # fmt: skip
    lib.uses("tests/Zoo.Tests/Prices.cs", f"{prices}.Child", "csharp:Zoo.Ticket.Price")
    _tests(lib, "tests/Zoo.Tests/Prices.cs", "Zoo.Tests.Prices", "Child")
    return lib


def _units(plan: Plan) -> dict[str, WorkUnit]:
    """Each unit by its rule's kind and module: ``type:animal``, ``program:app``,
    ``tests:keeper_tests``, ``retire``."""
    kinds = {
        "port_csharp_type_to_cpp": "type",
        "port_csharp_program_to_cpp": "program",
        "port_csharp_tests_to_cpp": "tests",
        "retire_csharp_to_cpp": "retire",
    }
    found: dict[str, WorkUnit] = {}
    for unit in plan.units:
        kind = kinds[unit.rules[0]]
        found[f"{kind}:{unit.port.module}" if unit.port else kind] = unit
    return found


def test_programs_tests_and_the_retirement_finish_the_port(fake_project: FakeProject) -> None:
    plan = _plan(_solution(fake_project).load())
    units = _units(plan)
    assert list(units) == [
        "type:animal",
        "type:i_feeder",
        "type:pen",
        "type:keeper",
        "type:ticket",
        "program:app",
        "tests:keeper_tests",
        "tests:prices_tests",
        "retire",
    ]
    assert {u.status for u in plan.units} == {UnitStatus.PENDING}
    ids = {k: u.id for k, u in units.items()}

    # The program: all of its files, after the library types it uses.
    program = units["program:app"]
    assert program.files == [APP, "src/App/Options.cs", "src/App/Program.cs"]
    assert program.needs == [ids["type:keeper"]]
    assert program.port is not None
    assert (program.port.crate, program.port.module, program.port.bin) == ("zoo", "app", "App")
    assert program.port.build_target == APP  # the façade's support goes beside it
    assert program.port.types == ["csharp:App.Options", "csharp:App.Program"]
    # The symbol map says where each member of its types went.
    assert program.port.exports == [
        "csharp:App.Options.Parse(System.String[])",
        "csharp:App.Program.Main(System.String[])",
    ]
    assert program.note == (
        "ports the program App (src/App/Options.cs, src/App/Program.cs) to C++: app_main in "
        "cpp/src/app_main.cpp, which the C# Main calls through the export zoo_app_main "
        "(cpp/bridge/app.cpp) until the retirement, and a CMake program App; 2 files; its types "
        "App.Options, App.Program; uses the library's Zoo.Keeper"
    )

    # A test file: read-only with its test doubles, its exact cases, after what it uses.
    keeper = units["tests:keeper_tests"]
    assert keeper.files == ["tests/Zoo.Tests/FakeFeeder.cs", "tests/Zoo.Tests/KeeperTests.cs"]
    assert keeper.reference_files == keeper.files
    assert keeper.port is not None
    assert keeper.port.tests == [
        "dotnet:Zoo.Tests.KeeperTests#Counts(1)",
        "dotnet:Zoo.Tests.KeeperTests#Counts(2)",
        "dotnet:Zoo.Tests.KeeperTests#Rounds",
    ]
    assert keeper.port.crate == "zoo"
    assert keeper.port.build_target == TESTS
    # The double implements IFeeder: its translation needs IFeeder's C++.
    assert set(keeper.needs) == {ids[k] for k in ("type:animal", "type:i_feeder", "type:pen",
                                                  "type:keeper")}  # fmt: skip
    assert keeper.note is not None
    assert keeper.note.startswith(
        "translates tests/Zoo.Tests/KeeperTests.cs (3 test cases) to C++: "
        "cpp/tests/keeper_tests.cpp, a GoogleTest program, each case mapped in the test map"
    )
    assert "Zoo.Tests.KeeperTests.FromData takes its cases from data" in keeper.note
    assert "with the test doubles and helpers of tests/Zoo.Tests/FakeFeeder.cs" in keeper.note
    # A file named for what it tests gets a test program's name.
    prices = units["tests:prices_tests"]
    assert prices.needs == [ids["type:ticket"]]
    assert prices.port is not None
    assert prices.port.tests == ["dotnet:Zoo.Tests.Prices#Child"]

    # The retirement: every C# file and the build, after everything else.
    retire = units["retire"]
    assert set(retire.needs) == {u.id for u in plan.units if u is not retire}
    for deleted in (APP, LIB, TESTS, "src/Zoo/Animal.cs", "tests/Zoo.Tests/FakeFeeder.cs"):
        assert deleted in retire.files
    assert retire.note is not None
    assert retire.note.startswith("retires the C#: deletes ")
    assert "the projects App, Zoo, Zoo.Tests" in retire.note
    assert retire.kept_api == []


def test_a_program_whose_types_other_csharp_uses_is_refused(fake_project: FakeProject) -> None:
    """A program's code moves whole: C# that uses its types would lose them."""
    lib = _solution(fake_project).project(APP_TESTS, "test", references=[APP])
    options_tests = lib.type("App.Tests.OptionsTests", "tests/App.Tests/OptionsTests.cs",
                             project=APP_TESTS, members=["Parses"])  # fmt: skip
    lib.uses("tests/App.Tests/OptionsTests.cs", f"{options_tests}.Parses",
             "csharp:App.Options.Parse(System.String[])")  # fmt: skip
    units = _units(_plan(lib.load()))
    program = units["program"]  # skipped: no port
    assert program.status is UnitStatus.SKIPPED
    assert program.note == (
        "skipped: App.Options is used outside the program (tests/App.Tests/OptionsTests.cs): a "
        "program's code moves whole, and that C# would lose it (D-78)"
    )
    assert program.id in units["retire"].needs  # it stays C#, so the C# stays


def test_what_uses_a_refused_type_stays_csharp(fake_project: FakeProject) -> None:
    lib = _solution(fake_project)
    loader = lib.type(
        "Zoo.Loader", "src/Zoo/Loader.cs", members=["Load"],
        refusals=[RefusalSite(what=PortRefusal.REFLECTION, detail="Activator.CreateInstance",
                              file="src/Zoo/Loader.cs", line=8)],
    )  # fmt: skip
    lib.uses("src/App/Program.cs", "csharp:App.Program.Main(System.String[])", f"{loader}.Load")
    loads = lib.type("Zoo.Tests.Loads", "tests/Zoo.Tests/Loads.cs", project=TESTS,
                     members=["Loads"])  # fmt: skip
    lib.uses("tests/Zoo.Tests/Loads.cs", f"{loads}.Loads", loader)
    _tests(lib, "tests/Zoo.Tests/Loads.cs", "Zoo.Tests.Loads", "Loads")
    plan = _plan(lib.load())
    skipped = {u.files[0]: u.note for u in plan.units if u.status is UnitStatus.SKIPPED}
    assert skipped == {
        "src/Zoo/Loader.cs": "skipped: Zoo.Loader uses reflection: Activator.CreateInstance "
        "(src/Zoo/Loader.cs:8) (D-78)",
        APP: "skipped: App uses Zoo.Loader, which this port refuses (see its unit)",
        "tests/Zoo.Tests/Loads.cs": "skipped: tests/Zoo.Tests/Loads.cs uses Zoo.Loader, which "
        "this port refuses (see its unit)",
    }
    retire = _units(plan)["retire"]
    assert {u.id for u in plan.units if u.status is UnitStatus.SKIPPED} <= set(retire.needs)


def test_tests_the_test_map_has_are_left_out(fake_project: FakeProject) -> None:
    """A later run on a partly ported branch: what the map pairs is translated already."""
    project = _solution(fake_project).load()
    mapped = [
        "dotnet:Zoo.Tests.KeeperTests#Rounds",
        "dotnet:Zoo.Tests.KeeperTests#FromData(3)",  # one of its data's cases: it was done
        "dotnet:Zoo.Tests.Prices#Child",
    ]
    units = _units(_plan(project, mapped=mapped))
    assert "tests:prices_tests" not in units
    keeper = units["tests:keeper_tests"]
    assert keeper.port is not None
    assert keeper.port.tests == [
        "dotnet:Zoo.Tests.KeeperTests#Counts(1)",
        "dotnet:Zoo.Tests.KeeperTests#Counts(2)",
    ]
    assert "FromData" not in (keeper.note or "")


def test_keep_source_api_retires_the_programs_only(fake_project: FakeProject) -> None:
    """The library stays a .NET library over the C++, with its C# tests; the programs are
    CMake programs, so they go, and so do the tests that need them."""
    lib = _solution(fake_project).project(APP_TESTS, "test", references=[APP])
    lib.type("App.Tests.RunTests", "tests/App.Tests/RunTests.cs", project=APP_TESTS)
    fake_project.file("Zoo.sln", "Microsoft Visual Studio Solution File\n", role="build",
                      language="msbuild")  # fmt: skip
    units = _units(_plan(lib.load(), keep=True))
    retire = units["retire"]
    assert retire.files == [
        "Zoo.sln",  # takes the deleted projects out
        APP,
        "src/App/Options.cs",
        "src/App/Program.cs",
        APP_TESTS,
        "tests/App.Tests/RunTests.cs",
    ]
    assert retire.note == (
        "retires the C# programs, which are C++ programs now: deletes App, App.Tests (3 files) "
        "and takes them out of Zoo.sln; the rest (Zoo, Zoo.Tests) stays as a .NET library over "
        "the C++ for .NET consumers (keep_source_api), its façades calling the bridge"
    )
    assert retire.kept_api == [LIB, TESTS]  # the unit's context names what stays


def test_keep_source_api_without_programs_retires_nothing(fake_project: FakeProject) -> None:
    units = _units(_plan(_zoo(fake_project).load(), keep=True))
    assert "retire" not in units
