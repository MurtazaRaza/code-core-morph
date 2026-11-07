# SQL statements interpolate only the module's own table and column names, never input.
# ruff: noqa: S608
"""The symbol index in SQLite (SPEC §7.2).

Adapters write the rows in bulk, as plain tuples, so indexing a large project doesn't build a
pydantic object per reference (NFR-1.1, NFR-1.2). Queries return the core models.

The index is a cache: a file with another schema version is rebuilt, not migrated. Rows belong
to the file they are located in (``symbols.file``, ``refs.from_file``, ``deps.from_file``), so
re-indexing a set of files replaces exactly their rows (FR-2.4).
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable, Sequence
from pathlib import Path
from types import TracebackType
from typing import Self

from codecoremorph.core.errors import StoreError
from codecoremorph.core.models import (
    Boundary,
    BuildTarget,
    DependencyKind,
    FileDependency,
    FileEntry,
    FileRole,
    IndexMeta,
    LineEnding,
    Reference,
    ReferenceKind,
    SourceRange,
    Symbol,
    SymbolKind,
    TargetKind,
    TestEntry,
    Visibility,
)

INDEX_SCHEMA_VERSION = "1"
INDEX_FILE_NAME = "index.sqlite"

FILE_COLUMNS = (
    "path", "language", "sha256", "role", "c_compatible", "public_api", "encoding", "line_ending",
)  # fmt: skip
SYMBOL_COLUMNS = (
    "id", "name", "qualified_name", "kind", "file", "start_line", "start_column", "end_line",
    "end_column", "signature", "visibility", "is_definition", "boundary",
)  # fmt: skip
REFERENCE_COLUMNS = ("from_file", "from_symbol", "to_symbol", "kind", "line", "col")
DEPENDENCY_COLUMNS = ("from_file", "to_file", "kind")

_VA_LIST = re.compile(r"\bva_list\b|\b__builtin_va_list\b|\b__va_list_tag\b")


def takes_variable_arguments(symbol: Symbol) -> bool:
    """A function that takes ``...`` or a ``va_list`` (by its signature, whose arguments end
    with ``...`` for a variadic one). Stable Rust can define neither, so a C port leaves such a
    function in C, over the Rust code, until the retirement (D-70)."""
    if symbol.kind is not SymbolKind.FUNCTION or not symbol.signature:
        return False
    return symbol.signature.rstrip().endswith("...)") or bool(_VA_LIST.search(symbol.signature))


FileRow = tuple[str, str, str, str, int, int, str, str]
SymbolRow = tuple[str, str, str, str, str, int, int, int, int, str | None, str, int, str]
ReferenceRow = tuple[str, str | None, str, str, int, int]
DependencyRow = tuple[str, str, str]

_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) STRICT;
CREATE TABLE files (
    path TEXT PRIMARY KEY, language TEXT NOT NULL, sha256 TEXT NOT NULL, role TEXT NOT NULL,
    c_compatible INTEGER NOT NULL, public_api INTEGER NOT NULL, encoding TEXT NOT NULL,
    line_ending TEXT NOT NULL
) STRICT;
CREATE TABLE symbols (
    id TEXT NOT NULL, name TEXT NOT NULL, qualified_name TEXT NOT NULL, kind TEXT NOT NULL,
    file TEXT NOT NULL, start_line INTEGER NOT NULL, start_column INTEGER NOT NULL,
    end_line INTEGER NOT NULL, end_column INTEGER NOT NULL, signature TEXT,
    visibility TEXT NOT NULL, is_definition INTEGER NOT NULL, boundary TEXT NOT NULL,
    PRIMARY KEY (id, file, start_line, start_column)
) STRICT;
CREATE INDEX symbols_by_file ON symbols (file);
CREATE INDEX symbols_by_name ON symbols (name);
CREATE TABLE refs (
    from_file TEXT NOT NULL, from_symbol TEXT, to_symbol TEXT NOT NULL, kind TEXT NOT NULL,
    line INTEGER NOT NULL, col INTEGER NOT NULL,
    PRIMARY KEY (from_file, line, col, to_symbol, kind)
) STRICT;
CREATE INDEX refs_by_target ON refs (to_symbol);
CREATE TABLE deps (
    from_file TEXT NOT NULL, to_file TEXT NOT NULL, kind TEXT NOT NULL,
    PRIMARY KEY (from_file, to_file, kind)
) STRICT;
CREATE INDEX deps_by_target ON deps (to_file);
"""
_TABLES = ("meta", "files", "symbols", "refs", "deps")


