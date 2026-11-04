from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from codecoremorph.core.errors import StoreError
from codecoremorph.core.models import Event, EventType, RunRecord, RunStatus
from codecoremorph.store import (
    LATEST_VERSION,
    MIGRATIONS,
    EventWriter,
    StateDir,
    Store,
)

RUN = "20261102-141500-a3f9"
T0 = datetime(2026, 11, 2, 14, 15, tzinfo=UTC)
TABLES = {"runs", "units", "attempts", "llm_calls", "approvals", "events", "schema_version"}


def _run(run_id: str = RUN) -> RunRecord:
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
def db_path(tmp_path: Path) -> Path:
    return StateDir.for_repo(tmp_path).db_path


@pytest.fixture
def store(db_path: Path) -> Store:
    s = Store.open(db_path)
    s.create_run(_run())
    return s


# --------------------------------------------------------------------------- schema and migrations


def test_migrations_are_consecutive() -> None:
    assert [m.version for m in MIGRATIONS] == list(range(1, len(MIGRATIONS) + 1))
    assert MIGRATIONS[-1].version == LATEST_VERSION


def test_open_creates_schema_from_empty(db_path: Path) -> None:
    assert not db_path.exists()
    with Store.open(db_path) as s:
        assert s.schema_version == LATEST_VERSION
    conn = sqlite3.connect(db_path)
    try:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert names >= TABLES
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        rows = conn.execute("SELECT version FROM schema_version").fetchall()
        assert rows == [(v,) for v in range(1, LATEST_VERSION + 1)]
    finally:
        conn.close()


def test_reopening_does_not_reapply_migrations(db_path: Path) -> None:
    Store.open(db_path).close()
    with Store.open(db_path) as s:
        assert s.schema_version == LATEST_VERSION
        count = s._conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]  # pyright: ignore[reportPrivateUsage]
        assert count == LATEST_VERSION


def test_newer_schema_is_refused(db_path: Path) -> None:
    Store.open(db_path).close()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO schema_version VALUES (?, 'from the future', '2030-01-01T00:00:00+00:00')",
        (LATEST_VERSION + 1,),
    )
    conn.commit()
    conn.close()
    with pytest.raises(StoreError, match="newer CodeCoreMorph") as info:
        Store.open(db_path)
    assert info.value.hint is not None


def test_garbage_file_is_refused(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True)
    db_path.write_bytes(b"this is not a sqlite database at all" * 100)
    with pytest.raises(StoreError, match="not a usable"):
        Store.open(db_path)


def test_foreign_keys_are_enforced(store: Store) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        store.append_event(Event.now("20991231-000000-0000", EventType.LOG))


def test_transactions_roll_back_and_do_not_nest(store: Store) -> None:
    def insert_then_fail() -> None:
        with store.transaction() as conn:
            conn.execute(
                "INSERT INTO events (run_id, ts, type) VALUES (?, ?, ?)",
                (RUN, T0.isoformat(), "log"),
            )
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        insert_then_fail()
    assert store.list_events(RUN) == []
    with store.transaction(), pytest.raises(StoreError, match="nested"), store.transaction():
        pass


# --------------------------------------------------------------------------- runs and events


def test_run_round_trip_and_status(store: Store) -> None:
    assert store.get_run(RUN) == _run()
    assert store.get_run("20991231-000000-0000") is None
    later = T0 + timedelta(minutes=5)
    store.set_run_status(RUN, RunStatus.PLANNING, later)
    run = store.get_run(RUN)
    assert run is not None
    assert (run.status, run.updated_at) == (RunStatus.PLANNING, later)
    with pytest.raises(StoreError):
        store.set_run_status("20991231-000000-0000", RunStatus.FAILED, later)


def test_event_round_trip(store: Store) -> None:
    original = Event(
        run_id=RUN,
        ts=T0 + timedelta(microseconds=123456),
        type=EventType.ATTEMPT_FINISHED,
        unit_id="U-0007",
        attempt=2,
        data={"outcome": "build_failed", "errors": 3, "files": ["a.cpp"], "note": "naïve ✓"},
    )
    stored = store.append_event(original)
    assert stored.id is not None
    assert stored.model_copy(update={"id": None}) == original
    assert store.list_events(RUN) == [stored]
    with pytest.raises(ValueError, match="already has an id"):
        store.append_event(stored)


def test_event_cursor_and_limit(store: Store) -> None:
    writer = EventWriter(store, RUN, clock=lambda: T0)
    events = [writer.emit(EventType.LOG, message=f"m{i}") for i in range(5)]
    ids = [e.id for e in events if e.id is not None]
    assert len(ids) == 5
    assert ids == sorted(ids)
    assert [e.data["message"] for e in store.list_events(RUN, after_id=ids[1])] == [
        "m2",
        "m3",
        "m4",
    ]
    assert len(store.list_events(RUN, limit=2)) == 2


