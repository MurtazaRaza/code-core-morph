"""A port from Java (D-91, D-92): the type units of a Java library, cut as a C# library's are
(leaves first, hierarchies and nested types together, interfaces Java implements reached
through calls back into Java), named by the module's jar, with Java's own generics rule; then
its test files and the retirement."""

from __future__ import annotations

import re
from collections.abc import Sequence

from codecoremorph.adapters.cpp import CppAdapter
from codecoremorph.adapters.portindex import type_sites
from codecoremorph.core.models import (
    GenericUse,
    Plan,
    PortRefusal,
    RefusalSite,
    RuleSite,
    SymbolKind,
    TestMethod,
    ThrowSite,
    TypeEntry,
    UnitStatus,
    WorkUnit,
)
from codecoremorph.planner.csharp import member_name, owner_of
from codecoremorph.planner.estimate import Estimator
from codecoremorph.planner.planner import PlannerSettings, build_plan
from codecoremorph.planner.project import ProjectModel
from codecoremorph.planner.sites import scan_rule
from codecoremorph.rules.applicability import StandardOrder
from codecoremorph.rules.registry import RuleRegistry

from .conftest import FakeProject
from .test_planner import EDITOR

POM = "pom.xml"
TESTS = "src/test/java"
MAIN = "src/main/java/shop"
REGISTRY = RuleRegistry.discover()
PORT = REGISTRY.get("port_java_type_to_cpp")
PORT_TESTS = REGISTRY.get("port_java_tests_to_cpp")
RETIRE = REGISTRY.get("retire_java_to_cpp")


class Library:
    """A Maven module in the index: its POM, sources, types, members and references."""

    def __init__(self, p: FakeProject) -> None:
        self.p = p
        self.main: list[str] = []
        self.tests: list[str] = []
        self.modules: dict[str, list[str]] = {}  # other modules' POMs: their sources
        p.file(POM, "<project><artifactId>shop</artifactId></project>\n", role="build",
               language="maven")  # fmt: skip

    def type(
        self,
        name: str,
        file: str,
        *,
        kind: str = "class",
        members: Sequence[str] = (),
        test: bool = False,
        **fields: object,
    ) -> str:
        """Declare ``name`` (``shop.Cart$ItemFilter``) with its members (``add(int)``) in
        ``file``; ``fields`` are the rest of its :class:`TypeEntry`."""
        module = fields.get("project")
        if isinstance(module, str) and module != POM:
            files = self.modules.setdefault(module, [])
        else:
            files = self.tests if test else self.main
        if file not in files:
            self.p.file(file, "package shop;\n" * 4, language="java",
                        role="test" if test else "source")  # fmt: skip
            files.append(file)
        type_id = f"java:{name}"
        member_ids = [f"{type_id}#{m}" for m in members]
        entry = TypeEntry.model_validate(
            {"id": type_id, "name": name.replace("$", "."), "kind": kind, "files": [file],
             "project": None if test else POM, "members": member_ids, **fields}
        )  # fmt: skip
        self.p.types.append(entry)
        self.p.symbol(type_id, file, (3, 1), (40, 1), kind=kind, visibility="public",
                      name=name.rsplit(".", 1)[-1])  # fmt: skip
        for member, member_id in zip(members, member_ids, strict=True):
            short = member.split("(", 1)[0]
            self.p.symbol(member_id, file, (4, 1), (5, 1),
                          kind="constructor" if short == "<init>" else "method",
                          visibility="public", name=short)  # fmt: skip
        return type_id

    def uses(self, file: str, by: str, used: str, line: int = 5) -> None:
        self.p.ref(file, used, line, from_symbol=by)

    def load(self) -> ProjectModel:
        self.p.targets.clear()
        self.p.target(POM, "library", self.main, artifacts=("shop.jar",))
        for module, files in self.modules.items():
            jar = module.split("/", 1)[0] + ".jar"
            self.p.target(module, "library", files, artifacts=(jar,))
        self.p.target(TESTS, "test", self.tests, (POM,))
        self.p.build()
        return ProjectModel.load(self.p.index_path, self.p.worktree, test_dirs=["src/test"])


