"""The run graph end to end with the fake adapter and a scripted model (SPEC §7.5, FR-10,
FR-13, FR-14): units, commits, gates, stops, budgets and resume."""

from __future__ import annotations

import json
from typing import Any

from codecoremorph.core.errors import ExitCode
from codecoremorph.core.models import (
    ApprovalDecision,
    ApprovalGate,
    AttemptOutcome,
    CompletionRequest,
    CompletionResult,
    RunReport,
    RunStatus,
    UnitStatus,
)
from codecoremorph.core.paths import HomeLayout
from codecoremorph.orchestrator.run import RunEngine, RunOptions
from codecoremorph.vcs.messages import parse_trailers

from ..agent.conftest import replace_edit, reply, submit
from .conftest import FIXED, MACRO, EngineWorld


def fix_macro(call_id: str = "s1") -> CompletionResult:
    return reply(submit(call_id, replace_edit("include/geo.h", MACRO, FIXED)))


def test_a_run_commits_its_units_verifies_and_reports(engine_world: EngineWorld) -> None:
    config = engine_world.manifest()
    result, scripted, session = engine_world.run(config, [fix_macro()])
    assert result.status is RunStatus.COMPLETED, result.reason
    assert result.exit_code is ExitCode.SUCCESS
    assert not result.parked
    assert len(scripted.requests) == 1
    (message,) = engine_world.log(result.branch)
    trailers = parse_trailers(message)
    assert trailers["CodeCoreMorph-Run"] == result.run_id
    assert trailers["CodeCoreMorph-Unit"] == "U-0001"
    assert trailers["CodeCoreMorph-Models"] == "anthropic/claude-opus-5-5"
    with engine_world.store() as store:
        (unit,) = store.units(result.run_id)
        assert unit.status is UnitStatus.COMMITTED
        (attempt,) = store.attempts(result.run_id)
        assert attempt.outcome is AttemptOutcome.SUCCEEDED
        assert {r.invariant for r in attempt.invariant_results} >= {"I-1", "I-2", "I-7", "I-8"}
        assert len(store.llm_calls(result.run_id)) == 1
        assert store.get_run(result.run_id).status is RunStatus.COMPLETED  # type: ignore[union-attr]
        events = [e.type.value for e in store.list_events(result.run_id)]
    assert "unit.status" in events
    report = RunReport.model_validate_json(session.artifacts.report_json.read_bytes())
    assert report.final is not None
    assert report.final.ran
    assert report.final.passed
    assert not report.final.clean  # the fake adapter can't clean its build
    assert report.units[0].commit is not None
    assert report.usage_total.calls == 1
    (rule,) = report.rules
    assert (rule.planned_sites, rule.converted_sites, rule.committed) == (2, 2, 1)
    markdown = session.artifacts.report_md.read_text(encoding="utf-8")
    assert "**Status:** completed: every unit committed" in markdown
    assert "Code was sent to: anthropic." in markdown
    # The worktree is gone and the lock released; the branch is the result.
    assert not session.workspace.worktree.path.exists()
    assert not (engine_world.repo / ".codecoremorph" / "lock").exists()
    assert engine_world.git(engine_world.repo, "branch", "--list", result.branch)


def test_a_rejected_plan_ends_the_run(engine_world: EngineWorld) -> None:
    config = engine_world.manifest(plan="required")

    def decide(request: dict[str, Any]) -> Any:
        return engine_world.approval(request["run_id"], ApprovalDecision.REJECT, reason="not today")

    result, scripted, session = engine_world.run(
        config, [], options=engine_world.options(decide=decide)
    )
    assert result.status is RunStatus.REJECTED
    assert result.exit_code is ExitCode.REJECTED
    assert engine_world.asked[0]["gate"] == "plan"
    assert scripted.requests == []
    assert "rejected at a gate (not today)" in session.artifacts.report_md.read_text(
        encoding="utf-8"
    )
    # Nothing was committed, so the branch is gone too.
    assert not engine_world.git(engine_world.repo, "branch", "--list", result.branch)


def test_excluded_units_are_left_out(engine_world: EngineWorld) -> None:
    config = engine_world.manifest(plan="required")

    def decide(request: dict[str, Any]) -> Any:
        return engine_world.approval(
            request["run_id"], ApprovalDecision.APPROVE, exclude=["U-0001"]
        )

    result, scripted, _ = engine_world.run(config, [], options=engine_world.options(decide=decide))
    assert result.status is RunStatus.COMPLETED_WITH_FAILURES
    assert result.exit_code is ExitCode.COMPLETED_WITH_FAILURES
    assert scripted.requests == []
    with engine_world.store() as store:
        (unit,) = store.units(result.run_id)
    assert unit.status is UnitStatus.EXCLUDED


