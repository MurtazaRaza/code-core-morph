"""I-4, the assertion guard (FR-7.5, SPEC §7.10): changed or removed assertions are flagged,
gated by policy, always gated for characterization tests, and denied edits fail."""

from __future__ import annotations

from codecoremorph.core.models import Severity, TestAssertions
from codecoremorph.invariants.checks import check_assertions


def case(file: str, test: str, *assertions: str) -> TestAssertions:
    return TestAssertions(
        file=file, test=test, test_id=f"gtest:{test}", line=1, assertions=list(assertions)
    )


BEFORE = [
    case("tests/a_test.cpp", "A.One", "EXPECT_EQ(1,a())", "EXPECT_TRUE(b())"),
    case("tests/a_test.cpp", "A.Two", "EXPECT_EQ(2,c())"),
]


def test_unchanged_or_strengthened_assertions_pass() -> None:
    stronger = [
        *BEFORE[:1],
        case("tests/a_test.cpp", "A.Two", "EXPECT_EQ(2,c())", "EXPECT_GT(c(),0)"),
    ]
    assert check_assertions(BEFORE, BEFORE).passed
    assert check_assertions(BEFORE, stronger).passed
    assert check_assertions([], [case("tests/new_test.cpp", "N.One", "EXPECT_TRUE(x())")]).passed


def test_a_changed_assertion_is_flagged_for_review() -> None:
    after = [case("tests/a_test.cpp", "A.One", "EXPECT_EQ(2,a())", "EXPECT_TRUE(b())"), BEFORE[1]]
    result = check_assertions(BEFORE, after)
    assert not result.passed
    assert result.severity is Severity.WARNING
    assert not result.needs_approval
    assert result.message == (
        "Test assertions changed: tests/a_test.cpp: A.One no longer checks EXPECT_EQ(1,a())."
    )


def test_a_removed_case_is_flagged_and_the_policy_gates_it() -> None:
    result = check_assertions(BEFORE, BEFORE[:1], gated=True)
    assert not result.passed
    assert result.needs_approval
    assert "A.Two is gone" in result.message


def test_characterization_tests_always_need_approval() -> None:
    before = [case("tests/ccm/geo_test.cpp", "GeoCharacterization.Twice", "EXPECT_EQ(4,twice(2))")]
    after = [case("tests/ccm/geo_test.cpp", "GeoCharacterization.Twice", "EXPECT_EQ(5,twice(2))")]
    result = check_assertions(before, after, protected=lambda path: path.startswith("tests/ccm/"))
    assert result.needs_approval
    # The project's own tests stay review items when the policy doesn't gate them.
    mixed = check_assertions(BEFORE, BEFORE[:1], protected=lambda path: path.startswith("x/"))
    assert not mixed.needs_approval


def test_denied_test_edits_fail_the_attempt() -> None:
    result = check_assertions(BEFORE, BEFORE, deny_edits=True)
    assert not result.passed
    assert result.severity is Severity.ERROR
    assert "guardrails.test_file_edits: deny" in result.message
    assert check_assertions([], [case("t.cpp", "N.New", "EXPECT_TRUE(1)")], deny_edits=True).passed