def _plan(project: ProjectModel, *rules: object, keep: bool = False) -> Plan:
    def query(name: str, files: list[str]) -> list[RuleSite]:
        if name == "java_types":
            return type_sites(project.index_path, files, "Java")
        assert name == "test_cases"
        return [
            RuleSite(file=m.file, line=1, text=c)
            for m in project.test_methods
            for c in m.cases
            if m.file in files
        ]

    order = StandardOrder("java", ("java8", "java11", "java25"))
    scans = [
        scan_rule(r, project, order=order, source="java8", target="cpp17", query=query)  # type: ignore[arg-type]
        for r in (rules or (PORT,))
    ]
    return build_plan(
        project,
        scans,
        estimator=Estimator(prices=EDITOR.prices, guidance_chars={}),
        editor=EDITOR,
        settings=PlannerSettings(token_limit=500_000),
        language="java",
        source="java8",
        target="cpp17",
        target_language="cpp",
        port_names=CppAdapter().port_names,
        keep_source_api=keep,
    )


def _by_type(plan: Plan) -> dict[str, WorkUnit]:
    found: dict[str, WorkUnit] = {}
    for unit in plan.units:
        if unit.port is not None and unit.port.types:
            found[unit.port.types[0].removeprefix("java:")] = unit
        else:
            note = (unit.note or "").removeprefix("skipped: ")
            found[re.split(r"[^\w.$]", note, maxsplit=1)[0]] = unit
    return found


def _shop(p: FakeProject) -> Library:
    """The spike's shop: an enum, a value class, an interface its tests implement, a cart with
    a nested interface, and a book hierarchy."""
    lib = Library(p)
    category = lib.type("shop.Category", f"{MAIN}/Category.java", kind="enum",
                        members=["vatPercent()", "parse(java.lang.String)"])  # fmt: skip
    money = lib.type(
        "shop.Money", f"{MAIN}/Money.java", sealed=True,
        members=["<init>(long,java.lang.String)", "plus(shop.Money)", "hashCode()"],
        throws=[ThrowSite(exception="java.lang.IllegalArgumentException",
                          file=f"{MAIN}/Money.java", line=15,
                          member="java:shop.Money#<init>(long,java.lang.String)")],
    )  # fmt: skip
    rule = lib.type("shop.PriceRule", f"{MAIN}/PriceRule.java", kind="interface",
                    members=["apply(java.lang.String,shop.Category,shop.Money)"],
                    virtual_members=["java:shop.PriceRule#apply(java.lang.String,shop.Category,"
                                     "shop.Money)"])  # fmt: skip
    lib.uses(f"{MAIN}/PriceRule.java", rule, money)
    lib.uses(f"{MAIN}/PriceRule.java", rule, category)
    cart = lib.type(
        "shop.Cart", f"{MAIN}/Cart.java",
        members=["<init>(java.lang.String)", "addRule(shop.PriceRule)", "total()",
                 "count(shop.Cart$ItemFilter)"],
        virtual_members=["java:shop.Cart#total()"],
    )  # fmt: skip
    lib.type("shop.Cart$ItemFilter", f"{MAIN}/Cart.java", kind="interface", outer=cart,
             members=["accepts(java.lang.String,shop.Category)"])  # fmt: skip
    lib.uses(f"{MAIN}/Cart.java", f"{cart}#addRule(shop.PriceRule)", rule)
    lib.uses(f"{MAIN}/Cart.java", f"{cart}#total()", money)
    book = lib.type("shop.Book", f"{MAIN}/Book.java", abstract=True,
                    members=["<init>(java.lang.String)", "price()"],
                    virtual_members=["java:shop.Book#price()"])  # fmt: skip
    novel = lib.type("shop.Novel", f"{MAIN}/Book.java", base=book, members=["price()"])
    lib.uses(f"{MAIN}/Book.java", novel, book)
    lib.uses(f"{MAIN}/Book.java", f"{book}#price()", money)
    # The tests: a test double of the interface, and the cart's tests.
    double = lib.type("shop.CartTest$Half", f"{TESTS}/shop/CartTest.java", test=True,
                      interfaces=[rule], members=["apply(java.lang.String,shop.Category,"
                                                   "shop.Money)"])  # fmt: skip
    tests = lib.type("shop.CartTest", f"{TESTS}/shop/CartTest.java", test=True,
                     members=["totals()"])  # fmt: skip
    lib.uses(f"{TESTS}/shop/CartTest.java", f"{tests}#totals()", f"{cart}#total()")
    lib.uses(f"{TESTS}/shop/CartTest.java", double, rule)
    p.test_methods.append(
        TestMethod(file=f"{TESTS}/shop/CartTest.java", class_name="shop.CartTest",
                   method="totals", framework="junit4", project=TESTS,
                   cases=["junit:shop.CartTest#totals"])
    )  # fmt: skip
    return lib


