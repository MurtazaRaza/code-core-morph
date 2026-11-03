"""SQLite state database (SPEC §7.12).

The engine is the only writer of run data. The CLI and the dashboard read run data and write
only ``approvals`` rows. WAL mode lets them read while the engine writes.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Self, cast

from pydantic import JsonValue

from codecoremorph.core.errors import StoreError
from codecoremorph.core.ids import validate_run_id
from codecoremorph.core.models import (
    ApprovalDecision,
    ApprovalGate,
    ApprovalRecord,
    Attempt,
    Event,
    EventType,
    LlmCallRecord,
    Role,
    RunRecord,
    RunStatus,
    WorkUnit,
)
from codecoremorph.store.migrations import LATEST_VERSION, MIGRATIONS, SCHEMA_VERSION_TABLE

log = logging.getLogger(__name__)

_BUSY_TIMEOUT_S = 10.0


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timestamps stored in the database must be timezone-aware")
    return value.isoformat()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class Store:
    """A connection to the state database. Not thread-safe: open one per thread."""

    def __init__(self, connection: sqlite3.Connection, path: Path):
        self._conn = connection
        self.path = path

    @classmethod
    def open(cls, path: Path, *, busy_timeout_s: float = _BUSY_TIMEOUT_S) -> Store:
        """Open (creating if needed) the database at ``path`` and bring its schema up to date."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # isolation_level=None: no implicit transactions; transaction() opens them explicitly.
            conn = sqlite3.connect(path, timeout=busy_timeout_s, isolation_level=None)
        except (OSError, sqlite3.Error) as exc:
            raise StoreError(
                f"can't open the state database {path}",
                reason=str(exc),
                hint="Check that the directory exists and is writable.",
            ) from exc
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            mode = str(conn.execute("PRAGMA journal_mode = WAL").fetchone()[0])
            if mode.lower() != "wal":
                raise StoreError(
                    f"the state database {path} can't use write-ahead logging",
                    reason=f"SQLite kept journal mode {mode!r}; this happens on some network "
                    "file systems",
                    hint="Keep the repository (or the state directory) on a local disk.",
                )
            conn.execute("PRAGMA synchronous = NORMAL")
            store = cls(conn, path)
            store.migrate()
        except sqlite3.DatabaseError as exc:
            conn.close()
            raise StoreError(
                f"{path} is not a usable CodeCoreMorph database",
                reason=str(exc),
                hint="Move the file aside; CodeCoreMorph creates a new one on the next run.",
            ) from exc
        except BaseException:
            conn.close()
            raise
        return store

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection]:
        """A write transaction. ``BEGIN IMMEDIATE`` takes the write lock up front."""
        if self._conn.in_transaction:
            raise StoreError("nested store transactions are not supported")
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield self._conn
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        self._conn.execute("COMMIT")

    # ------------------------------------------------------------------ schema

    @property
    def schema_version(self) -> int:
        row = self._conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        return int(row[0] or 0)

    def migrate(self) -> int:
        """Apply pending migrations. Returns the schema version afterwards."""
        with self.transaction() as conn:
            conn.execute(SCHEMA_VERSION_TABLE)
            current = self.schema_version
            if current > LATEST_VERSION:
                raise StoreError(
                    f"{self.path} was written by a newer CodeCoreMorph",
                    reason=f"its schema is version {current}; this version knows up to "
                    f"{LATEST_VERSION}",
                    hint="Upgrade CodeCoreMorph to work with this repository's run history.",
                )
            now = datetime.now(UTC).isoformat()
            for migration in MIGRATIONS[current:]:
                for statement in migration.statements:
                    conn.execute(statement)
                conn.execute(
                    "INSERT INTO schema_version (version, description, applied_at) "
                    "VALUES (?, ?, ?)",
                    (migration.version, migration.description, now),
                )
                log.debug(
                    "applied store migration %d (%s)", migration.version, migration.description
                )
        return LATEST_VERSION

    # ------------------------------------------------------------------ runs

    def create_run(self, run: RunRecord) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO runs (id, status, created_at, updated_at, repo_root, base_commit, "
                "config_hash, codecoremorph_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run.id,
                    run.status.value,
                    _iso(run.created_at),
                    _iso(run.updated_at),
                    str(run.repo_root),
                    run.base_commit,
                    run.config_hash,
                    run.codecoremorph_version,
                ),
            )

    def get_run(self, run_id: str) -> RunRecord | None:
        row = self._conn.execute(
            "SELECT id, status, created_at, updated_at, repo_root, base_commit, config_hash, "
            "codecoremorph_version FROM runs WHERE id = ?",
            (validate_run_id(run_id),),
        ).fetchone()
        if row is None:
            return None
        return RunRecord(
            id=row[0],
            status=RunStatus(row[1]),
            created_at=datetime.fromisoformat(row[2]),
            updated_at=datetime.fromisoformat(row[3]),
            repo_root=Path(row[4]),
            base_commit=row[5],
            config_hash=row[6],
            codecoremorph_version=row[7],
        )

    def set_run_status(self, run_id: str, status: RunStatus, at: datetime) -> None:
        with self.transaction() as conn:
            cursor = conn.execute(
                "UPDATE runs SET status = ?, updated_at = ? WHERE id = ?",
                (status.value, _iso(at), validate_run_id(run_id)),
            )
            if cursor.rowcount != 1:
                raise StoreError(f"run {run_id} is not in the state database")

    def list_runs(self) -> list[RunRecord]:
        """Every run, newest first (run IDs sort by time)."""
        rows = self._conn.execute("SELECT id FROM runs ORDER BY id DESC").fetchall()
        return [run for (run_id,) in rows if (run := self.get_run(str(run_id))) is not None]

    def set_run_environment(self, run_id: str, environment: Mapping[str, JsonValue]) -> None:
        """What the run ran with (NFR-6.1): versions, image digests, models, prompts."""
        with self.transaction() as conn:
            conn.execute(
                "UPDATE runs SET environment = ? WHERE id = ?",
                (_json(dict(environment)), validate_run_id(run_id)),
            )

    def run_environment(self, run_id: str) -> dict[str, JsonValue]:
        row = self._conn.execute(
            "SELECT environment FROM runs WHERE id = ?", (validate_run_id(run_id),)
        ).fetchone()
        return {} if row is None else cast(dict[str, JsonValue], json.loads(row[0]))

    # ------------------------------------------------------------------ units and attempts

    def put_units(self, run_id: str, units: Sequence[WorkUnit], at: datetime) -> None:
        """Insert or replace units (the plan's, then each status change)."""
        with self.transaction() as conn:
            conn.executemany(
                "INSERT INTO units (run_id, id, seq, status, updated_at, data) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (run_id, id) DO UPDATE SET "
                "seq = excluded.seq, status = excluded.status, "
                "updated_at = excluded.updated_at, data = excluded.data",
                [
                    (
                        validate_run_id(run_id),
                        u.id,
                        u.seq,
                        u.status.value,
                        _iso(at),
                        u.model_dump_json(),
                    )
                    for u in units
                ],
            )

    def units(self, run_id: str) -> list[WorkUnit]:
        """A run's units in plan order."""
        rows = self._conn.execute(
            "SELECT data FROM units WHERE run_id = ? ORDER BY seq", (validate_run_id(run_id),)
        )
        return [WorkUnit.model_validate_json(row[0]) for row in rows]

    def put_attempt(self, attempt: Attempt) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO attempts (run_id, unit_id, number, role, model, outcome, started_at, "
                "finished_at, data) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (run_id, unit_id, number) DO UPDATE SET role = excluded.role, "
                "model = excluded.model, outcome = excluded.outcome, "
                "finished_at = excluded.finished_at, data = excluded.data",
                (
                    attempt.run_id,
                    attempt.unit_id,
                    attempt.number,
                    attempt.role.value,
                    attempt.model,
                    attempt.outcome.value if attempt.outcome else None,
                    _iso(attempt.started_at),
                    _iso(attempt.finished_at) if attempt.finished_at else None,
                    attempt.model_dump_json(),
                ),
            )

    def attempts(self, run_id: str, unit_id: str | None = None) -> list[Attempt]:
        sql = "SELECT data FROM attempts WHERE run_id = ?"
        params: tuple[str, ...] = (validate_run_id(run_id),)
        if unit_id is not None:
            sql += " AND unit_id = ?"
            params = (*params, unit_id)
        rows = self._conn.execute(sql + " ORDER BY unit_id, number", params)
        return [Attempt.model_validate_json(row[0]) for row in rows]

    def delete_attempts(self, run_id: str, unit_id: str) -> None:
        """Forget a unit's attempts: it starts over (a resume after a crash mid-unit)."""
        with self.transaction() as conn:
            conn.execute(
                "DELETE FROM attempts WHERE run_id = ? AND unit_id = ?",
                (validate_run_id(run_id), unit_id),
            )

    # ------------------------------------------------------------------ approvals

    def add_approval(self, approval: ApprovalRecord) -> ApprovalRecord:
        """Write a decision (the CLI's and the dashboard's only write, SPEC §7.12)."""
        if approval.id is not None:
            raise ValueError("approval already has an id; approvals are written once")
        if self.get_run(approval.run_id) is None:
            raise StoreError(
                f"run {approval.run_id} is not in this repository's state database",
                hint="`codecoremorph status` lists the runs.",
            )
        data = {"exclude": list(approval.exclude)}
        with self.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO approvals (run_id, gate, unit_id, decision, reason, data, source, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    approval.run_id,
                    approval.gate.value,
                    approval.unit_id,
                    approval.decision.value,
                    approval.reason,
                    _json(data),
                    approval.source,
                    _iso(approval.created_at),
                ),
            )
        return approval.model_copy(update={"id": cursor.lastrowid})

    def approvals(self, run_id: str, *, pending: bool = False) -> list[ApprovalRecord]:
        """A run's decisions in the order they were written; ``pending`` ones the engine
        hasn't acted on yet."""
        sql = (
            "SELECT id, run_id, gate, unit_id, decision, reason, data, source, created_at, "
            "consumed_at FROM approvals WHERE run_id = ?"
        )
        if pending:
            sql += " AND consumed_at IS NULL"
        rows = self._conn.execute(sql + " ORDER BY id", (validate_run_id(run_id),))
        return [
            ApprovalRecord(
                id=row[0],
                run_id=row[1],
                gate=ApprovalGate(row[2]),
                unit_id=row[3],
                decision=ApprovalDecision(row[4]),
                reason=row[5],
                exclude=cast(list[str], json.loads(row[6]).get("exclude", [])),
                source=row[7],
                created_at=datetime.fromisoformat(row[8]),
                consumed_at=datetime.fromisoformat(row[9]) if row[9] else None,
            )
            for row in rows
        ]

    def consume_approval(self, approval_id: int, at: datetime) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE approvals SET consumed_at = ? WHERE id = ? AND consumed_at IS NULL",
                (_iso(at), approval_id),
            )

    # ------------------------------------------------------------------ model calls

    def add_llm_call(self, call: LlmCallRecord) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO llm_calls (run_id, unit_id, attempt, role, provider, model, "
                "input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, cost_usd, "
                "latency_s, stop_reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "?, ?)",
                (
                    call.run_id,
                    call.unit_id,
                    call.attempt,
                    call.role.value,
                    call.provider,
                    call.model,
                    call.input_tokens,
                    call.output_tokens,
                    call.cache_read_tokens,
                    call.cache_write_tokens,
                    call.cost_usd,
                    call.latency_s,
                    call.stop_reason,
                    _iso(call.created_at),
                ),
            )

    def llm_calls(self, run_id: str) -> list[LlmCallRecord]:
        rows = self._conn.execute(
            "SELECT run_id, unit_id, attempt, role, provider, model, input_tokens, "
            "output_tokens, cache_read_tokens, cache_write_tokens, cost_usd, latency_s, "
            "stop_reason, created_at FROM llm_calls WHERE run_id = ? ORDER BY id",
            (validate_run_id(run_id),),
        )
        return [
            LlmCallRecord(
                run_id=row[0],
                unit_id=row[1],
                attempt=row[2],
                role=Role(row[3]),
                provider=row[4],
                model=row[5],
                input_tokens=row[6],
                output_tokens=row[7],
                cache_read_tokens=row[8],
                cache_write_tokens=row[9],
                cost_usd=row[10],
                latency_s=row[11],
                stop_reason=row[12],
                created_at=datetime.fromisoformat(row[13]),
            )
            for row in rows
        ]

    # ------------------------------------------------------------------ events

    def append_event(self, event: Event) -> Event:
        """Write ``event`` and return it with its assigned ``id``."""
        if event.id is not None:
            raise ValueError("event already has an id; events are written once")
        with self.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO events (run_id, ts, type, unit_id, attempt, data) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    event.run_id,
                    _iso(event.ts),
                    event.type.value,
                    event.unit_id,
                    event.attempt,
                    json.dumps(event.data, ensure_ascii=False, separators=(",", ":")),
                ),
            )
        return event.model_copy(update={"id": cursor.lastrowid})

    def list_events(
        self, run_id: str, *, after_id: int = 0, limit: int | None = None
    ) -> list[Event]:
        """Events of a run in write order, starting after ``after_id`` (an SSE resume cursor)."""
        sql = (
            "SELECT id, run_id, ts, type, unit_id, attempt, data FROM events "
            "WHERE run_id = ? AND id > ? ORDER BY id"
        )
        params: Sequence[object] = (validate_run_id(run_id), after_id)
        if limit is not None:
            sql += " LIMIT ?"
            params = (*params, limit)
        return [
            Event(
                id=row[0],
                run_id=row[1],
                ts=datetime.fromisoformat(row[2]),
                type=EventType(row[3]),
                unit_id=row[4],
                attempt=row[5],
                data=cast(dict[str, JsonValue], json.loads(row[6])),
            )
            for row in self._conn.execute(sql, params)
        ]
