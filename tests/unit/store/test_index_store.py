"""The SQLite symbol index (SPEC §7.2)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from codecoremorph.core.models import FileRole, IndexMeta, ReferenceKind, SymbolKind
from codecoremorph.store.index import INDEX_SCHEMA_VERSION, IndexStore, SymbolRow


def _symbol(
    file: str, sl: int, sc: int, el: int, ec: int, *, definition: int, boundary: str
) -> SymbolRow:
    return ("c:@F@f#", "f", "ns::f", "function", file, sl, sc, el, ec, "int f()", "none",
            definition, boundary)  # fmt: skip


def _populate(store: IndexStore) -> None:
    store.write(
        files=[
            ("a.h", "cpp", "0" * 64, "header", 0, 1, "utf-8", "lf"),
            ("a.cpp", "cpp", "1" * 64, "source", 0, 0, "utf-8", "crlf"),
        ],
        symbols=[
            _symbol("a.h", 1, 1, 1, 9, definition=0, boundary="public_api"),
            _symbol("a.cpp", 3, 1, 5, 2, definition=1, boundary="none"),
            # a duplicate from a second translation unit
            _symbol("a.cpp", 3, 1, 5, 2, definition=1, boundary="none"),
        ],
        references=[("a.cpp", "c:@F@g#", "c:@F@f#", "call", 7, 3)],
        dependencies=[("a.cpp", "a.h", "include")],
    )


def test_round_trip(tmp_path: Path) -> None:
    with IndexStore.open(tmp_path / "index.sqlite") as store:
        _populate(store)
        store.set_meta(IndexMeta(adapter="cpp", adapter_version="1", stats={"symbols": 2}))
        store.set_value("note", "x")
        assert store.counts() == {"files": 2, "symbols": 2, "refs": 1, "deps": 1}
        assert [f.path for f in store.files(FileRole.HEADER)] == ["a.h"]
        header = store.file("a.h")
        assert header is not None
        assert header.public_api
        assert store.file("zzz") is None
        (definition,) = store.symbols(file="a.cpp")
        assert definition.is_definition
        assert definition.range.end_line == 5
        assert len(store.symbols(kind=SymbolKind.FUNCTION, name="f")) == 2
        assert len(store.symbols(symbol_id="c:@F@f#")) == 2
        (ref,) = store.references_to("c:@F@f#")
        assert (ref.kind, ref.from_symbol, ref.line) == (ReferenceKind.CALL, "c:@F@g#", 7)
        assert store.references_from("a.cpp") == [ref]
        assert [(d.from_file, d.to_file) for d in store.dependencies()] == [("a.cpp", "a.h")]
        assert store.dependents_of("a.h") == ["a.cpp"]
        meta = store.meta()
        assert meta is not None
        assert meta.stats == {"symbols": 2}
        assert store.value("note") == "x"
        with pytest.raises(ValueError, match="reserved"):
            store.set_value("schema", "2")


def test_remove_files_and_reopen(tmp_path: Path) -> None:
    path = tmp_path / "index.sqlite"
    with IndexStore.open(path) as store:
        _populate(store)
        store.remove_files(["a.cpp"])
        assert store.counts() == {"files": 1, "symbols": 1, "refs": 0, "deps": 0}
    with IndexStore.open(path) as store:
        assert store.counts()["files"] == 1  # kept across opens
    with IndexStore.open(path, reset=True) as store:
        assert store.counts()["files"] == 0


def test_other_schema_versions_are_rebuilt(tmp_path: Path) -> None:
    path = tmp_path / "index.sqlite"
    with IndexStore.open(path) as store:
        _populate(store)
    conn = sqlite3.connect(path)
    conn.execute("UPDATE meta SET value = 'old' WHERE key = 'schema'")
    conn.commit()
    conn.close()
    with IndexStore.open(path) as store:
        assert store.counts()["files"] == 0
        assert store.value("schema") == INDEX_SCHEMA_VERSION
