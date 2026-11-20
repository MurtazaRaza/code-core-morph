"""What a port from Java's units tell the agent (D-92): the façades and the Java that keeps using
them, the members and cases to map by ID, and the retirement."""

from __future__ import annotations

from codecoremorph.agent.context import java_port_note
from codecoremorph.core.models import PortUnit, PortUnits, WorkUnit


def _type_unit() -> WorkUnit:
    return WorkUnit(
        id="U-0002",
        seq=2,
        rules=["port_java_type_to_cpp"],
        files=["src/main/java/shop/Cart.java", "pom.xml"],
        note="ports shop.Cart (src/main/java/shop/Cart.java) to C++",
        port=PortUnit(
            build_target="pom.xml",
            crate="shop",
            module="cart",
            callers=["src/test/java/shop/CartTest.java"],
            exports=["java:shop.Cart#total()", "java:shop.Cart#<init>(java.lang.String)"],
            types=["java:shop.Cart"],
        ),
    )


def test_a_type_units_note_names_its_facades_its_callers_and_its_members() -> None:
    note = java_port_note(_type_unit(), PortUnits.JAVA_TYPE, "cpp")
    assert "The façades: src/main/java/shop/Cart.java." in note  # not the POM
    assert "don't change: src/test/java/shop/CartTest.java" in note
    assert "codecoremorph.interop.CcmNative" in note
    assert "CcmInterop.check" in note
    assert "cpp/symbol-map.toml" in note
    assert "- java:shop.Cart#total()\n- java:shop.Cart#<init>(java.lang.String)" in note
    assert "rust/symbol-map.toml" in java_port_note(_type_unit(), PortUnits.JAVA_TYPE, "rust")


def test_a_test_files_note_names_its_cases() -> None:
    unit = WorkUnit(
        id="U-0006",
        seq=6,
        rules=["port_java_tests_to_cpp"],
        files=["src/test/java/shop/CartTest.java"],
        reference_files=["src/test/java/shop/CartTest.java"],
        port=PortUnit(
            build_target="src/test/java",
            crate="shop",
            module="cart_test",
            tests=["junit:shop.CartTest#totals"],
        ),
    )
    note = java_port_note(unit, PortUnits.TESTS, "cpp")
    assert "read-only" in note
    assert '`java = "junit:..."`' in note
    assert "- junit:shop.CartTest#totals" in note


def test_the_retirements_note() -> None:
    unit = WorkUnit(id="U-0009", seq=9, rules=["retire_java_to_cpp"], files=["pom.xml"])
    note = java_port_note(unit, PortUnits.RETIREMENT, "cpp")
    assert "retired the Java" in note
    assert "cpp/test-map.toml" in note


def test_the_java_guidance_promises_no_directors_and_no_new_files() -> None:
    """A port from Java has no directors yet (D-92), and a unit edits only its own files and
    its rule's scope: the guidance of both targets says so, and sends a proxy's helper class to
    the interface's own file."""
    from codecoremorph.rules.registry import RuleRegistry

    registry = RuleRegistry.discover()
    for target in ("cpp", "rust"):
        path = registry.get(f"port_java_type_to_{target}").guidance_path()
        assert path is not None
        text = " ".join(path.read_text(encoding="utf-8").split())
        assert "has no directors yet (D-92)" in text
        assert "runs on a **director**" not in text
        assert "In the interface's own file, after it, a package-private top-level class" in text
        assert "package-private constructor of the base's" in text
        tests = registry.get(f"port_java_tests_to_{target}").guidance_path()
        assert tests is not None
        assert "a director until now" not in tests.read_text(encoding="utf-8")


def test_a_test_files_note_names_its_data_driven_methods() -> None:
    unit = WorkUnit(
        id="U-0008",
        seq=8,
        rules=["port_java_tests_to_cpp"],
        files=["src/test/java/shop/PackingTest.java"],
        reference_files=["src/test/java/shop/PackingTest.java"],
        note="translates src/test/java/shop/PackingTest.java (0 test cases) to C++",
        port=PortUnit(
            build_target="src/test", crate="shop", module="packing_test",
            data_driven=["junit:shop.PackingTest#countsPieces"],
        ),
    )  # fmt: skip
    note = java_port_note(unit, PortUnits.TESTS, "cpp")
    assert "- junit:shop.PackingTest#countsPieces[…], each case Surefire reports" in note
    assert "(none but the note's)" not in note
