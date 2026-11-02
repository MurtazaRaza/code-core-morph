"""Versioned schema migrations of the state database (``.codecoremorph/codecoremorph.db``).

Migrations are append-only: never edit one that has shipped; add the next version instead.
Each migration runs inside one transaction together with its ``schema_version`` row.
Statements are listed one by one, because ``executescript`` would commit mid-migration.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Migration:
    version: int
    description: str
    statements: tuple[str, ...]


SCHEMA_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    description TEXT NOT NULL,
    applied_at  TEXT NOT NULL
) STRICT
"""

_V1 = Migration(
    version=1,
    description="initial schema: runs, units, attempts, llm_calls, approvals, events",
    statements=(
        """
        CREATE TABLE runs (
            id                    TEXT PRIMARY KEY,
            status                TEXT NOT NULL,
            created_at            TEXT NOT NULL,
            updated_at            TEXT NOT NULL,
            repo_root             TEXT NOT NULL,
            base_commit           TEXT,
            config_hash           TEXT NOT NULL,
            codecoremorph_version TEXT NOT NULL,
            environment           TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(environment))
        ) STRICT
        """,
        # `data` holds the full WorkUnit JSON; the other columns are copies for querying.
        """
        CREATE TABLE units (
            run_id     TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            id         TEXT NOT NULL,
            seq        INTEGER NOT NULL,
            status     TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            data       TEXT NOT NULL CHECK (json_valid(data)),
            PRIMARY KEY (run_id, id)
        ) STRICT
        """,
        # `data` holds the full Attempt JSON (diagnostics, test results, invariant results).
        """
        CREATE TABLE attempts (
            run_id      TEXT NOT NULL,
            unit_id     TEXT NOT NULL,
            number      INTEGER NOT NULL,
            role        TEXT NOT NULL,
            model       TEXT,
            outcome     TEXT,
            started_at  TEXT NOT NULL,
            finished_at TEXT,
            data        TEXT NOT NULL CHECK (json_valid(data)),
            PRIMARY KEY (run_id, unit_id, number),
            FOREIGN KEY (run_id, unit_id) REFERENCES units(run_id, id) ON DELETE CASCADE
        ) STRICT
        """,
        """
        CREATE TABLE llm_calls (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id             TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            unit_id            TEXT,
            attempt            INTEGER,
            role               TEXT NOT NULL,
            provider           TEXT NOT NULL,
            model              TEXT NOT NULL,
            input_tokens       INTEGER NOT NULL DEFAULT 0,
            output_tokens      INTEGER NOT NULL DEFAULT 0,
            cache_read_tokens  INTEGER NOT NULL DEFAULT 0,
            cache_write_tokens INTEGER NOT NULL DEFAULT 0,
            cost_usd           REAL NOT NULL DEFAULT 0,
            latency_s          REAL NOT NULL,
            stop_reason        TEXT,
            created_at         TEXT NOT NULL
        ) STRICT
        """,
        "CREATE INDEX llm_calls_by_unit ON llm_calls (run_id, unit_id)",
        # The only table the CLI and dashboard write (SPEC §7.12). An abort request is a row
        # with gate 'run' and decision 'abort'. The engine sets consumed_at when it acts on one.
        """
        CREATE TABLE approvals (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id      TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            gate        TEXT NOT NULL CHECK (gate IN ('plan', 'unit', 'run')),
            unit_id     TEXT,
            decision    TEXT NOT NULL CHECK (decision IN ('approve', 'reject', 'abort')),
            reason      TEXT,
            data        TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(data)),
            source      TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            consumed_at TEXT
        ) STRICT
        """,
        "CREATE INDEX approvals_pending ON approvals (run_id, consumed_at)",
        # AUTOINCREMENT keeps event IDs strictly increasing, so SSE clients can resume by ID.
        """
        CREATE TABLE events (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id  TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            ts      TEXT NOT NULL,
            type    TEXT NOT NULL,
            unit_id TEXT,
            attempt INTEGER,
            data    TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(data))
        ) STRICT
        """,
        "CREATE INDEX events_by_run ON events (run_id, id)",
    ),
)

MIGRATIONS: tuple[Migration, ...] = (_V1,)
LATEST_VERSION = MIGRATIONS[-1].version
