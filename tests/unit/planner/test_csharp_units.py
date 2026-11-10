"""A port from C# (D-78): the type units of a C# library, leaves first by the references between
its types. A reference cycle is one unit, and so is a class with virtual members with the
classes that derive from it; nested and partial types go with their type; an interface is a
unit of its own, its C# implementations reaching the port through calls back into C#. What the
first version refuses is skipped with the reason, and so is what uses it."""

from __future__ import annotations

from collections.abc import Sequence

from codecoremorph.adapters.cpp import CppAdapter
from codecoremorph.adapters.csharp.adapter import port_type_sites, program_sites
from codecoremorph.core.models import (
    GenericUse,
    Plan,
    PortRefusal,
    RefusalSite,
    RuleSite,
    ThrowSite,
    TypeEntry,
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
from .test_planner import EDITOR

LIB = "src/Zoo/Zoo.csproj"
TESTS = "tests/Zoo.Tests/Zoo.Tests.csproj"
PORT = RuleRegistry.discover().get("port_csharp_type_to_cpp")  # the csharp_to_cpp pack's
_KINDS = {"#ctor": "constructor", "Fed": "event"}


class Library:
    """A C# solution in the index: its projects, files, types, members and references."""

    def __init__(self, p: FakeProject) -> None:
        self.p = p
        self.sources: dict[str, list[str]] = {}
        self.kinds: dict[str, str] = {}
        self.references: dict[str, tuple[str, ...]] = {}

    def project(self, path: str, kind: str = "library", references: Sequence[str] = ()) -> Library:
        self.p.file(path, "<Project Sdk=\"Microsoft.NET.Sdk\" />\n", role="build",
                    language="msbuild")  # fmt: skip
        self.sources[path] = []
        self.kinds[path] = kind
        self.references[path] = tuple(references)
        return self

    def file(self, path: str, project: str = LIB) -> None:
        if path not in self.sources[project]:
            self.p.file(path, "namespace Zoo { }\n" * 4, language="csharp")
            self.sources[project].append(path)

    def type(
        self,
        name: str,
        file: str | Sequence[str],
        *,
        kind: str = "class",
        members: Sequence[str] = (),
        project: str = LIB,
        visibility: str = "public",
        signatures: dict[str, str] | None = None,
        **fields: object,
    ) -> str:
        """Declare ``name`` (``Zoo.Pen`1``) with its members (``Add(`0)``), each public, in
        ``file``; ``fields`` are the rest of its :class:`TypeEntry`."""
        files = [file] if isinstance(file, str) else list(file)
        for f in files:
            self.file(f, project)
        type_id = f"csharp:{name}"
        member_ids = [f"{type_id}.{m}" for m in members]
        entry = TypeEntry.model_validate(
            {"id": type_id, "name": name.split("`", 1)[0], "kind": kind, "files": sorted(files),
             "project": project, "members": member_ids, **fields}
        )  # fmt: skip
        self.p.types.append(entry)
        self.p.symbol(type_id, files[0], (3, 5), (20, 1), kind=kind, visibility=visibility,
                      name=name.rsplit(".", 1)[-1])  # fmt: skip
        for member, member_id in zip(members, member_ids, strict=True):
            short = member.split("(", 1)[0]
            self.p.symbol(
                member_id, files[0], (4, 1), (5, 1), kind=_KINDS.get(short, "method"),
                visibility="public", signature=(signatures or {}).get(member), name=short,
            )  # fmt: skip
        return type_id

    def uses(self, file: str, by: str, used: str, line: int = 5) -> None:
        """``by`` (a type or member ID) in ``file`` uses ``used``."""
        self.p.ref(file, used, line, from_symbol=by)

    def generic(self, symbol: str, arguments: Sequence[str], file: str, member: str,
                *, open_: bool = False) -> None:  # fmt: skip
        self.p.generic_uses.append(
            GenericUse(symbol=symbol, arguments=list(arguments), open=open_, file=file, line=7,
                       member=member)
        )  # fmt: skip

    def load(self) -> ProjectModel:
        self.p.targets.clear()  # loaded again after more types
        for path, files in self.sources.items():
            self.p.target(path, self.kinds[path], files, self.references[path])
        self.p.build()
        return ProjectModel.load(self.p.index_path, self.p.worktree, test_dirs=["tests"])


def _plan(project: ProjectModel) -> Plan:
    def query(name: str, files: list[str]) -> list[RuleSite]:
        assert name == "csharp_types"  # the C# adapter's, over the index
        return port_type_sites(project.index_path, files)

    order = StandardOrder("csharp", ("csharp7.3", "csharp8", "csharp14"))
    scan = scan_rule(PORT, project, order=order, source="csharp7.3", target="cpp17", query=query)
    return build_plan(
        project,
        [scan],
        estimator=Estimator(prices=EDITOR.prices, guidance_chars={}),
        editor=EDITOR,
        settings=PlannerSettings(token_limit=500_000),
        language="csharp",
        source="csharp7.3",
        target="cpp17",
        target_language="cpp",
        port_names=CppAdapter().port_names,
    )


def _by_type(plan: Plan) -> dict[str, WorkUnit]:
    """Each unit by its first type's name; a skipped unit, by the type its note names first."""
    found: dict[str, WorkUnit] = {}
    for unit in plan.units:
        if unit.port is not None:
            key = unit.port.types[0].removeprefix("csharp:")
        else:
            key = (unit.note or "").removeprefix("skipped: ").split(" ", 1)[0]
        found[key] = unit
    return found


def _zoo(p: FakeProject) -> Library:
    """The integration fixture's zoo, without what a port refuses."""
    lib = Library(p).project(LIB).project(TESTS, "test")
    animal = lib.type(
        "Zoo.Animal", "src/Zoo/Animal.cs", abstract=True,
        members=["#ctor(System.String)", "Name", "Sound", "Legs"],
        virtual_members=["csharp:Zoo.Animal.Sound", "csharp:Zoo.Animal.Legs"],
        throws=[ThrowSite(exception="System.ArgumentNullException", file="src/Zoo/Animal.cs",
                          line=9, member="csharp:Zoo.Animal.#ctor(System.String)")],
    )  # fmt: skip
    dog = lib.type(
        "Zoo.Dog", "src/Zoo/Animal.cs", base=animal, members=["#ctor(System.String)", "Sound"],
        virtual_members=["csharp:Zoo.Dog.Sound"],
    )  # fmt: skip
    bird = lib.type("Zoo.Bird", "src/Zoo/Animal.cs", base=animal, sealed=True,
                    members=["#ctor(System.String)", "Sound", "Legs"])  # fmt: skip
    lib.uses("src/Zoo/Animal.cs", dog, animal)
    lib.uses("src/Zoo/Animal.cs", bird, animal)
    feeder = lib.type("Zoo.IFeeder", "src/Zoo/Keeper.cs", kind="interface",
                      members=["Feed(Zoo.Animal)"])  # fmt: skip
    lib.uses("src/Zoo/Keeper.cs", f"{feeder}.Feed(Zoo.Animal)", animal)
    fold = "Fold``1(``0,System.Func{``0,`0,``0})"
    pen = lib.type(
        "Zoo.Pen`1", "src/Zoo/Pen.cs", type_parameters=["T"], members=["Add(`0)", "Count", fold],
        signatures={fold: "public TResult Zoo.Pen<T>.Fold<TResult>(TResult seed, "
                          "System.Func<TResult, T, TResult> step)"},
    )  # fmt: skip
    lib.uses("src/Zoo/Pen.cs", pen, animal)  # where T : Animal
    keeper = lib.type(
        "Zoo.Keeper", "src/Zoo/Keeper.cs",
        members=["#ctor(Zoo.IFeeder)", "Round(Zoo.Pen{Zoo.Dog})", "Fed"],
        static_state=["csharp:Zoo.Keeper.Log"],
        throws=[ThrowSite(exception="Zoo.ZooException", file="src/Zoo/Keeper.cs", line=30)],
    )  # fmt: skip
    round_ = f"{keeper}.Round(Zoo.Pen{{Zoo.Dog}})"
    lib.uses("src/Zoo/Keeper.cs", f"{keeper}.#ctor(Zoo.IFeeder)", feeder)
    lib.uses("src/Zoo/Keeper.cs", round_, pen)
    lib.uses("src/Zoo/Keeper.cs", round_, dog)
    lib.uses("src/Zoo/Keeper.cs", round_, f"{feeder}.Feed(Zoo.Animal)")
    lib.generic(pen, ["Zoo.Dog"], "src/Zoo/Keeper.cs", round_)
    ticket = lib.type("Zoo.Ticket", ["src/Zoo/Ticket.cs", "src/Zoo/Ticket.Price.cs"],
                      members=["#ctor(Zoo.Ticket.Kind)", "Price"])  # fmt: skip
    lib.type("Zoo.Ticket.Kind", "src/Zoo/Ticket.cs", kind="enum", outer=ticket)
    stub = lib.type("Zoo.Ticket.Stub", "src/Zoo/Ticket.cs", kind="struct", outer=ticket,
                    members=["Number"], instance_fields=["Number"])  # fmt: skip
    lib.uses("src/Zoo/Ticket.Price.cs", f"{ticket}.Price", "csharp:Zoo.Ticket.Kind")
    lib.uses("src/Zoo/Ticket.cs", f"{ticket}.#ctor(Zoo.Ticket.Kind)", stub)
    # The tests: a test double of the interface, and the generics with other type arguments.
    fake = lib.type("Zoo.Tests.FakeFeeder", "tests/Zoo.Tests/FakeFeeder.cs", project=TESTS,
                    interfaces=[feeder], members=["Feed(Zoo.Animal)"])  # fmt: skip
    lib.uses("tests/Zoo.Tests/FakeFeeder.cs", fake, feeder)
    tests = lib.type("Zoo.Tests.KeeperTests", "tests/Zoo.Tests/KeeperTests.cs", project=TESTS,
                     members=["Rounds"])  # fmt: skip
    rounds = f"{tests}.Rounds"
    for used in (f"{keeper}.#ctor(Zoo.IFeeder)", round_, pen, f"{dog}.#ctor(System.String)"):
        lib.uses("tests/Zoo.Tests/KeeperTests.cs", rounds, used)
    lib.generic(pen, ["Zoo.Bird"], "tests/Zoo.Tests/KeeperTests.cs", rounds)
    lib.generic(f"{pen}.{fold}", ["int"], "tests/Zoo.Tests/KeeperTests.cs", rounds)
    return lib


def test_types_leaves_first_with_their_hierarchies_and_nested_types(
    fake_project: FakeProject,
) -> None:
    plan = _plan(_zoo(fake_project).load())
    assert [(u.port.module, u.port.types) for u in plan.units if u.port] == [
        # A class with virtual members comes with the classes that derive from it.
        ("animal", ["csharp:Zoo.Animal", "csharp:Zoo.Bird", "csharp:Zoo.Dog"]),
        ("i_feeder", ["csharp:Zoo.IFeeder"]),
        ("pen", ["csharp:Zoo.Pen`1"]),
        ("keeper", ["csharp:Zoo.Keeper"]),
        # A partial type is one type, and its nested types go with it.
        ("ticket", ["csharp:Zoo.Ticket", "csharp:Zoo.Ticket.Kind", "csharp:Zoo.Ticket.Stub"]),
    ]
    assert {u.status for u in plan.units} == {UnitStatus.PENDING}
    units = _by_type(plan)
    animal, feeder, pen, keeper = (units[n] for n in ("Zoo.Animal", "Zoo.IFeeder", "Zoo.Pen`1",
                                                      "Zoo.Keeper"))  # fmt: skip
    ticket = units["Zoo.Ticket"]
    assert animal.files == ["src/Zoo/Animal.cs", LIB]
    assert ticket.files == ["src/Zoo/Ticket.Price.cs", "src/Zoo/Ticket.cs", LIB]
    assert animal.port is not None
    assert (animal.port.crate, animal.port.build_target) == ("zoo", LIB)
    assert "csharp:Zoo.Animal.Legs" in animal.port.exports
    assert "csharp:Zoo.Dog.#ctor(System.String)" in animal.port.exports
    # Who keeps calling its façades: the other types' files and the tests.
    assert animal.port.callers == [
        "src/Zoo/Keeper.cs",
        "src/Zoo/Pen.cs",
        "tests/Zoo.Tests/KeeperTests.cs",
    ]
    assert set(keeper.needs) == {animal.id, feeder.id, pen.id}
    assert pen.needs == [animal.id]
    assert ticket.needs == []
    options = animal.port.codemod_options()
    assert options["types"] == "csharp:Zoo.Animal,csharp:Zoo.Bird,csharp:Zoo.Dog"
    assert options["module"] == "animal"

    def note(unit: WorkUnit) -> str:
        assert unit.note is not None
        return unit.note

    assert note(animal).startswith(
        "ports Zoo.Animal, Zoo.Bird, Zoo.Dog (src/Zoo/Animal.cs) to C++: cpp/src/animal.hpp and "
        ".cpp, with the bridge cpp/bridge/animal.h and .cpp"
    )
    assert "Zoo.Animal has virtual members, so the classes that derive from it come" in note(animal)
    assert "throws System.ArgumentNullException" in note(animal)
    assert (
        "Zoo.IFeeder is implemented in C# by Zoo.Tests.FakeFeeder (tests/Zoo.Tests/FakeFeeder.cs)"
        in note(feeder)
    )
    assert "Zoo.Pen<T> is generic, used with <Zoo.Bird>, <Zoo.Dog>" in note(pen)
    assert "Zoo.Pen.Fold is generic, used with <int>" in note(pen)
    assert "Zoo.Pen.Fold takes a delegate" in note(pen)
    assert "the event Zoo.Keeper.Fed" in note(keeper)
    assert "static state: Zoo.Keeper.Log" in note(keeper)
    assert "with its nested types Zoo.Ticket.Kind, Zoo.Ticket.Stub" in note(ticket)
    assert "Zoo.Ticket.Stub is a value type" in note(ticket)  # its public field crosses
    assert "2 files keep using it through the façade" in note(pen)


def test_a_reference_cycle_is_one_unit(fake_project: FakeProject) -> None:
    lib = Library(fake_project).project(LIB)
    order = lib.type("Zoo.Order", "src/Zoo/Order.cs", members=["Lines"])
    line = lib.type("Zoo.Line", "src/Zoo/Line.cs", members=["Order"])
    lib.uses("src/Zoo/Order.cs", f"{order}.Lines", line)
    lib.uses("src/Zoo/Line.cs", f"{line}.Order", order)
    plan = _plan(lib.load())
    (unit,) = plan.units
    assert unit.port is not None
    assert unit.port.types == ["csharp:Zoo.Line", "csharp:Zoo.Order"]
    assert unit.port.module == "line"  # the first type by name
    assert unit.files == ["src/Zoo/Line.cs", "src/Zoo/Order.cs", LIB]
    assert "a reference cycle: Zoo.Line, Zoo.Order use each other" in (unit.note or "")


def test_a_base_without_virtual_members_goes_first_on_its_own(fake_project: FakeProject) -> None:
    lib = Library(fake_project).project(LIB)
    base = lib.type("Zoo.Shape", "src/Zoo/Shape.cs", members=["Name"])
    square = lib.type("Zoo.Square", "src/Zoo/Square.cs", base=base, members=["Side"])
    lib.uses("src/Zoo/Square.cs", square, base)
    plan = _plan(lib.load())
    assert [u.port.types for u in plan.units if u.port] == [["csharp:Zoo.Shape"],
                                                            ["csharp:Zoo.Square"]]  # fmt: skip
    shape, square_unit = plan.units
    assert square_unit.needs == [shape.id]


def test_what_the_first_version_refuses(fake_project: FakeProject) -> None:
    lib = _zoo(fake_project)
    loader = lib.type(
        "Zoo.Loader", "src/Zoo/Refused.cs", members=["Load"],
        refusals=[RefusalSite(what=PortRefusal.ASYNC, detail="async Load",
                              file="src/Zoo/Refused.cs", line=12)],
    )  # fmt: skip
    # What uses a refused type is refused with it.
    yard = lib.type("Zoo.Yard", "src/Zoo/Yard.cs", members=["Open"])
    lib.uses("src/Zoo/Yard.cs", f"{yard}.Open", f"{loader}.Load")
    lib.type("Zoo.Gate", "src/Zoo/Gate.cs", members=["Width"], instance_fields=["Width"])
    lib.type("Zoo.Crate`1", "src/Zoo/Crate.cs", type_parameters=["T"], members=["Content"])
    # An internal generic nothing instantiates is no API to keep: a template with no exports.
    lib.type("Zoo.Bag`1", "src/Zoo/Bag.cs", type_parameters=["T"], visibility="private")
    lib.type("Zoo.Pens", "src/Zoo/Pens.cs", static=True, members=["Of``1(``0[])"])
    # An open use counts when what it is in is instantiated: Box<T> through Boxes.Wrap<T>.
    box = lib.type("Zoo.Box`1", "src/Zoo/Box.cs", type_parameters=["T"], members=["Item"])
    boxes = lib.type("Zoo.Boxes", "src/Zoo/Boxes.cs", static=True, members=["Wrap``1(``0)"])
    wrap = f"{boxes}.Wrap``1(``0)"
    lib.uses("src/Zoo/Boxes.cs", wrap, box)
    lib.generic(box, ["``0"], "src/Zoo/Boxes.cs", wrap, open_=True)
    lib.generic(
        wrap, ["int"], "tests/Zoo.Tests/KeeperTests.cs", "csharp:Zoo.Tests.KeeperTests.Rounds"
    )
    plan = _plan(lib.load())
    units = _by_type(plan)
    skipped = {u.files[0]: u.note or "" for u in plan.units if u.status is UnitStatus.SKIPPED}
    assert set(skipped) == {
        "src/Zoo/Crate.cs",
        "src/Zoo/Gate.cs",
        "src/Zoo/Pens.cs",
        "src/Zoo/Refused.cs",
        "src/Zoo/Yard.cs",
    }
    assert skipped["src/Zoo/Refused.cs"] == (
        "skipped: Zoo.Loader uses async: async Load (src/Zoo/Refused.cs:12) (D-78)"
    )
    assert skipped["src/Zoo/Yard.cs"] == (
        "skipped: Zoo.Yard uses Zoo.Loader, which this port refuses (see its unit)"
    )
    assert "Zoo.Gate has non-private instance fields (Width)" in skipped["src/Zoo/Gate.cs"]
    assert "Zoo.Crate<T> is public and generic, and nothing" in skipped["src/Zoo/Crate.cs"]
    assert "Zoo.Pens.Of is public and generic" in skipped["src/Zoo/Pens.cs"]
    for name in ("Zoo.Bag`1", "Zoo.Box`1", "Zoo.Boxes"):
        assert units[name].status is UnitStatus.PENDING, name
    # The rest of the zoo is ported as before.
    assert units["Zoo.Keeper"].status is UnitStatus.PENDING


def test_a_class_the_tests_derive_from_and_override_is_refused(fake_project: FakeProject) -> None:
    """The ported code can't call a C# override (D-78); deriving without overriding is fine."""
    lib = _zoo(fake_project)
    lib.type("Zoo.Tests.Puppy", "tests/Zoo.Tests/Puppy.cs", project=TESTS,
             base="csharp:Zoo.Dog", members=["#ctor(System.String)"])  # fmt: skip
    assert _by_type(_plan(lib.load()))["Zoo.Animal"].status is UnitStatus.PENDING
    lib.type("Zoo.Tests.Robot", "tests/Zoo.Tests/Robot.cs", project=TESTS,
             base="csharp:Zoo.Dog", members=["Sound", "Legs"])  # fmt: skip
    units = _by_type(_plan(lib.load()))
    animal = units["Zoo.Dog"]
    assert animal.status is UnitStatus.SKIPPED
    assert animal.files == ["src/Zoo/Animal.cs", LIB]
    assert animal.note == (
        "skipped: Zoo.Dog has a subclass that stays C# and overrides it, Zoo.Tests.Robot "
        "(tests/Zoo.Tests/Robot.cs, overriding Sound, Legs): the ported code can't call a C# "
        "override (D-78)"
    )
    # What uses the hierarchy can't be ported either; Ticket uses none of it.
    assert units["Zoo.Keeper"].status is UnitStatus.SKIPPED
    assert units["Zoo.Keeper"].note == (
        "skipped: Zoo.Keeper uses Zoo.Animal, Zoo.IFeeder, Zoo.Pen<T>, which this port refuses "
        "(see their units)"
    )
    assert units["Zoo.Ticket"].status is UnitStatus.PENDING


def test_types_in_two_library_projects_are_refused(fake_project: FakeProject) -> None:
    lib = Library(fake_project).project(LIB).project("src/Farm/Farm.csproj")
    lib.type("Zoo.Animal", "src/Zoo/Animal.cs", members=["Name"])
    lib.type("Farm.Cow", "src/Farm/Cow.cs", project="src/Farm/Farm.csproj", members=["Moo"])
    (unit,) = _plan(lib.load()).units
    assert unit.status is UnitStatus.SKIPPED
    assert unit.files == ["src/Farm/Cow.cs", "src/Zoo/Animal.cs"]
    assert unit.note is not None
    assert unit.note.startswith(
        "skipped: the types to port are in 2 library projects (src/Farm/Farm.csproj, "
        "src/Zoo/Zoo.csproj)"
    )


def test_a_programs_types_go_with_the_program(fake_project: FakeProject) -> None:
    """A console project's types are its program unit's (a later rule), not type units."""
    lib = Library(fake_project).project(LIB).project("src/App/App.csproj", "executable")
    animal = lib.type("Zoo.Animal", "src/Zoo/Animal.cs", members=["Name"])
    program = lib.type("App.Program", "src/App/Program.cs", project="src/App/App.csproj",
                       static=True, members=["Main(System.String[])"])  # fmt: skip
    lib.uses("src/App/Program.cs", f"{program}.Main(System.String[])", animal)
    (unit,) = _plan(lib.load()).units
    assert unit.port is not None
    assert unit.port.types == ["csharp:Zoo.Animal"]
    assert unit.port.callers == ["src/App/Program.cs"]


def test_the_detectors_sites(fake_project: FakeProject) -> None:
    """The C# adapter's queries over the index (D-79): ``csharp_types``, each type a library
    declares and isn't nested, at its declaration; ``csharp_programs``, each program project at
    its ``Main``."""
    lib = _zoo(fake_project).project("src/App/App.csproj", "executable")
    lib.type("App.Options", "src/App/Options.cs", project="src/App/App.csproj")
    program = lib.type("App.Program", "src/App/Program.cs", project="src/App/App.csproj",
                       static=True, members=["Main(System.String[])"])  # fmt: skip
    project = lib.load()
    every = sorted(project.files)
    types = port_type_sites(project.index_path, every)
    assert [(s.file, s.line, s.text.split(":")[0]) for s in types] == [
        ("src/Zoo/Animal.cs", 3, "Zoo.Animal"),
        ("src/Zoo/Animal.cs", 3, "Zoo.Bird"),
        ("src/Zoo/Animal.cs", 3, "Zoo.Dog"),
        ("src/Zoo/Keeper.cs", 3, "Zoo.IFeeder"),
        ("src/Zoo/Keeper.cs", 3, "Zoo.Keeper"),
        ("src/Zoo/Pen.cs", 3, "Zoo.Pen"),
        # A partial type, in each of its files (this index declares it in Ticket.cs only).
        ("src/Zoo/Ticket.Price.cs", 1, "Zoo.Ticket"),
        ("src/Zoo/Ticket.cs", 3, "Zoo.Ticket"),
    ]
    assert types[0].text == "Zoo.Animal: port it, keeping its C# API as a façade"
    assert port_type_sites(project.index_path, ["src/Zoo/Pen.cs"])[0].text.startswith("Zoo.Pen")
    programs = program_sites(project.index_path, every)
    assert [(s.file, s.line, s.text) for s in programs] == [
        ("src/App/Program.cs", 4, "src/App/App.csproj: port the program"),
    ]
    assert program.startswith("csharp:App.")
    assert program_sites(project.index_path, ["src/Zoo/Animal.cs"]) == []