def test_java_ids_name_members_and_their_types() -> None:
    types = {
        i: TypeEntry(id=i, name=i.removeprefix("java:"), kind=SymbolKind.CLASS)
        for i in ("java:shop.Cart", "java:shop.Cart$ItemFilter")
    }
    assert owner_of(types, "java:shop.Cart#add(java.lang.String)") == "java:shop.Cart"
    assert owner_of(types, "java:shop.Cart$ItemFilter#accepts(int)") == "java:shop.Cart$ItemFilter"
    assert owner_of(types, "java:shop.Other#x()") is None
    assert member_name("java:shop.Cart$ItemFilter#accepts(int)") == "shop.Cart.ItemFilter.accepts"
    assert member_name("java:shop.Money#<init>(long)") == "shop.Money"


def test_types_leaves_first_named_by_the_modules_jar(fake_project: FakeProject) -> None:
    plan = _plan(_shop(fake_project).load())
    assert [(u.port.crate, u.port.module, u.port.types) for u in plan.units if u.port] == [
        ("shop", "category", ["java:shop.Category"]),
        ("shop", "money", ["java:shop.Money"]),
        ("shop", "book", ["java:shop.Book", "java:shop.Novel"]),
        ("shop", "price_rule", ["java:shop.PriceRule"]),
        # A nested type goes with its outer type.
        ("shop", "cart", ["java:shop.Cart", "java:shop.Cart$ItemFilter"]),
    ]
    assert {u.status for u in plan.units} == {UnitStatus.PENDING}
    units = _by_type(plan)
    cart, rule, money = units["shop.Cart"], units["shop.PriceRule"], units["shop.Money"]
    assert cart.port is not None
    assert cart.port.build_target == POM
    assert "the Java types keeping their API as façades" in (cart.note or "")
    assert "cpp/src/cart.hpp" in (cart.note or "")
    assert units["shop.Book"].port is not None
    assert "virtual members, so the classes that derive" in (units["shop.Book"].note or "")
    # The interface's Java implementation reaches the C++ through a proxy.
    assert "implemented in Java by shop.CartTest.Half" in (rule.note or "")
    # A member that takes one of the project's interfaces calls into Java.
    assert "shop.Cart.addRule, shop.Cart.count take a callback" in (cart.note or "")
    assert "the façade throws them again in Java" in (money.note or "")
    assert rule.id in cart.needs
    assert cart.port.callers == [f"{TESTS}/shop/CartTest.java"]


def test_an_anonymous_class_goes_with_its_outer_type(fake_project: FakeProject) -> None:
    """A library's anonymous class is ported with the type it is in, and the note counts it
    rather than naming it by its number (``shop.Cart.1``)."""
    lib = _shop(fake_project)
    lib.type("shop.Cart$1", f"{MAIN}/Cart.java", outer="java:shop.Cart",
             interfaces=["java:shop.Cart$ItemFilter"])  # fmt: skip
    lib.type("shop.Book$1", f"{MAIN}/Book.java", outer="java:shop.Book")
    units = _by_type(_plan(lib.load()))
    cart, book = units["shop.Cart"], units["shop.Book"]
    assert cart.port is not None
    assert "java:shop.Cart$1" in cart.port.types
    note = cart.note or ""
    assert "with its nested types shop.Cart.ItemFilter and an anonymous class;" in note
    assert "shop.Cart.1" not in note
    assert "with an anonymous class of its own" in (book.note or "")


def test_overloads_taking_a_callback_are_named_once(fake_project: FakeProject) -> None:
    lib = _shop(fake_project)
    lib.type("shop.Till", f"{MAIN}/Till.java",
             members=["<init>(shop.PriceRule)", "<init>(shop.PriceRule,int)"])  # fmt: skip
    note = _by_type(_plan(lib.load()))["shop.Till"].note or ""
    assert "calls into Java: shop.Till takes a callback" in note


