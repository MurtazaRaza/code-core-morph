"""Decisions, offline aborts, status and clean on parked and finished runs (FR-10.2, FR-14.4,
FR-6.5), with the fake adapter."""

from __future__ import annotations

import pytest

from codecoremorph.core.errors import RunNotActiveError, UsageError
from codecoremorph.core.models import (
    ApprovalDecision,
    ApprovalGate,
    CompletionResult,
    RunReport,
    RunStatus,
    UnitStatus,
)
from codecoremorph.core.paths import HomeLayout
from codecoremorph.orchestrator.lifecycle import (
    RunPlace,
    abort_run,
    clean_runs,
    overview,
    record_decision,
)

from ..agent.conftest import replace_edit, reply, submit
from .conftest import FIXED, MACRO, EngineWorld


def fix_macro() -> CompletionResult:
    return reply(submit("s1", replace_edit("include/geo.h", MACRO, FIXED)))


def _parked_at_plan(world: EngineWorld) -> str:
    config = world.manifest(plan="required")
    result, _, _ = world.run(config, [], options=world.options(decide=lambda _r: None))
    assert result.parked
    return result.run_id


def test_approve_a_parked_plan_then_resume(engine_world: EngineWorld) -> None:
    run_id = _parked_at_plan(engine_world)
    config = engine_world.repo / "migration_config.yaml"
    place = RunPlace.for_config(config)
    with place.store() as store:
        view = overview(place, store, place.record(store, None))
    assert view.waiting == ["plan"]
    assert not view.engine_alive

    decision = record_decision(config, None, decision=ApprovalDecision.APPROVE)
    assert decision.approval.gate is ApprovalGate.PLAN
    assert decision.approval.run_id == run_id
    assert not decision.engine_alive
    resumed, _, _ = engine_world.run(config, [fix_macro()], run_id=run_id, resume=True)
    assert resumed.status is RunStatus.COMPLETED


def test_the_plan_can_be_approved_while_characterization_tests_are_written(
    engine_world: EngineWorld,
) -> None:
    """Regression: ``characterizing`` (Phase 6) comes before the plan gate too."""
    from datetime import UTC, datetime

    run_id = _parked_at_plan(engine_world)
    config = engine_world.repo / "migration_config.yaml"
    with engine_world.store() as store:
        store.set_run_status(run_id, RunStatus.CHARACTERIZING, datetime.now(UTC))
    decision = record_decision(config, None, decision=ApprovalDecision.APPROVE)
    assert decision.approval.gate is ApprovalGate.PLAN


def test_a_unit_decision_needs_a_waiting_unit(engine_world: EngineWorld) -> None:
    run_id = _parked_at_plan(engine_world)
    config = engine_world.repo / "migration_config.yaml"
    with pytest.raises(UsageError, match="isn't waiting for approval"):
        record_decision(config, run_id, decision=ApprovalDecision.APPROVE, unit_id="U-0001")
    with pytest.raises(UsageError, match="no run"):
        record_decision(config, "20990101-000000-0000", decision=ApprovalDecision.APPROVE)


def test_abort_a_parked_run_without_its_engine(engine_world: EngineWorld) -> None:
    run_id = _parked_at_plan(engine_world)
    config = engine_world.repo / "migration_config.yaml"
    worktree = HomeLayout.resolve().worktree(run_id)
    assert worktree.is_dir()
    outcome = abort_run(config, run_id, reason="changed my mind")
    assert not outcome.engine_alive
    assert not outcome.branch_kept
    assert outcome.report_md is not None
    report = RunReport.model_validate_json(outcome.report_md.with_suffix(".json").read_bytes())
    assert report.status is RunStatus.ABORTED
    assert report.reason == "changed my mind"
    assert not worktree.exists()
    assert not (engine_world.repo / ".codecoremorph" / "lock").exists()
    with pytest.raises(RunNotActiveError, match="already ended"):
        abort_run(config, run_id, reason=None)


def test_abort_takes_a_waiting_units_commit_off_the_branch(engine_world: EngineWorld) -> None:
    config = engine_world.manifest(unit="all")
    result, _, _ = engine_world.run(
        config, [fix_macro()], options=engine_world.options(decide=lambda _r: None)
    )
    assert result.parked
    assert result.waiting is not None
    assert result.waiting["gate"] == "unit"
    assert result.head != engine_world.git(engine_world.repo, "rev-parse", "main").strip()
    outcome = abort_run(config, result.run_id, reason=None)
    assert not outcome.branch_kept
    place = RunPlace.for_config(config)
    with place.store() as store:
        (unit,) = store.units(result.run_id)
    assert unit.status is UnitStatus.FAILED
    assert unit.note == "the run was aborted while the unit waited for approval"
    assert not engine_world.git(engine_world.repo, "branch", "--list", result.branch)


def test_clean_keeps_active_runs_and_removes_finished_ones(engine_world: EngineWorld) -> None:
    config = engine_world.manifest()
    done, _, _ = engine_world.run(config, [fix_macro()])
    assert done.status is RunStatus.COMPLETED
    home = HomeLayout.resolve()
    home.build_dir(done.run_id).mkdir(parents=True, exist_ok=True)
    parked = _parked_at_plan(engine_world)
    place = RunPlace.for_config(config)
    with pytest.raises(UsageError, match="name a run"):
        clean_runs(place, run_id=None, every=False, branches=False, older_than_days=None)
    outcome = clean_runs(place, run_id=None, every=True, branches=True, older_than_days=None)
    assert outcome.cleaned == [done.run_id]
    assert outcome.branches_deleted == [done.branch]
    assert "resume or abort it first" in outcome.skipped[parked]
    assert not home.build_dir(done.run_id).exists()
    assert home.worktree(parked).is_dir()
    assert not engine_world.git(engine_world.repo, "branch", "--list", done.branch)