def test_a_parked_plan_gate_resumes_with_an_approval(engine_world: EngineWorld) -> None:
    config = engine_world.manifest(plan="required")
    result, _, _ = engine_world.run(
        config, [], options=engine_world.options(decide=lambda _r: None)
    )
    assert result.parked
    assert result.exit_code is ExitCode.ABORTED
    assert result.waiting is not None
    assert result.waiting["gate"] == "plan"
    with engine_world.store() as store:
        assert store.get_run(result.run_id).status is RunStatus.AWAITING_PLAN_APPROVAL  # type: ignore[union-attr]
        store.add_approval(engine_world.approval(result.run_id, ApprovalDecision.APPROVE))
    resumed, scripted, _ = engine_world.run(
        config, [fix_macro()], run_id=result.run_id, resume=True
    )
    assert resumed.status is RunStatus.COMPLETED, resumed.reason
    assert len(scripted.requests) == 1


def test_resume_yes_approves_a_waiting_plan(engine_world: EngineWorld) -> None:
    config = engine_world.manifest(plan="required")
    result, _, _ = engine_world.run(
        config, [], options=engine_world.options(decide=lambda _r: None)
    )
    assert result.parked
    resumed, _, _ = engine_world.run(
        config,
        [fix_macro()],
        run_id=result.run_id,
        resume=True,
        options=RunOptions(yes=True, poll_s=0.01),
    )
    assert resumed.status is RunStatus.COMPLETED, resumed.reason
    with engine_world.store() as store:
        (approval,) = store.approvals(result.run_id)
    assert approval.source == "cli --yes"


def test_the_unit_gate_approves_after_the_commit(engine_world: EngineWorld) -> None:
    config = engine_world.manifest(unit="all")
    seen: list[str] = []

    def decide(request: dict[str, Any]) -> Any:
        seen.append(request["gate"])
        # The branch holds the unit's commit while it waits, and the commit is green.
        assert request["commit"] is not None
        return engine_world.approval(
            request["run_id"], ApprovalDecision.APPROVE, gate=ApprovalGate.UNIT, unit_id="U-0001"
        )

    result, _, session = engine_world.run(
        config, [fix_macro()], options=engine_world.options(decide=decide)
    )
    assert seen == ["unit"]
    assert result.status is RunStatus.COMPLETED, result.reason
    report = json.loads(session.artifacts.report_json.read_text(encoding="utf-8"))
    assert report["units"][0]["approved_by"] == "test"


def test_a_rejected_unit_leaves_the_branch(engine_world: EngineWorld) -> None:
    config = engine_world.manifest(unit="all")

    def decide(request: dict[str, Any]) -> Any:
        return engine_world.approval(
            request["run_id"],
            ApprovalDecision.REJECT,
            gate=ApprovalGate.UNIT,
            unit_id="U-0001",
            reason="ugly",
        )

    result, _, _ = engine_world.run(
        config, [fix_macro()], options=engine_world.options(decide=decide)
    )
    assert result.status is RunStatus.COMPLETED_WITH_FAILURES
    with engine_world.store() as store:
        (unit,) = store.units(result.run_id)
        (attempt,) = store.attempts(result.run_id)
    assert unit.status is UnitStatus.FAILED
    assert unit.note == "rejected at the unit gate: ugly"
    assert attempt.outcome is AttemptOutcome.GATE_REJECTED
    assert not engine_world.git(engine_world.repo, "branch", "--list", result.branch)


def test_a_failed_unit_rolls_back_and_the_run_completes(engine_world: EngineWorld) -> None:
    config = engine_world.manifest()
    broken = reply(submit("s1", replace_edit("include/geo.h", MACRO, "BROKEN")))
    still = reply(submit("s2", replace_edit("include/geo.h", "BROKEN", "BROKEN // still")))
    result, _, session = engine_world.run(config, [broken, still])
    assert result.status is RunStatus.COMPLETED_WITH_FAILURES
    with engine_world.store() as store:
        (unit,) = store.units(result.run_id)
    assert unit.status is UnitStatus.FAILED
    assert "retry budget (1) ran out" in (unit.note or "")
    markdown = session.artifacts.report_md.read_text(encoding="utf-8")
    assert "### Failed units" in markdown
    assert "The final verification didn't run: no unit changed the code" in markdown