def test_what_a_port_from_java_refuses_and_what_uses_it(fake_project: FakeProject) -> None:
    lib = _shop(fake_project)
    lib.type(
        "shop.Ledger", f"{MAIN}/Ledger.java", members=["total()"],
        refusals=[RefusalSite(what=PortRefusal.BIG_NUMBERS, detail="java.math.BigDecimal",
                              file=f"{MAIN}/Ledger.java", line=7)],
    )  # fmt: skip
    report = lib.type("shop.Report", f"{MAIN}/Report.java", members=["print()"])
    lib.uses(f"{MAIN}/Report.java", f"{report}#print()", "java:shop.Ledger#total()")
    units = _by_type(_plan(lib.load()))
    ledger, printed = units["shop.Ledger"], units["shop.Report"]
    assert ledger.status is UnitStatus.SKIPPED
    assert "shop.Ledger uses big numbers: java.math.BigDecimal" in (ledger.note or "")
    assert printed.status is UnitStatus.SKIPPED
    assert "uses shop.Ledger, which this port refuses" in (printed.note or "")


def test_generics_port_to_their_bound_or_their_one_instantiation(
    fake_project: FakeProject,
) -> None:
    """Java erases a type parameter (D-92): a bounded one ports to its bound; an unbounded one
    ports where every use gives it the same argument, and is refused otherwise."""
    lib = _shop(fake_project)
    lib.type("shop.Shelf", f"{MAIN}/Shelf.java", members=["put(java.lang.Object)"],
             type_parameters=["T extends shop.Book"])  # fmt: skip
    box = lib.type("shop.Box", f"{MAIN}/Box.java", members=["get()"], type_parameters=["V"])
    pair = lib.type("shop.Pair", f"{MAIN}/Pair.java", members=["first()"],
                    type_parameters=["A", "B"])  # fmt: skip
    for args in (["java.lang.String"], ["java.lang.String"]):
        fake_project.generic_uses.append(
            GenericUse(symbol=box, arguments=args, file=f"{MAIN}/Cart.java", line=9)
        )
    for args in (["int", "int"], ["java.lang.String", "int"]):
        fake_project.generic_uses.append(
            GenericUse(symbol=pair, arguments=args, file=f"{MAIN}/Cart.java", line=9)
        )
    fake_project.generic_uses.append(
        GenericUse(symbol="java:shop.Shelf", arguments=["shop.Novel"], file=f"{MAIN}/Cart.java",
                   line=9)
    )  # fmt: skip
    units = _by_type(_plan(lib.load()))
    assert units["shop.Shelf"].status is UnitStatus.PENDING
    # Not C#'s one set of exports per type argument: the parameter ports to its bound.
    note = units["shop.Shelf"].note or ""
    assert "Java erases the type parameters, so T ports to its bound, shop.Book" in note
    assert "per type argument" not in note
    assert "V ports to the one argument the project gives it" in (units["shop.Box"].note or "")
    assert units["shop.Box"].status is UnitStatus.PENDING
    refused = units["shop.Pair"]
    assert refused.status is UnitStatus.SKIPPED
    assert "no bound on A, B" in (refused.note or "")
    assert "Java erases the parameter" in (refused.note or "")


def test_test_files_and_the_retirement(fake_project: FakeProject) -> None:
    plan = _plan(_shop(fake_project).load(), PORT, PORT_TESTS, RETIRE)
    tests = [u for u in plan.units if u.rules == ["port_java_tests_to_cpp"]]
    assert len(tests) == 1
    test = tests[0]
    assert test.port is not None
    assert test.port.module == "cart_test"
    assert test.port.tests == ["junit:shop.CartTest#totals"]
    assert "the Java tests stay until the retirement" in (test.note or "")
    retire = [u for u in plan.units if u.rules == ["retire_java_to_cpp"]]
    assert len(retire) == 1
    assert "retires the Java" in (retire[0].note or "")
    assert POM in retire[0].files
    assert "the CMake build then builds and tests the C++ alone" in (retire[0].note or "")


def test_types_in_two_modules_keep_their_tests_and_the_java(fake_project: FakeProject) -> None:
    """A port from Java takes one Maven module for now: with types in two, every type stays
    Java, so the test files that use them stay too, and the retirement waits on the refusal
    (Phase 9's task-planner, planned as a port, had them pending)."""
    lib = _shop(fake_project)
    fake_project.file("app/pom.xml", "<project><artifactId>app</artifactId></project>\n",
                      role="build", language="maven")  # fmt: skip
    lib.type("app.Planner", "app/src/main/java/app/Planner.java", members=["plan()"],
             project="app/pom.xml")  # fmt: skip
    plan = _plan(lib.load(), PORT, PORT_TESTS, RETIRE)
    (refused,) = [u for u in plan.units if u.rules == ["port_java_type_to_cpp"]]
    assert refused.status is UnitStatus.SKIPPED
    assert "the types to port are in 2 library projects" in (refused.note or "")
    (test,) = [u for u in plan.units if u.rules == ["port_java_tests_to_cpp"]]
    assert test.status is UnitStatus.SKIPPED
    assert test.note == (
        "skipped: src/test/java/shop/CartTest.java uses shop.Cart, shop.PriceRule, which this "
        "port refuses (see its unit)"
    )
    (retire,) = [u for u in plan.units if u.rules == ["retire_java_to_cpp"]]
    assert refused.id in retire.needs


