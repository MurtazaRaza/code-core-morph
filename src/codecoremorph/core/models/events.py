"""Structured engine events (FR-13.3). The dashboard reads them over SSE."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import AwareDatetime, Field, JsonValue

from codecoremorph.core.models.base import CoreModel, RunId, UnitId


class EventType(StrEnum):
    RUN_CREATED = "run.created"
    RUN_STATUS = "run.status"
    STEP_STARTED = "step.started"
    STEP_FINISHED = "step.finished"
    UNIT_STATUS = "unit.status"
    ATTEMPT_STARTED = "attempt.started"
    ATTEMPT_FINISHED = "attempt.finished"
    GATE_WAITING = "gate.waiting"
    GATE_DECIDED = "gate.decided"
    LLM_CALL = "llm.call"
    LOG = "log"


class Event(CoreModel):
    id: int | None = Field(default=None, description="Assigned by the store; None before writing")
    run_id: RunId
    ts: AwareDatetime
    type: EventType
    unit_id: UnitId | None = None
    attempt: int | None = Field(default=None, ge=1)
    data: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])

    @classmethod
    def now(
        cls,
        run_id: str,
        type: EventType,  # noqa: A002 - mirrors the field name
        *,
        unit_id: str | None = None,
        attempt: int | None = None,
        data: dict[str, JsonValue] | None = None,
    ) -> Event:
        return cls(
            run_id=run_id,
            ts=datetime.now(UTC),
            type=type,
            unit_id=unit_id,
            attempt=attempt,
            data=data or {},
        )