def test_events_are_stored_redacted(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    """Security review (Phase 8): the dashboard streams events as stored."""
    monkeypatch.setenv("CODECOREMORPH_TEST_API_KEY", "literal-key-value-1234")
    writer = EventWriter(store, RUN, clock=lambda: T0)
    writer.emit(
        EventType.LOG,
        message="cmake: using literal-key-value-1234",
        details={"lines": ["password=hunter2hunter2", 7]},
    )
    (event,) = store.list_events(RUN)
    assert event.data == {
        "message": "cmake: using [REDACTED]",
        "details": {"lines": ["password=[REDACTED]", 7]},
    }


def test_recent_events_and_the_last_id(store: Store) -> None:
    writer = EventWriter(store, RUN, clock=lambda: T0)
    assert store.last_event_id(RUN) == 0
    assert store.recent_events(RUN, limit=3) == []
    for i in range(5):
        writer.emit(EventType.LOG, message=f"m{i}")
        writer.emit(EventType.STEP_STARTED, message=f"s{i}")
    recent = store.recent_events(RUN, limit=3)
    assert [e.data["message"] for e in recent] == ["s3", "m4", "s4"]  # oldest first
    steps = store.recent_events(RUN, limit=2, types=[EventType.STEP_STARTED])
    assert [e.data["message"] for e in steps] == ["s3", "s4"]
    assert store.last_event_id(RUN) == recent[-1].id


def test_a_read_only_store_reads_and_never_writes(store: Store, db_path: Path) -> None:
    EventWriter(store, RUN, clock=lambda: T0).emit(EventType.LOG, message="hello")
    with Store.open_readonly(db_path) as reader:
        assert reader.get_run(RUN) is not None
        assert [e.data["message"] for e in reader.list_events(RUN)] == ["hello"]
        with pytest.raises(sqlite3.OperationalError):
            reader.append_event(Event.now(RUN, EventType.LOG))
        # The engine keeps writing while the reader is open.
        EventWriter(store, RUN, clock=lambda: T0).emit(EventType.LOG, message="again")
        assert len(reader.list_events(RUN)) == 2


def test_a_read_only_store_needs_a_current_database(tmp_path: Path, db_path: Path) -> None:
    with pytest.raises(StoreError, match="no state database"):
        Store.open_readonly(tmp_path / "missing.db")
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"not a database at all" * 100)
    with pytest.raises(StoreError, match="not a usable"):
        Store.open_readonly(junk)
    with Store.open(db_path) as s, s.transaction() as conn:
        conn.execute("DELETE FROM schema_version WHERE version = ?", (LATEST_VERSION,))
    with pytest.raises(StoreError, match="schema version"):
        Store.open_readonly(db_path)


def test_wal_reader_sees_committed_state_while_writer_holds_a_transaction(
    store: Store, db_path: Path
) -> None:
    with Store.open(db_path) as reader:
        with store.transaction() as conn:
            conn.execute(
                "INSERT INTO events (run_id, ts, type) VALUES (?, ?, ?)",
                (RUN, T0.isoformat(), "log"),
            )
            # The write lock is held; a WAL reader still reads the last committed state.
            assert reader.list_events(RUN) == []
        assert len(reader.list_events(RUN)) == 1


def test_concurrent_reader_and_writer(store: Store, db_path: Path) -> None:
    total = 200
    errors: list[BaseException] = []
    seen_counts: list[int] = []
    done = threading.Event()

    def write() -> None:
        try:
            with Store.open(db_path) as s:
                writer = EventWriter(s, RUN)
                for i in range(total):
                    writer.emit(EventType.LOG, n=i)
        except Exception as exc:  # noqa: BLE001 - surface thread failures to the test
            errors.append(exc)
        finally:
            done.set()

    def read() -> None:
        try:
            with Store.open(db_path) as s:
                while not done.is_set():
                    seen_counts.append(len(s.list_events(RUN)))
                seen_counts.append(len(s.list_events(RUN)))
        except Exception as exc:  # noqa: BLE001 - surface thread failures to the test
            errors.append(exc)

    threads = [threading.Thread(target=write), threading.Thread(target=read)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert errors == []
    assert seen_counts == sorted(seen_counts)  # a reader never sees events disappear
    assert seen_counts[-1] == total


# --------------------------------------------------------------------------- artifact layout


def test_artifact_layout(tmp_path: Path) -> None:
    state = StateDir.for_repo(tmp_path)
    assert state.root == tmp_path / ".codecoremorph"
    assert state.lock_path == state.root / "lock"
    run = state.run(RUN).ensure()
    assert run.root.is_dir()
    assert run.plan_json == state.runs_dir / RUN / "plan.json"
    assert run.checkpoints_db.name == "checkpoints.sqlite"
    attempt = run.attempt("U-0042", 3).ensure()
    assert attempt.root == run.root / "attempts" / "U-0042" / "3"
    assert attempt.root.is_dir()
    assert {p.name for p in (attempt.diff_patch, attempt.build_log, attempt.transcript_jsonl)} == {
        "diff.patch",
        "build.log",
        "transcript.jsonl",
    }


@pytest.mark.parametrize("bad_unit", ["../U-0001", "U-1", "U-0001/x"])
def test_artifact_ids_cannot_escape(tmp_path: Path, bad_unit: str) -> None:
    run = StateDir.for_repo(tmp_path).run(RUN)
    with pytest.raises(ValueError, match="invalid unit ID"):
        run.attempt(bad_unit, 1)
    with pytest.raises(ValueError, match="invalid run ID"):
        StateDir.for_repo(tmp_path).run("../../x")
    with pytest.raises(ValueError, match="start at 1"):
        run.attempt("U-0001", 0)