def test_keep_source_api_keeps_a_library_and_its_tests(fake_project: FakeProject) -> None:
    """With ``keep_source_api``, a library with no program stays a Java library over the C++:
    there is nothing for a retirement to delete, so the plan has none, and the Java tests stay
    with the API they test."""
    plan = _plan(_shop(fake_project).load(), PORT, PORT_TESTS, RETIRE, keep=True)
    assert not [u for u in plan.units if u.rules == ["retire_java_to_cpp"]]
    (test,) = [u for u in plan.units if u.rules == ["port_java_tests_to_cpp"]]
    assert "the Java tests stay with the kept API (keep_source_api)" in (test.note or "")


def test_a_class_java_tests_extend_and_override_is_refused(fake_project: FakeProject) -> None:
    """A port from Java has no directors yet (D-92): a Java class staying Java that overrides a
    ported class's member, an anonymous one in the tests included, refuses the class."""
    lib = _shop(fake_project)
    lib.type(
        "shop.CartTest$1", f"{TESTS}/shop/CartTest.java", test=True, base="java:shop.Book",
        members=["price()"], outer="java:shop.CartTest",
    )  # fmt: skip
    plan = _plan(lib.load())
    (book,) = [u for u in plan.units if f"{MAIN}/Book.java" in u.files]
    assert book.status is UnitStatus.SKIPPED
    assert "an anonymous class in shop.CartTest (src/test/java/shop/CartTest.java)" in (
        book.note or ""
    )
    assert "overriding price" in (book.note or "")
    assert "has no directors yet (D-92)" in (book.note or "")


def test_a_class_the_tests_mock_is_refused(fake_project: FakeProject) -> None:
    """A Mockito mock is a Java subclass made at run time, overriding every member, with no
    native object behind it: the index records it as one in the test that mocks the class,
    so the port refuses the class as it does a test's subclass (D-92)."""
    lib = _shop(fake_project)
    lib.type(
        "shop.CartTest$mock$shop.Book", f"{TESTS}/shop/CartTest.java", test=True,
        base="java:shop.Book", members=["price()"], outer="java:shop.CartTest", sealed=True,
    )  # fmt: skip
    plan = _plan(lib.load())
    (book,) = [u for u in plan.units if f"{MAIN}/Book.java" in u.files]
    assert book.status is UnitStatus.SKIPPED
    assert "a Mockito mock in shop.CartTest (src/test/java/shop/CartTest.java)" in (book.note or "")
    assert "overriding price" in (book.note or "")


def test_a_parameterized_methods_cases_come_from_the_report(fake_project: FakeProject) -> None:
    """JUnit's Parameterized runner names its cases at run time (``#counts[pc]``): the index
    can't list them, so the unit carries the method's stem, and its test map must have every
    case the baseline ran (D-92). Without it, the unit had nothing to map and committed with
    nothing translated."""
    lib = _shop(fake_project)
    fake_project.test_methods.append(
        TestMethod(file=f"{TESTS}/shop/CartTest.java", class_name="shop.CartTest",
                   method="counts", framework="junit4", project=TESTS, exact=False)
    )  # fmt: skip
    plan = _plan(lib.load(), PORT, PORT_TESTS)
    (test,) = [u for u in plan.units if u.rules == ["port_java_tests_to_cpp"]]
    assert test.port is not None
    assert test.port.tests == ["junit:shop.CartTest#totals"]
    assert test.port.data_driven == ["junit:shop.CartTest#counts"]
    assert "shop.CartTest.counts takes its cases from data" in (test.note or "")
    # The count says so, not "0 test cases" for a file whose cases all come from data.
    assert (test.note or "").startswith(
        f"translates {TESTS}/shop/CartTest.java (1 test case and 1 method whose cases come "
        "from data) to C++"
    )
