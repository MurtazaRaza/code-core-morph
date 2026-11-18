"""C# tests that reach what the library keeps ``internal``, through ``InternalsVisibleTo``
(D-89, the C# side of white-box tests): the type's unit names them, and its context has the
façades keep them."""

from __future__ import annotations

from codecoremorph.agent.context import csharp_port_note
from codecoremorph.core.models import PortUnits

from .conftest import FakeProject
from .test_csharp_units import LIB, TESTS, Library, _by_type, _plan  # pyright: ignore[reportPrivateUsage]


def test_what_the_tests_reach_through_internals_visible_to(fake_project: FakeProject) -> None:
    p = fake_project
    lib = Library(p).project(LIB).project(TESTS, "test")
    ledger = lib.type("Zoo.Ledger", "src/Zoo/Ledger.cs", members=["Add(System.Int32)"])
    # internal, which the index records as private: one the tests use, one they don't.
    p.symbol(f"{ledger}.Reset", "src/Zoo/Ledger.cs", (8, 1), (9, 1), kind="method",
             visibility="private", name="Reset")  # fmt: skip
    p.symbol(f"{ledger}.Audit", "src/Zoo/Ledger.cs", (10, 1), (11, 1), kind="method",
             visibility="private", name="Audit")  # fmt: skip
    gate = lib.type("Zoo.Gate", "src/Zoo/Gate.cs", members=["Open"])
    tests = lib.type("Zoo.Tests.LedgerTests", "tests/Zoo.Tests/LedgerTests.cs", project=TESTS,
                     members=["Resets"])  # fmt: skip
    for used in (f"{ledger}.Reset", f"{ledger}.Add(System.Int32)", f"{gate}.Open"):
        lib.uses("tests/Zoo.Tests/LedgerTests.cs", f"{tests}.Resets", used)
    plan = _plan(lib.load())
    units = _by_type(plan)
    unit = units["Zoo.Ledger"]
    assert unit.port is not None
    assert unit.port.white_box_uses == ["Reset"]  # Audit is no test's; Add is public
    assert unit.port.white_box_tests == ["tests/Zoo.Tests/LedgerTests.cs"]
    assert "; 1 test file uses what it keeps internal (Reset)" in (unit.note or "")
    note = csharp_port_note(unit, PortUnits.CSHARP_TYPE, "cpp")
    assert "through the library's `InternalsVisibleTo`: `Reset`" in note
    assert "`internal` as the C# had them" in note
    other = units["Zoo.Gate"]
    assert other.port is not None
    assert other.port.white_box_uses == []
    assert "InternalsVisibleTo" not in csharp_port_note(other, PortUnits.CSHARP_TYPE, "cpp")
