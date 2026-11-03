"""Whole runs driven by a host agent (D-44), with the fake adapter: the run parks at each unit
that needs edits, and the agent's ``unit submit`` or ``unit abort`` continues it."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from codecoremorph.agent.host import HostFiles
from codecoremorph.core.errors import ExitCode, SandboxError, UsageError
from codecoremorph.core.models import (
    ApprovalDecision,
    ApprovalGate,
    AttemptOutcome,
    HostStatus,
    RunReport,
    RunStatus,
    UnitStatus,
)
from codecoremorph.orchestrator.lifecycle import RunPlace, abort_run, overview
from codecoremorph.orchestrator.run import AgentAction, RunOptions, RunResult
from codecoremorph.store.layout import StateDir
from codecoremorph.vcs.lock import read_lock
from codecoremorph.vcs.messages import parse_trailers

from ..agent.conftest import replace_edit
from .conftest import FIXED, MACRO, EngineWorld

HOST = RunOptions(host=True, poll_s=0.01)
SUBMIT = AgentAction("submit")


def _write_edits(world: EngineWorld, run_id: str, *edits: dict[str, str]) -> Path:
    path = StateDir.for_repo(world.repo).run(run_id).root / "edits.json"
    body = {"edits": list(edits), "summary": "Alias declarations.", "review_notes": []}
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def _start(world: EngineWorld, **manifest: str) -> RunResult:
    config = world.manifest(**manifest)
    result, scripted, _ = world.run(config, [], options=HOST)
    assert scripted.requests == []
    return result


def _again(
    world: EngineWorld, run_id: str, agent: AgentAction | None = None, **kw: Any
) -> RunResult:
    config = world.repo / "migration_config.yaml"
    result, scripted, _ = world.run(
        config, [], options=HOST, run_id=run_id, resume=True, agent=agent, **kw
    )
    assert scripted.requests == []  # a host-agent run never calls a model
    return result


def _host_state(world: EngineWorld, run_id: str) -> Any:
    return HostFiles(StateDir.for_repo(world.repo).run(run_id).root).load()


def test_the_run_waits_for_the_agent_then_commits_its_edits(engine_world: EngineWorld) -> None:
    started = _start(engine_world)
    assert started.parked
    assert started.awaiting
    assert started.exit_code is ExitCode.SUCCESS  # waiting for the agent is the normal flow
    waiting = started.waiting
    assert waiting is not None
    assert (waiting["gate"], waiting["unit"]) == ("agent", "U-0001")
    assert (waiting["attempt"], waiting["attempts_allowed"]) == (1, 2)
    assert Path(waiting["context_file"]).is_file()
    assert any("U-0001: The unit needs your edits." in n for n in started.notes)
    state = _host_state(engine_world, started.run_id)
    assert state.in_run
    assert state.status is HostStatus.WAITING
    lock = read_lock(engine_world.repo / ".codecoremorph" / "lock")
    assert lock is not None
    assert lock.parked
    place = RunPlace.for_config(engine_world.repo / "migration_config.yaml")
    with place.store() as store:
        view = overview(place, store, place.record(store, started.run_id))
    assert view.agent is not None
    assert view.agent["unit"] == "U-0001"
    assert view.current == "U-0001"

    _write_edits(engine_world, started.run_id, replace_edit("include/geo.h", MACRO, FIXED))
    done = _again(engine_world, started.run_id, SUBMIT)
    assert done.status is RunStatus.COMPLETED, done.reason
    assert not done.parked
    assert done.exit_code is ExitCode.SUCCESS
    assert any("succeeded" in n for n in done.notes)
    assert any("is commit" in n for n in done.notes)
    # One commit: the codemods' checkpoint was squashed into the unit's commit.
    (message,) = engine_world.log(done.branch)
    trailers = parse_trailers(message)
    assert trailers["CodeCoreMorph-Unit"] == "U-0001"
    assert trailers["CodeCoreMorph-Models"] == "host"
    assert trailers["CodeCoreMorph-Attempts"] == "1"
    with engine_world.store() as store:
        (unit,) = store.units(done.run_id)
        (attempt,) = store.attempts(done.run_id)
        assert store.llm_calls(done.run_id) == []
        environment = store.run_environment(done.run_id)
    assert unit.status is UnitStatus.COMMITTED
    assert attempt.model == "host"
    assert attempt.outcome is AttemptOutcome.SUCCEEDED
    assert environment["agent"] == "host"
    assert "models" not in environment
    artifacts = StateDir.for_repo(engine_world.repo).run(done.run_id)
    report = RunReport.model_validate_json(artifacts.report_json.read_bytes())
    assert report.units[0].models == ["host"]
    assert report.final is not None
    assert report.final.passed
    assert "made no model calls" in artifacts.report_md.read_text(encoding="utf-8")
    final = _host_state(engine_world, done.run_id)
    assert final.status is HostStatus.SUCCEEDED
    assert final.commit is not None
    assert final.commit == engine_world.git(engine_world.repo, "rev-parse", done.branch).strip()


def test_a_failed_attempt_waits_again_with_its_feedback(engine_world: EngineWorld) -> None:
    started = _start(engine_world)
    _write_edits(engine_world, started.run_id, replace_edit("include/geo.h", MACRO, "BROKEN"))
    failed = _again(engine_world, started.run_id, SUBMIT)
    assert failed.parked
    assert failed.awaiting
    assert failed.waiting is not None
    assert failed.waiting["attempt"] == 2
    assert any("Attempt 1 failed" in n for n in failed.notes)
    _write_edits(engine_world, started.run_id, replace_edit("include/geo.h", "BROKEN", FIXED))
    done = _again(engine_world, started.run_id, SUBMIT)
    assert done.status is RunStatus.COMPLETED, done.reason
    with engine_world.store() as store:
        outcomes = [a.outcome for a in store.attempts(done.run_id)]
    assert outcomes == [AttemptOutcome.BUILD_FAILED, AttemptOutcome.SUCCEEDED]
    (message,) = engine_world.log(done.branch)
    assert parse_trailers(message)["CodeCoreMorph-Attempts"] == "2"


def test_a_rejected_submission_waits_in_the_same_attempt(engine_world: EngineWorld) -> None:
    started = _start(engine_world)
    _write_edits(engine_world, started.run_id, replace_edit("include/geo.h", "no such text", "x"))
    rejected = _again(engine_world, started.run_id, SUBMIT)
    assert rejected.awaiting
    assert rejected.waiting is not None
    assert rejected.waiting["attempt"] == 1
    assert any("rejected" in n for n in rejected.notes)


def test_unit_abort_gives_the_unit_up_and_the_run_goes_on(engine_world: EngineWorld) -> None:
    started = _start(engine_world)
    done = _again(
        engine_world, started.run_id, AgentAction("abort", "it needs a file outside the unit")
    )
    assert done.status is RunStatus.COMPLETED_WITH_FAILURES
    assert done.exit_code is ExitCode.COMPLETED_WITH_FAILURES
    with engine_world.store() as store:
        (unit,) = store.units(done.run_id)
    assert unit.status is UnitStatus.FAILED
    assert unit.note == "the agent gave the unit up: it needs a file outside the unit"
    assert _host_state(engine_world, done.run_id).status is HostStatus.ABORTED
    # Nothing was committed: the codemods' checkpoint went with the unit, and so did the branch.
    assert not engine_world.git(engine_world.repo, "branch", "--list", done.branch)


def test_resume_without_the_agent_parks_at_the_same_attempt(engine_world: EngineWorld) -> None:
    started = _start(engine_world)
    again = _again(engine_world, started.run_id)
    assert again.awaiting
    assert again.waiting == started.waiting
    assert again.notes == ()


def test_gates_park_a_host_run_instead_of_waiting(engine_world: EngineWorld) -> None:
    started = _start(engine_world, plan="required", unit="all")
    assert started.awaiting
    assert started.exit_code is ExitCode.SUCCESS
    assert started.waiting is not None
    assert started.waiting["gate"] == "plan"
    assert engine_world.asked == []  # nothing was asked in the process
    with pytest.raises(UsageError, match="waits at its plan gate"):
        _again(engine_world, started.run_id, SUBMIT)
    with engine_world.store() as store:
        store.add_approval(engine_world.approval(started.run_id, ApprovalDecision.APPROVE))
    at_agent = _again(engine_world, started.run_id)
    assert at_agent.waiting is not None
    assert at_agent.waiting["gate"] == "agent"

    _write_edits(engine_world, started.run_id, replace_edit("include/geo.h", MACRO, FIXED))
    at_gate = _again(engine_world, started.run_id, SUBMIT)
    assert at_gate.awaiting
    assert at_gate.waiting is not None
    assert (at_gate.waiting["gate"], at_gate.waiting["unit"]) == ("unit", "U-0001")
    assert at_gate.waiting["commit"] is not None  # the commit is on the branch while it waits
    with engine_world.store() as store:
        store.add_approval(
            engine_world.approval(
                started.run_id, ApprovalDecision.APPROVE, gate=ApprovalGate.UNIT, unit_id="U-0001"
            )
        )
    done = _again(engine_world, started.run_id)
    assert done.status is RunStatus.COMPLETED, done.reason


def test_a_failed_submit_keeps_the_edits_and_abort_still_works(
    engine_world: EngineWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sandbox fails while the edits are verified: the worktree goes back to the last
    checkpoint and the edits file is restored. LangGraph then replays the failed submit's
    answer on every resume; the agent's next command must still decide what happens."""
    started = _start(engine_world)
    edits = _write_edits(engine_world, started.run_id, replace_edit("include/geo.h", MACRO, FIXED))
    checkpoint = _host_state(engine_world, started.run_id).head
    failures = [SandboxError("Docker went away")]
    build = engine_world.adapter.build

    def failing_build(ctx: Any, targets: list[str] | None = None) -> Any:
        if failures:
            raise failures.pop()
        return build(ctx, targets)

    monkeypatch.setattr(engine_world.adapter, "build", failing_build)
    with pytest.raises(SandboxError):
        _again(engine_world, started.run_id, SUBMIT)
    assert edits.is_file()
    head = engine_world.git(engine_world.repo, "rev-parse", started.branch).strip()
    assert head == checkpoint
    parked = _again(engine_world, started.run_id)
    assert parked.awaiting
    assert parked.waiting is not None
    assert parked.waiting["gate"] == "agent"
    done = _again(engine_world, started.run_id, AgentAction("abort", "the sandbox is flaky"))
    assert done.status is RunStatus.COMPLETED_WITH_FAILURES
    with engine_world.store() as store:
        (unit,) = store.units(done.run_id)
    assert unit.note == "the agent gave the unit up: the sandbox is flaky"


