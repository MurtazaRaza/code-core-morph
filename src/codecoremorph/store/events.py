"""Event writer: the engine's structured log of state transitions (FR-13.3)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import JsonValue

from codecoremorph.core.ids import validate_run_id
from codecoremorph.core.models import Event, EventType
from codecoremorph.core.secrets import Redactor
from codecoremorph.store.db import Store

log = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class EventWriter:
    """Appends events for one run to the store, with credentials redacted from their data
    (the dashboard streams events as they are, NFR-5.3)."""

    def __init__(self, store: Store, run_id: str, clock: Callable[[], datetime] = _utc_now):
        self._store = store
        self.run_id = validate_run_id(run_id)
        self._clock = clock
        self._redactor = Redactor.from_environment()

    def emit(
        self,
        type: EventType,  # noqa: A002 - mirrors Event.type
        *,
        unit_id: str | None = None,
        attempt: int | None = None,
        **data: JsonValue,
    ) -> Event:
        redacted = {key: self._redactor.redact_json(value) for key, value in data.items()}
        event = Event(
            run_id=self.run_id,
            ts=self._clock(),
            type=type,
            unit_id=unit_id,
            attempt=attempt,
            data=redacted,
        )
        stored = self._store.append_event(event)
        log.debug("event %s %s", type.value, redacted)
        return stored
