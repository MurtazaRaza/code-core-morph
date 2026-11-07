"""The SQLite symbol index (SPEC §7.2)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from codecoremorph.core.models import (
    Boundary,
    BuildTarget,
    FileRole,
    IndexMeta,
    ReferenceKind,
    SymbolKind,
    TargetKind,
)
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


def test_reference_counts_and_what_other_files_use(tmp_path: Path) -> None:
    def symbol(sid: str, file: str, line: int, *, definition: int) -> SymbolRow:
        name = sid.rsplit("@", 1)[-1]
        return (sid, name, name, "function", file, line, 1, line, 9, None, "none", definition,
                "c_abi")  # fmt: skip

    with IndexStore.open(tmp_path / "index.sqlite") as store:
        store.write(
            files=[
                ("x.h", "c", "0" * 64, "header", 1, 0, "utf-8", "lf"),
                ("x.c", "c", "1" * 64, "source", 0, 0, "utf-8", "lf"),
                ("y.c", "c", "2" * 64, "source", 0, 0, "utf-8", "lf"),
            ],
            symbols=[
                symbol("c:@F@declared", "x.h", 1, definition=0),
                symbol("c:@F@declared", "x.c", 3, definition=1),
                symbol("c:@F@called", "x.c", 6, definition=1),
                symbol("c:@F@alone", "x.c", 9, definition=1),
                symbol("c:x.c@F@helper", "x.c", 12, definition=1),
            ],
            references=[
                ("y.c", None, "c:@F@called", "call", 2, 3),
                ("y.c", None, "c:@F@declared", "call", 3, 3),
                ("y.c", None, "c:@F@declared", "call", 4, 3),
                ("x.c", None, "c:x.c@F@helper", "call", 7, 3),
            ],
            dependencies=[("x.c", "x.h", "include"), ("y.c", "x.h", "include")],
        )
        assert store.reference_counts() == [("y.c", "x.c", 2)]
        assert store.reference_edges() == [("y.c", "x.c")]
        # Declared in a header or used by another file; not one only its own file uses.
        assert [s.name for s in store.used_elsewhere("x.c")] == ["declared", "called"]
        assert store.used_elsewhere("y.c") == []


def test_c_files_defining_the_same_type_do_not_use_each_other(tmp_path: Path) -> None:
    """A C type has no linkage: two files that each define ``struct sink`` define two types,
    though clang gives both one ID (D-70). In C++ the one-definition rule makes them one."""

    def symbol(sid: str, file: str) -> SymbolRow:
        return (sid, "sink", "sink", "struct", file, 1, 1, 4, 2, None, "none", 1, "none")

    with IndexStore.open(tmp_path / "index.sqlite") as store:
        store.write(
            files=[
                ("a.c", "c", "0" * 64, "test", 0, 0, "utf-8", "lf"),
                ("b.c", "c", "1" * 64, "test", 0, 0, "utf-8", "lf"),
                ("a.cpp", "cpp", "2" * 64, "test", 0, 0, "utf-8", "lf"),
                ("b.cpp", "cpp", "3" * 64, "test", 0, 0, "utf-8", "lf"),
            ],
            symbols=[
                symbol("c:@S@sink", "a.c"),
                symbol("c:@S@sink", "b.c"),
                symbol("c:@S@Sink", "a.cpp"),
                symbol("c:@S@Sink", "b.cpp"),
            ],
            references=[
                ("a.c", None, "c:@S@sink", "type", 6, 3),
                ("b.c", None, "c:@S@sink", "type", 6, 3),
                ("a.cpp", None, "c:@S@Sink", "type", 6, 3),
            ],
        )
        assert store.reference_counts() == [("a.cpp", "b.cpp", 1)]


def test_a_programs_definitions_are_off_the_c_abi_boundary(tmp_path: Path) -> None:
    """bzip2recover.c's file-scope globals (M9, D-71): nothing links against a program, so
    what only programs compile is no ABI. The library's files and the header stay on it."""

    def symbol(name: str, file: str, kind: str = "variable") -> SymbolRow:
        return (f"c:@{name}", name, name, kind, file, 1, 1, 1, 9, None, "none", 1, "c_abi")

    with IndexStore.open(tmp_path / "index.sqlite") as store:
        store.write(
            files=[
                (path, "c", f"{n}" * 64, "source", 0, 0, "utf-8", "lf")
                for n, path in enumerate(("bzlib.c", "bzlib.h", "bzip2recover.c", "shared.c"))
            ],
            symbols=[
                symbol("BZ2_bzlibVersion", "bzlib.c", "function"),
                symbol("BZ2_bzlibVersion", "bzlib.h", "function"),
                symbol("bStart", "bzip2recover.c"),
                symbol("tooManyBlocks", "bzip2recover.c", "function"),
                symbol("shared", "shared.c"),
            ],
            references=[],
        )
        store.set_build_targets(
            [
                BuildTarget(name="bz2", kind=TargetKind.LIBRARY, files=["bzlib.c", "shared.c"]),
                BuildTarget(
                    name="bzip2recover",
                    kind=TargetKind.EXECUTABLE,
                    files=["bzip2recover.c", "shared.c"],
                ),
            ]
        )
        store.clear_program_boundaries()
        assert {s.file: s.boundary for s in store.symbols()} == {
            "bzlib.c": Boundary.C_ABI,
            "bzlib.h": Boundary.C_ABI,
            "bzip2recover.c": Boundary.NONE,
            "shared.c": Boundary.C_ABI,  # a library compiles it too
        }
        assert set(store.boundary_files()) == {"bzlib.c", "bzlib.h", "shared.c"}
