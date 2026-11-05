"""The test map of a port (D-60): its form, the post-condition, parity through it, and the
assertion guard across languages."""

from __future__ import annotations

from pathlib import Path

from codecoremorph.adapters.rust import RustAdapter
from codecoremorph.core.models import TestAssertions, TestStatus
from codecoremorph.invariants import test_map as tm
from codecoremorph.invariants.test_map import (
    TestPair,
    by_source,
    gone_but_mapped,
    parity,
    project_test_pairs,
    read_test_map,
    weaker_translations,
)

MAP = """\
# a comment
[[test]]
cpp = "gtest:Rational.Adds"
rust = "nextest:calc_rs::rational_test::adds"

[[test]]
cpp = "ctest:calc_tests"
note = "only runs a GoogleTest binary"

[[test]]
cpp = "gtest:Rational.Parse"
rust = "nextest:calc_rs::rational_test::parse"
note = "returns an Option"
"""

PASSED, FAILED = TestStatus.PASSED, TestStatus.FAILED


def _pairs() -> dict[str, TestPair]:
    entries, problems = read_test_map(MAP, "cpp", "rust")
    assert problems == []
    return by_source(entries)


def test_reading_the_map() -> None:
    pairs = _pairs()
    assert pairs["gtest:Rational.Adds"] == TestPair(
        "gtest:Rational.Adds", "nextest:calc_rs::rational_test::adds", None
    )
    assert pairs["ctest:calc_tests"].target is None
    assert pairs["gtest:Rational.Parse"].note == "returns an Option"
    bad = '[[test]]\nrust = "x"\n\n[[test]]\ncpp = "a"\nrust = ["x", "y"]\n'
    assert read_test_map(bad, "cpp", "rust") == (
        [],
        ["entry 1 has no `cpp` test ID", "a: `rust` must be one test ID"],
    )
    assert read_test_map("[[test", "cpp", "rust")[1][0].startswith("the file isn't valid TOML")
    assert read_test_map('test = "x"', "cpp", "rust") == (
        [],
        ["`test` must be an array of tables: [[test]]"],
    )


def test_the_post_condition() -> None:
    entries, _ = read_test_map(MAP, "cpp", "rust")
    statuses = {"nextest:calc_rs::rational_test::adds": PASSED}
    required = ["gtest:Rational.Adds", "gtest:Rational.Parse", "gtest:Rational.Missing"]
    assert tm.test_map_problems(entries, required, statuses) == [
        "gtest:Rational.Parse: its test nextest:calc_rs::rational_test::parse didn't run",
        "gtest:Rational.Missing has no entry",
    ]
    statuses["nextest:calc_rs::rational_test::parse"] = FAILED
    assert tm.test_map_problems(entries, required[:2], statuses) == [
        "gtest:Rational.Parse: its test nextest:calc_rs::rational_test::parse failed"
    ]
    twice = [
        TestPair("a", "x", None),
        TestPair("a", "y", None),
        TestPair("b", "x", None),
        TestPair("c", None, None),
    ]
    assert tm.test_map_problems(twice, [], {}) == [
        "a is mapped 2 times; keep one entry",
        "x stands for 2 tests; map each test to its own",
        "c: name the test that took it over, or say why not",
    ]
    assert tm.test_map_feedback("port_tests", "rust/test-map.toml", []) == ""
    feedback = tm.test_map_feedback("port_tests", "rust/test-map.toml", ["c has no entry"])
    assert feedback.startswith("port_tests: the test map rust/test-map.toml isn't whole yet")
    assert feedback.endswith("\n- c has no entry")


def test_parity_through_the_map() -> None:
    pairs = _pairs()
    passing = ["gtest:Rational.Adds", "gtest:Rational.Parse", "ctest:calc_tests", "gtest:Other.T"]
    # While the C++ tests run, they pass, and so must the Rust tests mapped to them.
    both = {
        "gtest:Rational.Adds": PASSED,
        "gtest:Rational.Parse": PASSED,
        "ctest:calc_tests": PASSED,
        "gtest:Other.T": PASSED,
        "nextest:calc_rs::rational_test::adds": FAILED,
    }
    result = parity(passing, both, pairs)
    assert result.failing == ["nextest:calc_rs::rational_test::adds"]
    assert result.missing == ["nextest:calc_rs::rational_test::parse"]
    # After retirement the Rust tests stand for them; a test mapped to nothing is retired.
    rust_only = {
        "nextest:calc_rs::rational_test::adds": PASSED,
        "nextest:calc_rs::rational_test::parse": PASSED,
    }
    result = parity(passing, rust_only, pairs)
    assert (result.failing, result.missing, result.retired) == (
        [],
        ["gtest:Other.T"],
        ["ctest:calc_tests"],
    )
    # I-3: a test gone from a run counts as removed unless the map accounts for it.
    gone = ["gtest:Rational.Adds", "gtest:Rational.Parse", "ctest:calc_tests", "gtest:Other.T"]
    seen = {"nextest:calc_rs::rational_test::adds"}
    assert gone_but_mapped(gone, seen, pairs) == ["gtest:Rational.Parse", "gtest:Other.T"]


def test_translations_that_check_less() -> None:
    pairs = list(_pairs().values())
    sources = [
        TestAssertions(file="t.cpp", test="Rational.Adds", test_id="gtest:Rational.Adds", line=1,
                       assertions=["EXPECT_EQ(a, b)", "EXPECT_EQ(c, d)"]),
        TestAssertions(file="t.cpp", test="Rational.Parse", test_id="gtest:Rational.Parse",
                       line=9, assertions=["EXPECT_TRUE(x)"]),
    ]  # fmt: skip
    targets = [
        TestAssertions(file="r.rs", test="adds", test_id="nextest:calc_rs::rational_test::adds",
                       line=1, assertions=["assert_eq!(a, b)"]),
    ]  # fmt: skip
    assert weaker_translations(pairs, sources, targets) == [
        "gtest:Rational.Adds made 2 check(s); its test nextest:calc_rs::rational_test::adds "
        "makes 1",
        "ctest:calc_tests has no translation (only runs a GoogleTest binary)",
        "gtest:Rational.Parse: its test nextest:calc_rs::rational_test::parse isn't in the sources",
    ]


def test_a_projects_map(tmp_path: Path) -> None:
    assert project_test_pairs(tmp_path, None, "cpp", "rust") == {}
    assert project_test_pairs(tmp_path, RustAdapter(), "cpp", "rust") == {}
    (tmp_path / "rust").mkdir()
    (tmp_path / "rust" / "test-map.toml").write_text(MAP, encoding="utf-8")
    assert set(project_test_pairs(tmp_path, RustAdapter(), "cpp", "rust")) == {
        "gtest:Rational.Adds",
        "ctest:calc_tests",
        "gtest:Rational.Parse",
    }
