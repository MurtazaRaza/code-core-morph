"""The safety invariants inside the unit loop, the abort guard and the unit's time limit
(FR-4.4, FR-14.1), with the fake adapter and a scripted model."""

from __future__ import annotations

import re
import time

import pytest

from codecoremorph.adapters.base import AdapterContext
from codecoremorph.adapters.cpp.adapter import CppAdapter
from codecoremorph.agent.loop import AgentSettings, UnitRunner
from codecoremorph.core.models import AttemptOutcome, TestAssertions
from codecoremorph.invariants.checker import InvariantChecker, InvariantSettings

from .conftest import TYPEDEFS, FakeAdapter, World, replace_edit, reply, submit, unit

MACRO = "#define DECLARE(T, N) typedef T N"
FIXED = "#define DECLARE(T, N) using N = T"
_DISABLED = re.compile(r"TEST\((\w+), (DISABLED_\w+)\)")


class MarkingAdapter(FakeAdapter):
    """The fake adapter, with GoogleTest's DISABLED_ prefix as its disable marker."""

    def detect_disabled_tests(self, ctx: AdapterContext, files: list[str]) -> list[str]:
        found: list[str] = []
        for path in files:
            text = (ctx.worktree / path).read_text(encoding="utf-8")
            for number, line in enumerate(text.splitlines(), start=1):
                for match in _DISABLED.finditer(line):
                    found.append(
                        f"{path}:{number}: disabled test {match.group(1)}.{match.group(2)}"
                    )
        return found


def _checker(world: World, **settings: object) -> InvariantChecker:
    return InvariantChecker(
        world.adapter,
        InvariantSettings(**settings),  # type: ignore[arg-type]
        excluded=lambda path: path.startswith("third_party/"),
        warnings_before={},
        store_dir=world.tmp / "unit-store",
    )


def _with(runner: UnitRunner, checker: InvariantChecker) -> UnitRunner:
    runner.invariants = checker
    return runner


def test_a_new_suppression_is_fed_back_and_fixed(world: World) -> None:
    silenced = FIXED + "  // NOLINT"
    runner, _ = world.runner(
        [
            reply(submit("s1", replace_edit("include/geo.h", MACRO, silenced))),
            reply(submit("s2", replace_edit("include/geo.h", silenced, FIXED))),
        ]
    )
    outcome = _with(runner, _checker(world)).run(unit("include/geo.h"), [TYPEDEFS])
    assert outcome.succeeded, outcome.reason
    first, second = outcome.attempts
    assert first.outcome is AttemptOutcome.INVARIANT_FAILED
    failed = {r.invariant: r for r in first.invariant_results if not r.passed}
    assert set(failed) == {"I-7"}
    assert "NOLINT in include/geo.h" in failed["I-7"].message
    assert second.outcome is AttemptOutcome.SUCCEEDED
    passed = {r.invariant for r in second.invariant_results if r.passed}
    assert {"I-1", "I-2", "I-3", "I-5", "I-6", "I-7", "I-8", "I-9", "I-10"} <= passed
    assert outcome.invariant_results == second.invariant_results
    feedback = runner.artifacts.attempt("U-0001", 1).feedback_md.read_text(encoding="utf-8")
    assert feedback.startswith("The changes break CodeCoreMorph's safety rules:")


def test_disabling_a_test_fails_the_attempt(world: World, git_commit: None) -> None:
    world.adapter = MarkingAdapter()
    disable = replace_edit("tests/geo_test.cpp", "TEST(Geo, Area)", "TEST(Geo, DISABLED_Area)")
    runner, _ = world.runner(
        [reply(submit("s1", replace_edit("include/geo.h", MACRO, FIXED), disable))],
        settings=AgentSettings(max_retry_budget=0, max_edit_retries=0),
    )
    runner.adapter = world.adapter
    target = unit("include/geo.h", "tests/geo_test.cpp")
    outcome = _with(runner, _checker(world)).run(target, [TYPEDEFS])
    assert not outcome.succeeded
    (attempt,) = outcome.attempts
    assert attempt.outcome is AttemptOutcome.INVARIANT_FAILED
    (i3,) = [r for r in attempt.invariant_results if r.invariant == "I-3"]
    assert not i3.passed
    assert "tests/geo_test.cpp: disabled test Geo.DISABLED_Area" in i3.message
    assert "TEST(Geo, Area)" in world.read("tests/geo_test.cpp")  # rolled back


class AssertingAdapter(MarkingAdapter):
    """With the C++ adapter's assertion extraction (I-4)."""

    def extract_assertions(self, ctx: AdapterContext, files: list[str]) -> list[TestAssertions]:
        return CppAdapter().extract_assertions(ctx, files)