def test_an_abort_request_stops_at_the_next_safe_point(engine_world: EngineWorld) -> None:
    config = engine_world.manifest()

    def abort_then_reply(request: CompletionRequest) -> CompletionResult:
        with engine_world.store() as store:
            run_id = store.list_runs()[0].id
            store.add_approval(
                engine_world.approval(
                    run_id, ApprovalDecision.ABORT, gate=ApprovalGate.RUN, reason="enough"
                )
            )
        return reply(submit("s1", replace_edit("include/geo.h", MACRO, "BROKEN")))

    result, _, session = engine_world.run(config, [abort_then_reply])
    assert result.status is RunStatus.ABORTED
    assert result.exit_code is ExitCode.ABORTED
    with engine_world.store() as store:
        (unit,) = store.units(result.run_id)
        assert store.approvals(result.run_id, pending=True) == []
    assert unit.status is UnitStatus.PENDING  # rolled back, never committed
    assert "**Status:** aborted (enough)" in session.artifacts.report_md.read_text(encoding="utf-8")


def test_ctrl_c_parks_the_run_and_resume_finishes_it(engine_world: EngineWorld) -> None:
    config = engine_world.manifest()
    engines: list[RunEngine] = []

    def interrupt(request: CompletionRequest) -> CompletionResult:
        # The first Ctrl-C, during the first attempt: that attempt fails its build, and the
        # engine stops before the second one.
        engines[0].control.interrupted = True
        return reply(submit("s1", replace_edit("include/geo.h", MACRO, "BROKEN")))

    result, _, session = engine_world.run(config, [interrupt], on_engine=engines.append)
    assert result.parked
    assert not session.workspace.worktree.has_changes()
    assert session.workspace.worktree.head() == session.workspace.base_commit
    with engine_world.store() as store:
        (unit,) = store.units(result.run_id)
        assert store.attempts(result.run_id) == []
    assert unit.status is UnitStatus.PENDING
    resumed, scripted, _ = engine_world.run(
        config, [fix_macro()], run_id=result.run_id, resume=True
    )
    assert resumed.status is RunStatus.COMPLETED, resumed.reason
    assert len(scripted.requests) == 1


def test_the_token_budget_stops_the_run(engine_world: EngineWorld) -> None:
    config = engine_world.manifest(extra=", max_run_tokens: 100")
    first = reply(submit("s1", replace_edit("include/geo.h", MACRO, "BROKEN")))
    result, scripted, _ = engine_world.run(config, [first, fix_macro("s2")])
    assert result.status is RunStatus.BUDGET_EXHAUSTED
    assert result.exit_code is ExitCode.BUDGET_EXHAUSTED
    assert len(scripted.requests) == 1  # the cap is checked before the next call
    with engine_world.store() as store:
        (unit,) = store.units(result.run_id)
    assert unit.status is UnitStatus.PENDING
    assert unit.note == "stopped: the run's budget ran out"


def test_a_unit_committed_before_a_crash_is_recovered(engine_world: EngineWorld) -> None:
    """The engine died after the unit's commit, before its checkpoint: on the next pass the
    unit node finds the commit by its trailers instead of migrating the unit again."""
    config = engine_world.manifest(plan="required")

    def commit_then_approve(request: dict[str, Any]) -> Any:
        worktree = HomeLayout.resolve().worktree(request["run_id"])
        target = worktree / "include" / "geo.h"
        target.write_text(
            target.read_text(encoding="utf-8").replace(MACRO, FIXED), encoding="utf-8"
        )
        engine_world.git(worktree, "commit", "--quiet", "-am",
                         f"codecoremorph(x): geo.h\n\nCodeCoreMorph-Run: {request['run_id']}\n"
                         "CodeCoreMorph-Unit: U-0001")  # fmt: skip
        return engine_world.approval(request["run_id"], ApprovalDecision.APPROVE)

    result, scripted, session = engine_world.run(
        config, [], options=engine_world.options(decide=commit_then_approve)
    )
    assert result.status is RunStatus.COMPLETED, result.reason
    assert scripted.requests == []
    report = RunReport.model_validate_json(session.artifacts.report_json.read_bytes())
    assert "recovered after a restart" in report.units[0].warnings[0]