def test_a_unit_whose_end_was_lost_runs_again(engine_world: EngineWorld) -> None:
    """The engine stopped after the unit succeeded and before the run committed it: its
    staged changes are gone, so the unit starts over and waits for the agent again."""
    started = _start(engine_world)
    files = HostFiles(StateDir.for_repo(engine_world.repo).run(started.run_id).root)
    files.save(files.load().model_copy(update={"status": HostStatus.SUCCEEDED}))
    again = _again(engine_world, started.run_id, SUBMIT)
    assert again.awaiting
    assert again.waiting is not None
    assert (again.waiting["unit"], again.waiting["attempt"]) == ("U-0001", 1)
    assert any("U-0001 starts over" in n for n in again.notes)
    # The submit was for the lost unit: the restarted one waits, its edits file untouched.
    assert _host_state(engine_world, started.run_id).status is HostStatus.WAITING
    # The restarted unit's codemods ran on the last real commit, not on the old checkpoint.
    log = engine_world.git(engine_world.repo, "log", "--format=%s", f"main..{started.branch}")
    assert log.split("\n").count("codecoremorph: checkpoint (codemods)") == 1


def test_an_offline_abort_drops_the_waiting_units_checkpoints(engine_world: EngineWorld) -> None:
    started = _start(engine_world)
    config = engine_world.repo / "migration_config.yaml"
    outcome = abort_run(config, started.run_id, reason="not now")
    assert not outcome.engine_alive
    assert not outcome.branch_kept  # only a checkpoint was on it
    assert not engine_world.git(engine_world.repo, "branch", "--list", started.branch)
    assert _host_state(engine_world, started.run_id).status is HostStatus.ABORTED
    place = RunPlace.for_config(config)
    with place.store() as store:
        (unit,) = store.units(started.run_id)
    assert unit.status is UnitStatus.PENDING
