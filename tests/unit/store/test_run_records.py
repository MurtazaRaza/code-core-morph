"""Run records in the state database: units, attempts, approvals and model calls (§7.12)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from codecoremorph.core.errors import StoreError
from codecoremorph.core.models import (
    ApprovalDecision,
    ApprovalGate,
    ApprovalRecord,
    Attempt,
    AttemptOutcome,
    InvariantResult,
    LlmCallRecord,
    Role,
    RunRecord,
    UnitStatus,
    WorkUnit,
)
from codecoremorph.store import StateDir, Store

RUN = "20261102-141500-a3f9"
OTHER = "20261103-090000-bb01"
T0 = datetime(2026, 11, 2, 14, 15, tzinfo=UTC)


def _run(run_id: str) -> RunRecord:
    return RunRecord(
        id=run_id,
        created_at=T0,
        updated_at=T0,
        repo_root=Path("/repo"),
        base_commit="0" * 40,
        config_hash="a" * 64,
        codecoremorph_version="0.1.0",
    )


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store.open(StateDir.for_repo(tmp_path).db_path)
    s.create_run(_run(RUN))
    return s


def _unit(n: int, status: UnitStatus = UnitStatus.PENDING) -> WorkUnit:
    return WorkUnit(id=f"U-{n:04d}", seq=n, rules=["r"], files=[f"src/f{n}.cpp"], status=status)


def test_runs_newest_first_and_environment(store: Store) -> None:
    store.create_run(_run(OTHER))
    assert [r.id for r in store.list_runs()] == [OTHER, RUN]
    assert store.run_environment(RUN) == {}
    store.set_run_environment(RUN, {"codecoremorph": "0.1.0", "models": ["host"]})
    assert store.run_environment(RUN) == {"codecoremorph": "0.1.0", "models": ["host"]}


def test_units_upsert_in_plan_order(store: Store) -> None:
    store.put_units(RUN, [_unit(2), _unit(1)], T0)
    assert [u.id for u in store.units(RUN)] == ["U-0001", "U-0002"]
    changed = _unit(2, UnitStatus.COMMITTED).model_copy(update={"note": "done"})
    store.put_units(RUN, [changed], T0 + timedelta(minutes=1))
    units = store.units(RUN)
    assert [u.status for u in units] == [UnitStatus.PENDING, UnitStatus.COMMITTED]
    assert units[1].note == "done"


def test_attempts_round_trip_and_reset(store: Store) -> None:
    store.put_units(RUN, [_unit(1)], T0)
    attempt = Attempt(
        run_id=RUN,
        unit_id="U-0001",
        number=1,
        role=Role.EDITOR,
        model="anthropic/claude-opus-5-5",
        started_at=T0,
        invariant_results=[InvariantResult(invariant="I-7", passed=False, message="NOLINT")],
    )
    store.put_attempt(attempt)
    finished = attempt.model_copy(
        update={"outcome": AttemptOutcome.INVARIANT_FAILED, "finished_at": T0}
    )
    store.put_attempt(finished)  # the same attempt, finished
    assert store.attempts(RUN) == [finished]
    assert store.attempts(RUN, "U-0002") == []
    store.delete_attempts(RUN, "U-0001")
    assert store.attempts(RUN) == []


def test_approvals_pending_and_consumed(store: Store) -> None:
    plan = store.add_approval(
        ApprovalRecord(
            run_id=RUN,
            gate=ApprovalGate.PLAN,
            decision=ApprovalDecision.APPROVE,
            exclude=["U-0003"],
            source="cli",
            created_at=T0,
        )
    )
    abort = store.add_approval(
        ApprovalRecord(
            run_id=RUN,
            gate=ApprovalGate.RUN,
            decision=ApprovalDecision.ABORT,
            reason="enough",
            source="cli",
            created_at=T0,
        )
    )
    assert plan.id is not None
    assert abort.id is not None
    pending = store.approvals(RUN, pending=True)
    assert [(a.gate, a.decision) for a in pending] == [
        (ApprovalGate.PLAN, ApprovalDecision.APPROVE),
        (ApprovalGate.RUN, ApprovalDecision.ABORT),
    ]
    assert pending[0].exclude == ["U-0003"]
    store.consume_approval(plan.id, T0 + timedelta(seconds=1))
    assert [a.id for a in store.approvals(RUN, pending=True)] == [abort.id]
    assert store.approvals(RUN)[0].consumed_at == T0 + timedelta(seconds=1)


def test_an_approval_for_an_unknown_run_is_refused(store: Store) -> None:
    with pytest.raises(StoreError, match="not in this repository"):
        store.add_approval(
            ApprovalRecord(
                run_id=OTHER,
                gate=ApprovalGate.PLAN,
                decision=ApprovalDecision.APPROVE,
                source="cli",
                created_at=T0,
            )
        )


def test_llm_calls_round_trip(store: Store) -> None:
    call = LlmCallRecord(
        run_id=RUN,
        unit_id="U-0001",
        attempt=1,
        role=Role.EDITOR,
        provider="anthropic",
        model="claude-opus-5-5",
        input_tokens=1000,
        output_tokens=200,
        cache_read_tokens=50,
        cost_usd=0.02,
        latency_s=1.5,
        stop_reason="tool_use",
        created_at=T0,
    )
    store.add_llm_call(call)
    (stored,) = store.llm_calls(RUN)
    assert stored == call
    assert stored.tokens == 1250
