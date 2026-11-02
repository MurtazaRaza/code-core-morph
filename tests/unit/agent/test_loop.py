"""The single-unit loop with a scripted model and a fake adapter (FR-3.4, FR-4.1 to FR-4.6)."""

from __future__ import annotations

import json

from codecoremorph.agent.loop import AgentSettings
from codecoremorph.core.models import (
    AttemptOutcome,
    Postcondition,
    PostconditionType,
    Role,
    StopReason,
    TextBlock,
    ToolResult,
)
from codecoremorph.store.layout import StateDir

from .conftest import RUN_ID, TYPEDEFS, World, call, replace_edit, reply, submit, unit

MACRO = "#define DECLARE(T, N) typedef T N"
FIXED = "#define DECLARE(T, N) using N = T"
BROKEN_MACRO = "#define DECLARE(T, N) using N = BROKEN"


def test_codemods_alone_skip_the_agent(world: World) -> None:
    (world.worktree.path / "src" / "geo.cpp").write_text("typedef int Count;\n", encoding="utf-8")
    world.worktree.commit("a simple typedef")
    runner, scripted = world.runner([])
    outcome = runner.run(unit("src/geo.cpp"), [TYPEDEFS])
    assert outcome.succeeded
    assert outcome.agent_skipped
    assert scripted.requests == []
    assert world.read("src/geo.cpp") == "using Count = int;\n"
    assert outcome.changed_files == ["src/geo.cpp"]
    assert "build" in world.adapter.calls
    assert "test" in world.adapter.calls


def test_the_agent_reads_then_submits(world: World) -> None:
    runner, scripted = world.runner(
        [
            reply(call("read_file", "t1", path="src/geo.cpp", start_line=None, end_line=None)),
            reply(submit("s1", replace_edit("include/geo.h", MACRO, FIXED))),
        ]
    )
    outcome = runner.run(unit("include/geo.h"), [TYPEDEFS])
    assert outcome.succeeded, outcome.reason
    assert not outcome.agent_skipped
    assert [a.outcome for a in outcome.attempts] == [AttemptOutcome.SUCCEEDED]
    assert outcome.summary == "Done."
    assert FIXED in world.read("include/geo.h")
    assert "using Scalar = double;" in world.read("include/geo.h")  # the codemod's part
    assert "+#define DECLARE(T, N) using N = T" in outcome.diff

    first, second = scripted.requests
    context = first.messages[0].text
    assert "using Scalar = double;" in context  # files after codemods
    assert "replace_typedefs_with_using: 1 site(s)" in context
    assert "include/geo.h:3:" in context
    assert first.system[-1].cache_breakpoint
    assert first.messages[0].cache_breakpoint
    assert [t.name for t in first.tools][-1] == "submit_edits"
    assert first.provider_options == {"effort": "high"}
    # append-only: the second request extends the first
    assert second.messages[: len(first.messages)] == first.messages
    (result,) = second.messages[-1].content
    assert isinstance(result, ToolResult)
    assert result.call_id == "t1"
    assert "Scalar area(Scalar w" in result.content

    transcript = StateDir(world.tmp / "state").run(RUN_ID).attempt("U-0001", 1).transcript_jsonl
    roles = [json.loads(line)["role"] for line in transcript.read_text().splitlines()]
    assert roles == ["user", "assistant", "user", "assistant"]


def test_a_build_failure_is_fed_back_and_fixed(world: World) -> None:
    runner, scripted = world.runner(
        [
            reply(submit("s1", replace_edit("include/geo.h", MACRO, BROKEN_MACRO))),
            reply(submit("s2", replace_edit("include/geo.h", "using N = BROKEN", "using N = T"))),
        ]
    )
    outcome = runner.run(unit("include/geo.h"), [TYPEDEFS])
    assert outcome.succeeded, outcome.reason
    assert [a.outcome for a in outcome.attempts] == [
        AttemptOutcome.BUILD_FAILED,
        AttemptOutcome.SUCCEEDED,
    ]
    assert [a.role for a in outcome.attempts] == [Role.EDITOR, Role.FIXER]
    assert outcome.attempts[0].diagnostics[0].message == "use of undeclared identifier 'BROKEN'"
    retry = scripted.requests[1].messages[-1]
    applied, feedback = retry.content
    assert isinstance(applied, ToolResult)
    assert "Applied 1 edit(s)" in applied.content
    assert isinstance(feedback, TextBlock)
    assert "Attempt 1 failed. The build failed with 1 error." in feedback.text
    assert "> 3 | #define DECLARE(T, N) using N = BROKEN" in feedback.text
    feedback_file = StateDir(world.tmp / "state").run(RUN_ID).attempt("U-0001", 1).feedback_md
    assert "BROKEN" in feedback_file.read_text()


