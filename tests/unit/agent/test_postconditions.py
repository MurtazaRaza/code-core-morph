"""Rule post-conditions on a green build (FR-4.4, I-9): codemod_clean and hook, with the fake
adapter, whose codemod rewrites simple ``typedef T N;`` lines."""

from __future__ import annotations

from pathlib import Path

import pytest

from codecoremorph.agent import loop
from codecoremorph.core.models import (
    AttemptOutcome,
    LoadedRule,
    Postcondition,
    PostconditionType,
    RuleSite,
)

from .conftest import TYPEDEFS, World, replace_edit, reply, submit, unit

MACRO = "#define DECLARE(T, N) typedef T N"
FIXED = "#define DECLARE(T, N) using N = T"


def _with(*posts: Postcondition) -> LoadedRule:
    rule = TYPEDEFS.rule.model_copy(update={"postconditions": list(posts)})
    return TYPEDEFS.model_copy(update={"rule": rule})


def test_codemod_clean_puts_back_what_the_tools_would_still_change(world: World) -> None:
    rule = _with(Postcondition(type=PostconditionType.CODEMOD_CLEAN, severity="error"))
    # The agent fixes the macro but adds a typedef the codemod would rewrite.
    sloppy = FIXED + "\ntypedef long Big;"
    runner, _ = world.runner(
        [
            reply(submit("s1", replace_edit("include/geo.h", MACRO, sloppy))),
            reply(
                submit(
                    "s2", replace_edit("include/geo.h", "typedef long Big;", "using Big = long;")
                )
            ),
        ]
    )
    outcome = runner.run(unit("include/geo.h"), [rule])
    assert outcome.succeeded, outcome.reason
    first, second = outcome.attempts
    assert first.outcome is AttemptOutcome.INVARIANT_FAILED
    assert second.outcome is AttemptOutcome.SUCCEEDED
    feedback = runner.artifacts.attempt("U-0001", 1).feedback_md.read_text(encoding="utf-8")
    assert "the rule's tools still find work" in feedback
    assert "+using Big = long;" in feedback  # what the codemod would have done
    assert "using Big = long;" in world.read("include/geo.h")


def test_a_codemod_clean_warning_keeps_the_unit(world: World) -> None:
    rule = _with(Postcondition(type=PostconditionType.CODEMOD_CLEAN, severity="warning"))
    runner, _ = world.runner(
        [reply(submit("s1", replace_edit("include/geo.h", MACRO, FIXED + "\ntypedef long Big;")))]
    )
    outcome = runner.run(unit("include/geo.h"), [rule])
    assert outcome.succeeded, outcome.reason
    assert outcome.warnings == [
        "replace_typedefs_with_using: the rule's tools still find work (a warning)"
    ]
    assert "typedef long Big;" in world.read("include/geo.h")  # left as the agent wrote it


def test_a_hook_postcondition(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def hook(worktree: Path, files: list[str]) -> list[RuleSite]:
        calls.append(files)
        text = (worktree / "include" / "geo.h").read_text(encoding="utf-8")
        return (
            [RuleSite(file="include/geo.h", line=4, text="DECLARE")]
            if "DECLARE(int" in text
            else []
        )

    monkeypatch.setattr(loop, "load_hook", lambda spec: hook)
    rule = _with(Postcondition(type=PostconditionType.HOOK, severity="error", hook="pkg.mod:check"))
    runner, _ = world.runner(
        [
            reply(submit("s1", replace_edit("include/geo.h", MACRO, FIXED))),
            reply(
                submit("s2", replace_edit("include/geo.h", "DECLARE(int, Id);", "using Id = int;"))
            ),
        ]
    )
    outcome = runner.run(unit("include/geo.h"), [rule])
    assert outcome.succeeded, outcome.reason
    assert [a.outcome for a in outcome.attempts] == [
        AttemptOutcome.INVARIANT_FAILED,
        AttemptOutcome.SUCCEEDED,
    ]
    assert calls[0] == ["include/geo.h"]


def test_a_check_failing_after_the_codemods_is_in_the_first_message(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A green build whose hook (or codemod_clean) check fails: the agent's first message
    says what failed; the detector's own sites stay in their list."""

    def hook(worktree: Path, files: list[str]) -> list[RuleSite]:
        text = (worktree / "include" / "geo.h").read_text(encoding="utf-8")
        return [RuleSite(file="include/geo.h", line=4, text="DECLARE(int, Id);")] * (
            "DECLARE(int" in text
        )

    monkeypatch.setattr(loop, "load_hook", lambda spec: hook)
    rule = _with(Postcondition(type=PostconditionType.HOOK, severity="error", hook="pkg.mod:check"))
    runner, scripted = world.runner(
        [reply(submit("s1", replace_edit("include/geo.h", "DECLARE(int, Id);", "using Id = int;")))]
    )
    outcome = runner.run(unit("include/geo.h"), [rule])
    assert outcome.succeeded, outcome.reason
    first = "".join(getattr(b, "text", "") for b in scripted.requests[0].messages[0].content)
    assert "but the rules' checks fail" in first
    assert "include/geo.h:4" in first


def test_detector_sites_alone_leave_the_build_section_as_it_was() -> None:
    sites_only = loop.Verification(ok=False, stage="postconditions", feedback="x: 1 site")
    assert loop.first_build(sites_only) is None
    told = loop.Verification(ok=False, stage="postconditions", feedback="a\n\nb", untold="b")
    assert loop.first_build(told) == (
        "The project builds and its tests pass, but the rules' checks fail:\n\nb"
    )
    broken = loop.Verification(ok=False, stage="build", feedback="error: x")
    assert loop.first_build(broken) == "error: x"
    assert loop.first_build(loop.Verification(ok=True, stage="ok")) is None