def _placeholders(columns: Sequence[str]) -> str:
    return ", ".join("?" for _ in columns)


class IndexStore:
    """An open index database. Not thread-safe."""

    def __init__(self, connection: sqlite3.Connection, path: Path):
        self._conn = connection
        self.path = path

    @classmethod
    def open(cls, path: Path, *, reset: bool = False) -> IndexStore:
        """Open (creating if needed) the index at ``path``. ``reset`` starts from empty."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(path, isolation_level=None)
            # The index can always be rebuilt, so durability is traded for speed.
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = OFF")
            store = cls(conn, path)
            if reset or store._schema_version() != INDEX_SCHEMA_VERSION:
                store._recreate()
        except (OSError, sqlite3.Error) as exc:
            raise StoreError(
                f"can't open the symbol index {path}",
                reason=str(exc),
                hint="The index is a cache: delete the file and index again.",
            ) from exc
        return store

    def _schema_version(self) -> str | None:
        try:
            row = self._conn.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()
        except sqlite3.OperationalError:
            return None
        return None if row is None else str(row[0])

    def _recreate(self) -> None:
        with self._transaction():
            for table in _TABLES:
                self._conn.execute(f"DROP TABLE IF EXISTS {table}")
            # executescript() would commit the open transaction, so run statements one by one.
            for statement in _SCHEMA.split(";"):
                if statement.strip():
                    self._conn.execute(statement)
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES ('schema', ?)", (INDEX_SCHEMA_VERSION,)
            )

    def _transaction(self) -> _Transaction:
        return _Transaction(self._conn)

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

    # ------------------------------------------------------------------ writing

    def remove_files(self, paths: Iterable[str]) -> None:
        """Delete every row located in ``paths`` (before re-indexing them)."""
        rows = [(p,) for p in paths]
        with self._transaction():
            self._conn.executemany("DELETE FROM files WHERE path = ?", rows)
            self._conn.executemany("DELETE FROM symbols WHERE file = ?", rows)
            self._conn.executemany("DELETE FROM refs WHERE from_file = ?", rows)
            self._conn.executemany("DELETE FROM deps WHERE from_file = ?", rows)

    def write(
        self,
        *,
        files: Iterable[FileRow] = (),
        symbols: Iterable[SymbolRow] = (),
        references: Iterable[ReferenceRow] = (),
        dependencies: Iterable[DependencyRow] = (),
    ) -> None:
        """Insert rows in one transaction. Duplicates (the same header seen from several
        translation units) are ignored; file rows replace older ones."""
        with self._transaction():
            self._conn.executemany(
                f"INSERT OR REPLACE INTO files VALUES ({_placeholders(FILE_COLUMNS)})", files
            )
            self._conn.executemany(
                f"INSERT OR IGNORE INTO symbols VALUES ({_placeholders(SYMBOL_COLUMNS)})", symbols
            )
            self._conn.executemany(
                f"INSERT OR IGNORE INTO refs VALUES ({_placeholders(REFERENCE_COLUMNS)})",
                references,
            )
            self._conn.executemany(
                f"INSERT OR IGNORE INTO deps VALUES ({_placeholders(DEPENDENCY_COLUMNS)})",
                dependencies,
            )

    def set_meta(self, meta: IndexMeta) -> None:
        with self._transaction():
            self._conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('index', ?)",
                (meta.model_dump_json(),),
            )

    def set_value(self, key: str, value: str) -> None:
        if key in ("schema", "index"):
            raise ValueError(f"{key!r} is reserved")
        with self._transaction():
            self._conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value)
            )

    def set_build_targets(self, targets: Sequence[BuildTarget]) -> None:
        """The build's targets and their files (D-58), kept with the index they came with."""
        self.set_value("build_targets", "[" + ",".join(t.model_dump_json() for t in targets) + "]")

    def set_test_entries(self, entries: Sequence[TestEntry]) -> None:
        """The tests the build registers and what they run (D-60)."""
        self.set_value("test_entries", "[" + ",".join(e.model_dump_json() for e in entries) + "]")

    def clear_program_boundaries(self) -> None:
        """Take what a source file only programs compile defines off the C ABI boundary: no
        code links against a program, so its functions and globals are no ABI (D-71;
        bzip2recover.c's file-scope globals, M9). A declaration in a header stays on it."""
        targets = self.build_targets()
        libraries = {f for t in targets if t.kind is TargetKind.LIBRARY for f in t.files}
        programs = {f for t in targets if t.kind is TargetKind.EXECUTABLE for f in t.files}
        with self._transaction():
            self._conn.executemany(
                "UPDATE symbols SET boundary = ? WHERE file = ? AND boundary = ?",
                [
                    (Boundary.NONE.value, path, Boundary.C_ABI.value)
                    for path in sorted(programs - libraries)
                ],
            )

    # ------------------------------------------------------------------ reading

    def build_targets(self) -> list[BuildTarget]:
        """The targets the adapter recorded, or none when it records none."""
        text = self.value("build_targets")
        if not text:
            return []
        return [BuildTarget.model_validate(t) for t in json.loads(text)]

    def test_entries(self) -> list[TestEntry]:
        """The test entries the adapter recorded, or none."""
        text = self.value("test_entries")
        if not text:
            return []
        return [TestEntry.model_validate(e) for e in json.loads(text)]

    def meta(self) -> IndexMeta | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key = 'index'").fetchone()
        return None if row is None else IndexMeta.model_validate_json(str(row[0]))

    def value(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row[0])

    def counts(self) -> dict[str, int]:
        return {
            table: int(self._conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            for table in ("files", "symbols", "refs", "deps")
        }

    def files(self, role: FileRole | None = None) -> list[FileEntry]:
        sql = f"SELECT {', '.join(FILE_COLUMNS)} FROM files"
        params: tuple[str, ...] = ()
        if role is not None:
            sql += " WHERE role = ?"
            params = (role.value,)
        return [_file(row) for row in self._conn.execute(sql + " ORDER BY path", params)]

    def file(self, path: str) -> FileEntry | None:
        row = self._conn.execute(
            f"SELECT {', '.join(FILE_COLUMNS)} FROM files WHERE path = ?", (path,)
        ).fetchone()
        return None if row is None else _file(row)

    def symbols(
        self,
        *,
        file: str | None = None,
        kind: SymbolKind | None = None,
        name: str | None = None,
        symbol_id: str | None = None,
    ) -> list[Symbol]:
        clauses: list[str] = []
        params: list[str] = []
        for column, value in (("file", file), ("kind", kind), ("name", name), ("id", symbol_id)):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(str(value))
        sql = f"SELECT {', '.join(SYMBOL_COLUMNS)} FROM symbols"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY file, start_line, start_column"
        return [_symbol(row) for row in self._conn.execute(sql, params)]

    def references_to(self, symbol_id: str) -> list[Reference]:
        rows = self._conn.execute(
            f"SELECT {', '.join(REFERENCE_COLUMNS)} FROM refs WHERE to_symbol = ? "
            "ORDER BY from_file, line, col",
            (symbol_id,),
        )
        return [_reference(row) for row in rows]

    def references_from(self, file: str) -> list[Reference]:
        rows = self._conn.execute(
            f"SELECT {', '.join(REFERENCE_COLUMNS)} FROM refs WHERE from_file = ? "
            "ORDER BY line, col",
            (file,),
        )
        return [_reference(row) for row in rows]

    def dependencies(self) -> list[FileDependency]:
        rows = self._conn.execute(
            f"SELECT {', '.join(DEPENDENCY_COLUMNS)} FROM deps ORDER BY from_file, to_file"
        )
        return [
            FileDependency(from_file=str(a), to_file=str(b), kind=DependencyKind(str(k)))
            for a, b, k in rows
        ]

    def dependents_of(self, path: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT from_file FROM deps WHERE to_file = ? ORDER BY from_file", (path,)
        )
        return [str(row[0]) for row in rows]

    # ------------------------------------------------------------------ graph queries

    def reference_edges(self) -> list[tuple[str, str]]:
        """``(from_file, to_file)`` for every file that references a symbol defined in another
        file. Only definitions count: forward declarations would tie unrelated headers together.
        """
        return [(a, b) for a, b, _ in self.reference_counts()]

    def reference_counts(self) -> list[tuple[str, str, int]]:
        """:meth:`reference_edges` with, for each, how many distinct symbols ``from_file`` uses
        that ``to_file`` defines: what a C port calls through ``extern`` declarations while
        ``to_file`` is still C (D-68). A C file that defines the symbol itself uses its own:
        a C type has no linkage, so two files that each define ``struct sink`` define two
        types, which share one ID (D-70)."""
        rows = self._conn.execute(
            "SELECT r.from_file, s.file, COUNT(DISTINCT s.id) FROM refs r "
            "JOIN symbols s ON s.id = r.to_symbol AND s.is_definition = 1 "
            "WHERE s.file != r.from_file AND NOT ("
            "EXISTS (SELECT 1 FROM files f WHERE f.path = r.from_file AND f.language = 'c') AND "
            "EXISTS (SELECT 1 FROM symbols own WHERE own.id = s.id AND own.file = r.from_file "
            "AND own.is_definition = 1)) "
            "GROUP BY r.from_file, s.file ORDER BY r.from_file, s.file"
        )
        return [(str(a), str(b), _int(n)) for a, b, n in rows]

    def used_elsewhere(self, path: str) -> list[Symbol]:
        """The symbols ``path`` defines that another file declares or references: in C, what
        a translation unit exports (D-68)."""
        rows = self._conn.execute(
            f"SELECT {', '.join(f's.{c}' for c in SYMBOL_COLUMNS)} FROM symbols s "
            "WHERE s.file = ? AND s.is_definition = 1 AND ("
            "EXISTS (SELECT 1 FROM symbols d WHERE d.id = s.id AND d.file != s.file) OR "
            "EXISTS (SELECT 1 FROM refs r WHERE r.to_symbol = s.id AND r.from_file != s.file)) "
            "ORDER BY s.start_line, s.start_column, s.id",
            (path,),
        )
        found: dict[str, Symbol] = {}
        for row in rows:
            symbol = _symbol(row)
            found.setdefault(symbol.id, symbol)  # a symbol defined twice in one file is one
        return list(found.values())

    def files_declaring(self, symbol_ids: Iterable[str]) -> list[str]:
        """Files holding a declaration or definition of any of ``symbol_ids``."""
        return self._distinct_by_symbol("SELECT DISTINCT file FROM symbols WHERE id IN", symbol_ids)

    def files_referencing(self, symbol_ids: Iterable[str]) -> list[str]:
        """Files that reference any of ``symbol_ids`` (the fan-in, FR-5.3)."""
        return self._distinct_by_symbol(
            "SELECT DISTINCT from_file FROM refs WHERE to_symbol IN", symbol_ids
        )

    def _distinct_by_symbol(self, sql: str, symbol_ids: Iterable[str]) -> list[str]:
        ids = sorted(set(symbol_ids))
        found: set[str] = set()
        # SQLite caps bound parameters per statement, so query in chunks.
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            rows = self._conn.execute(f"{sql} ({_placeholders(chunk)})", chunk)
            found.update(str(row[0]) for row in rows)
        return sorted(found)

    def boundary_files(self) -> dict[str, set[Boundary]]:
        """Files declaring symbols on a language or ABI boundary (not public API, which is a
        property of the file)."""
        rows = self._conn.execute(
            "SELECT DISTINCT file, boundary FROM symbols WHERE boundary NOT IN (?, ?)",
            (Boundary.NONE.value, Boundary.PUBLIC_API.value),
        )
        found: dict[str, set[Boundary]] = {}
        for file, boundary in rows:
            found.setdefault(str(file), set()).add(Boundary(str(boundary)))
        return found


class _Transaction:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def __enter__(self) -> None:
        self._conn.execute("BEGIN")

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._conn.execute("ROLLBACK" if exc_type is not None else "COMMIT")


def _file(row: Sequence[object]) -> FileEntry:
    path, language, sha256, role, c_compat, public, encoding, line_ending = row
    return FileEntry(
        path=str(path),
        language=str(language),
        sha256=str(sha256),
        role=FileRole(str(role)),
        c_compatible=bool(c_compat),
        public_api=bool(public),
        encoding=str(encoding),
        line_ending=LineEnding(str(line_ending)),
    )


def _symbol(row: Sequence[object]) -> Symbol:
    (sid, name, qname, kind, file, sl, sc, el, ec, signature, visibility, definition, boundary) = (
        row
    )
    return Symbol(
        id=str(sid),
        name=str(name),
        qualified_name=str(qname),
        kind=SymbolKind(str(kind)),
        file=str(file),
        range=SourceRange(
            start_line=_int(sl), start_column=_int(sc), end_line=_int(el), end_column=_int(ec)
        ),
        signature=None if signature is None else str(signature),
        visibility=Visibility(str(visibility)),
        is_definition=bool(definition),
        boundary=Boundary(str(boundary)),
    )


def _reference(row: Sequence[object]) -> Reference:
    from_file, from_symbol, to_symbol, kind, line, col = row
    return Reference(
        from_file=str(from_file),
        from_symbol=None if from_symbol is None else str(from_symbol),
        to_symbol=str(to_symbol),
        kind=ReferenceKind(str(kind)),
        line=_int(line),
        column=_int(col),
    )


def _int(value: object) -> int:
    if not isinstance(value, int):
        raise StoreError(f"corrupt symbol index: expected an integer, got {value!r}")
    return value
