"""Read-only agent tools: path safety, caps that always say so, strict schemas (D-19)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from codecoremorph.agent.tools import MAX_SEARCH_MATCHES, TOOL_SPECS, ToolBox
from codecoremorph.store.index import IndexStore


@pytest.fixture
def box(tmp_path: Path) -> ToolBox:
    root = tmp_path / "wt"
    (root / "src").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("secret = 1\n")
    (root / "src" / "a.cpp").write_bytes(b"int a;\r\nint b;\r\nint c;\r\n")
    (root / "src" / "big.txt").write_text("\n".join(f"line {i}" for i in range(1, 2001)))
    (tmp_path / "outside.txt").write_text("outside\n")
    return ToolBox(root)


def test_schemas_are_strict_compatible() -> None:
    def check(schema: dict[str, Any]) -> None:
        if schema.get("type") == "object":
            assert schema["additionalProperties"] is False
            assert sorted(schema["required"]) == sorted(schema["properties"])
        for value in schema.get("properties", {}).values():
            check(value)
        for option in schema.get("anyOf", []):
            check(option)
        if isinstance(schema.get("items"), dict):
            check(schema["items"])
        for forbidden in ("minLength", "maxLength", "minimum", "maximum"):
            assert forbidden not in schema

    for spec in TOOL_SPECS:
        assert spec.strict
        check(spec.input_schema)
    assert [s.name for s in TOOL_SPECS][-1] == "submit_edits"


def test_read_file(box: ToolBox) -> None:
    out = box.run("read_file", {"path": "src/a.cpp", "start_line": 2, "end_line": None})
    assert out.content == "src/a.cpp (lines 2-3 of 3)\nint b;\nint c;"
    big = box.run("read_file", {"path": "src/big.txt", "start_line": None, "end_line": None})
    assert "(lines 1-1500 of 2000)" in big.content
    assert big.content.endswith("[stopped at line 1500; read from line 1501 for more]")
    assert box.calls == 2


@pytest.mark.parametrize(
    "path", ["../outside.txt", "/etc/passwd", "C:/Windows/win.ini", ".git/config", "src/../../x"]
)
def test_paths_stay_inside_the_worktree(box: ToolBox, path: str) -> None:
    out = box.run("read_file", {"path": path, "start_line": None, "end_line": None})
    assert out.is_error
    assert "(lines" not in out.content  # nothing was read


def test_invalid_arguments_are_errors_not_crashes(box: ToolBox) -> None:
    out = box.run("read_file", {"path": "src/a.cpp", "start_line": "two"})
    assert out.is_error
    assert "Invalid arguments for read_file" in out.content
    assert box.run("read_file", {"path": "src/a.cpp", "extra": 1}).is_error
    assert box.run("shell", {"cmd": "rm -rf /"}).is_error
    missing = box.run("read_file", {"path": "src/none.cpp", "start_line": None, "end_line": None})
    assert missing.content == "src/none.cpp is not a file"


def test_list_and_search(box: ToolBox) -> None:
    listing = box.run("list_dir", {"path": "."}).content
    assert listing == "./\nsrc/"  # .git is never listed
    found = box.run("search_code", {"regex": r"int \w;", "glob": "*.cpp"}).content
    assert found == "src/a.cpp:1: int a;\nsrc/a.cpp:2: int b;\nsrc/a.cpp:3: int c;"
    many = box.run("search_code", {"regex": "line", "glob": None}).content
    assert many.endswith(f"[more than {MAX_SEARCH_MATCHES} matches; narrow the search]")
    assert box.run("search_code", {"regex": "(", "glob": None}).is_error
    assert box.run("search_code", {"regex": "secret", "glob": None}).content == "No matches."


def test_symbol_tools(box: ToolBox, tmp_path: Path) -> None:
    assert "isn't available" in box.run("find_symbol", {"name": "a"}).content
    index = tmp_path / "index.sqlite"
    with IndexStore.open(index) as store:
        store.write(
            symbols=[("c:@a", "a", "ns::a", "variable", "src/a.cpp", 1, 1, 1, 6, "int a",
                      "none", 1, "none")],
            references=[("src/b.cpp", None, "c:@a", "use", 4, 2)],
        )  # fmt: skip
    indexed = ToolBox(box.worktree, index)
    assert indexed.run("find_symbol", {"name": "ns::a"}).content == (
        "c:@a  variable ns::a  src/a.cpp:1 (definition)"
    )
    assert indexed.run("get_references", {"symbol_id": "c:@a"}).content == "src/b.cpp:4:2 use"
    assert indexed.run("get_signature", {"symbol_id": "c:@a"}).content == (
        "src/a.cpp:1: int a (definition)"
    )
    assert "No symbol" in indexed.run("find_symbol", {"name": "zzz"}).content