def test_rejected_edits_get_immediate_feedback(world: World) -> None:
    runner, scripted = world.runner(
        [
            reply(submit("s1", replace_edit("include/geo.h", "typedef nope", "x"))),
            reply(submit("s2", replace_edit("src/other.cpp", "return 1", "return 2"))),
            reply(submit("s3", replace_edit("include/geo.h", MACRO, FIXED))),
        ],
        settings=AgentSettings(max_retry_budget=0, max_edit_retries=2),
    )
    outcome = runner.run(unit("include/geo.h"), [TYPEDEFS])
    assert outcome.succeeded, outcome.reason
    assert len(outcome.attempts) == 1  # edit retries are inside the attempt, with no build
    not_found = scripted.requests[1].messages[-1].content[0]
    assert isinstance(not_found, ToolResult)
    assert not_found.is_error
    assert "No edit was applied" in not_found.content
    outside = scripted.requests[2].messages[-1].content[0]
    assert isinstance(outside, ToolResult)
    assert "outside the unit" in outside.content
    assert world.read("src/other.cpp") == "int other() { return 1; }\n"


def test_an_exhausted_budget_rolls_back(world: World) -> None:
    runner, _ = world.runner(
        [
            reply(submit("s1", replace_edit("include/geo.h", MACRO, FIXED + " // REGRESS"))),
            reply(submit("s2", replace_edit("include/geo.h", "// REGRESS", "// REGRESS again"))),
        ],
        settings=AgentSettings(max_retry_budget=1),
    )
    before = world.read("include/geo.h")
    outcome = runner.run(unit("include/geo.h"), [TYPEDEFS])
    assert not outcome.succeeded
    assert [a.outcome for a in outcome.attempts] == [AttemptOutcome.TESTS_FAILED] * 2
    assert outcome.attempts[0].regressions == ["gtest:Geo.Area"]  # the flaky test isn't one
    assert outcome.reason is not None
    assert "retry budget (1) ran out" in outcome.reason
    assert world.read("include/geo.h") == before  # codemod changes rolled back too
    assert not world.worktree.has_changes()


def test_refusal_and_no_submission_fail_the_unit(world: World) -> None:
    refused = reply(stop=StopReason.REFUSAL).model_copy(update={"refusal_category": "cyber"})
    runner, scripted = world.runner([refused])
    outcome = runner.run(unit("include/geo.h"), [TYPEDEFS])
    assert not outcome.succeeded
    assert outcome.reason == "the model refused (cyber)"
    assert len(scripted.requests) == 1

    runner, scripted = world.runner(
        [reply(TextBlock(text="Hmm.")), reply(TextBlock(text="I can't do this safely."))],
        settings=AgentSettings(max_retry_budget=0),
    )
    outcome = runner.run(unit("include/geo.h"), [TYPEDEFS])
    assert not outcome.succeeded
    assert [a.outcome for a in outcome.attempts] == [AttemptOutcome.NO_SUBMISSION]
    reminder = scripted.requests[1].messages[-1].text
    assert "You stopped without calling `submit_edits`" in reminder


def test_a_cut_off_reply_runs_no_tools(world: World) -> None:
    runner, scripted = world.runner(
        [
            reply(
                submit("s1", replace_edit("include/geo.h", MACRO, FIXED)),
                stop=StopReason.MAX_TOKENS,
            ),
            reply(submit("s2", replace_edit("include/geo.h", MACRO, FIXED))),
        ]
    )
    outcome = runner.run(unit("include/geo.h"), [TYPEDEFS])
    assert outcome.succeeded
    not_run, note = scripted.requests[1].messages[-1].content
    assert isinstance(not_run, ToolResult)
    assert (not_run.call_id, not_run.is_error) == ("s1", True)
    assert isinstance(note, TextBlock)
    assert "hit the output limit" in note.text


def test_warning_level_sites_still_need_the_agent(world: World) -> None:
    """Regression: a green build with only warning-level post-conditions left must not skip
    the agent while rule sites remain (FR-3.4)."""
    warning_rule = TYPEDEFS.model_copy(
        update={
            "rule": TYPEDEFS.rule.model_copy(
                update={"postconditions": [
                    Postcondition(type=PostconditionType.DETECTOR_EMPTY, severity="warning")
                ]}
            )
        }
    )  # fmt: skip
    runner, scripted = world.runner(
        [reply(submit("s1", replace_edit("include/geo.h", MACRO, FIXED)))]
    )
    outcome = runner.run(unit("include/geo.h"), [warning_rule])
    assert outcome.succeeded
    assert not outcome.agent_skipped
    assert len(scripted.requests) == 1