def _weaken(world: World, **settings: object) -> tuple[AttemptOutcome | None, list[str], list[str]]:
    """A unit that also changes the test's expected value; returns the attempt's outcome, the
    unit's approvals and its warnings."""
    world.adapter = AssertingAdapter()
    weaker = replace_edit("tests/geo_test.cpp", "EXPECT_EQ(4, 4)", "EXPECT_EQ(5, 5)")
    runner, _ = world.runner(
        [reply(submit("s1", replace_edit("include/geo.h", MACRO, FIXED), weaker))],
        settings=AgentSettings(max_retry_budget=0, max_edit_retries=0),
    )
    runner.adapter = world.adapter
    target = unit("include/geo.h", "tests/geo_test.cpp")
    outcome = _with(runner, _checker(world, **settings)).run(target, [TYPEDEFS])
    return outcome.attempts[0].outcome, outcome.approvals, outcome.warnings


def test_a_changed_assertion_is_listed_for_review(world: World, git_commit: None) -> None:
    result, approvals, warnings = _weaken(world)
    assert result is AttemptOutcome.SUCCEEDED
    assert approvals == []
    assert warnings == [
        "I-4: Test assertions changed: tests/geo_test.cpp: Geo.Area no longer checks "
        "EXPECT_EQ(4,4)."
    ]


def test_a_changed_assertion_waits_for_the_gate_when_the_policy_says(
    world: World, git_commit: None
) -> None:
    result, approvals, _ = _weaken(world, gate_test_changes=True)
    assert result is AttemptOutcome.SUCCEEDED
    assert [a.split(":")[0] for a in approvals] == ["I-4"]


def test_a_characterization_tests_assertions_always_wait_for_the_gate(
    world: World, git_commit: None
) -> None:
    result, approvals, _ = _weaken(world, characterization_dir="tests")  # SPEC §7.11 step 6
    assert result is AttemptOutcome.SUCCEEDED
    assert [a.split(":")[0] for a in approvals] == ["I-4"]


def test_denied_test_edits_fail_the_attempt(world: World, git_commit: None) -> None:
    result, _, _ = _weaken(world, test_file_edits="deny")
    assert result is AttemptOutcome.INVARIANT_FAILED


def test_the_agents_diff_size_is_limited(world: World) -> None:
    runner, _ = world.runner(
        [reply(submit("s1", replace_edit("include/geo.h", MACRO, FIXED)))],
        settings=AgentSettings(max_retry_budget=0, max_edit_retries=0),
    )
    # The codemods change a line too; only the agent's own two changed lines count.
    outcome = _with(runner, _checker(world, max_diff_lines=1)).run(
        unit("include/geo.h"), [TYPEDEFS]
    )
    assert not outcome.succeeded
    (i8,) = [r for r in outcome.attempts[0].invariant_results if r.invariant == "I-8"]
    assert "change 2 lines, more than the limit of 1" in i8.message

    runner, _ = world.runner([reply(submit("s1", replace_edit("include/geo.h", MACRO, FIXED)))])
    outcome = _with(runner, _checker(world, max_diff_lines=2)).run(
        unit("include/geo.h"), [TYPEDEFS]
    )
    assert outcome.succeeded, outcome.reason


def test_the_unit_times_out_and_rolls_back(world: World) -> None:
    runner, scripted = world.runner(
        [reply(submit("s1", replace_edit("include/geo.h", MACRO, FIXED)))]
    )
    runner.deadline = time.monotonic() - 1
    head = world.worktree.head()
    outcome = runner.run(unit("include/geo.h"), [TYPEDEFS])
    assert not outcome.succeeded
    assert outcome.timed_out
    assert "time limit" in (outcome.reason or "")
    assert scripted.requests == []
    assert world.worktree.head() == head
    assert not world.worktree.has_changes()


class StopError(Exception):
    pass


def test_the_guard_stops_the_unit_and_rolls_back(world: World) -> None:
    runner, _ = world.runner([reply(submit("s1", replace_edit("include/geo.h", MACRO, FIXED)))])
    calls: list[int] = []

    def guard() -> None:
        calls.append(1)
        if len(calls) > 2:  # let the codemods run, then stop before the model call
            raise StopError

    runner.guard = guard
    with pytest.raises(StopError):
        runner.run(unit("include/geo.h"), [TYPEDEFS])
    assert not world.worktree.has_changes()
    assert "typedef double Scalar;" in world.read("include/geo.h")


@pytest.fixture
def git_commit(world: World) -> None:
    """A test file in the worktree, committed, for the unit to edit."""
    path = world.worktree.path / "tests" / "geo_test.cpp"
    path.parent.mkdir()
    path.write_text('#include "geo.h"\nTEST(Geo, Area) { EXPECT_EQ(4, 4); }\n', encoding="utf-8")
    world.worktree.commit("a test")
