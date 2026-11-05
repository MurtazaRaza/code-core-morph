"""What a module port still has to move (D-58): ``unported_definitions`` over a small index."""

from __future__ import annotations

from pathlib import Path

import pytest

from codecoremorph.adapters.cpp.ports import trivial_body, unported_definitions
from codecoremorph.store.index import IndexStore, ReferenceRow, SymbolRow

MODULE = """\
namespace {
int helper(int x) { if (x) return 1; return 0; }
}
int ported(int x) { return bridge_fn(x) + 1; }
int uses_ported(int x) { int y = ported(x); if (y) return y; return 0; }
int still_cpp(int x) { int s = 0; for (int i = 0; i < x; ++i) s += i; return s; }
int trivial(int x) { return x + 1; }
static int file_local(int x) { while (x) --x; return x; }
int calls_other_module(int x) { if (x) return other(x); return 0; }
"""
OTHER = "int other(int x) { return bridge_fn(x); }\n"
BRIDGE = 'extern "C" int bridge_fn(int x);\n'


def _symbol(usr: str, qualified: str, path: str, text: str, line: int) -> SymbolRow:
    length = len(text.splitlines()[line - 1])
    name = qualified.rsplit("::", 1)[-1]
    definition = int(path.endswith(".cpp"))
    boundary = "c_abi" if path.endswith(".h") else "none"
    return (usr, name, qualified, "function", path, line, 1, line, length + 1, None, "none",
            definition, boundary)  # fmt: skip


def _call(path: str, caller: str, callee: str, line: int) -> ReferenceRow:
    return (path, caller, callee, "call", line, 10)


@pytest.fixture
def index(tmp_path: Path) -> Path:
    files = {"src/m.cpp": MODULE, "src/other.cpp": OTHER, "rust/c_rs/include/bridge.h": BRIDGE}
    for path, text in files.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(text, encoding="utf-8")
    symbols = [
        _symbol("c:helper", "(anonymous)::helper", "src/m.cpp", MODULE, 2),
        _symbol("c:ported", "ported", "src/m.cpp", MODULE, 4),
        _symbol("c:uses_ported", "uses_ported", "src/m.cpp", MODULE, 5),
        _symbol("c:still_cpp", "still_cpp", "src/m.cpp", MODULE, 6),
        _symbol("c:trivial", "trivial", "src/m.cpp", MODULE, 7),
        _symbol("c:file_local", "file_local", "src/m.cpp", MODULE, 8),
        _symbol("c:calls_other", "calls_other_module", "src/m.cpp", MODULE, 9),
        _symbol("c:other", "other", "src/other.cpp", OTHER, 1),
        _symbol("c:bridge_fn", "bridge_fn", "rust/c_rs/include/bridge.h", BRIDGE, 1),
    ]
    refs = [
        _call("src/m.cpp", "c:ported", "c:bridge_fn", 4),
        _call("src/m.cpp", "c:uses_ported", "c:ported", 5),
        _call("src/m.cpp", "c:calls_other", "c:other", 9),
        _call("src/other.cpp", "c:other", "c:bridge_fn", 1),
    ]
    path = tmp_path / "index.sqlite"
    with IndexStore.open(path) as store:
        store.write(symbols=symbols, references=refs)
    return path


def test_what_is_left_to_port(index: Path) -> None:
    root = index.parent

    def read(path: str) -> str | None:
        return (root / path).read_text(encoding="utf-8")

    sites = unported_definitions(index, ["src/m.cpp"], read)
    # A call into another module's port doesn't port this one; internal helpers, trivial
    # bodies, bridge calls and calls to this file's ported functions don't count.
    assert [(s.line, s.text) for s in sites] == [
        (6, "still_cpp is still C++"),
        (9, "calls_other_module is still C++"),
    ]
    assert unported_definitions(index, ["src/other.cpp"], read) == []
    # Asked about both files at once, a call into the other one still doesn't count.
    both = unported_definitions(index, ["src/m.cpp", "src/other.cpp"], read)
    assert [s.line for s in both] == [6, 9]


@pytest.mark.parametrize(
    ("source", "trivial"),
    [
        ("int f() { return x_; }", True),
        ("A::A(int a) : a_(a) {}", True),
        ("A::A() : v_{1, 2} { g(); }", True),
        ("void f();", True),
        ("int f() { if (a) return 1; return 2; }", False),
        ("int f() { int a = 1; return a; }", False),
        ("int f() { return a ? 1 : 2; }", False),
        ("void f() { while (a) {} }", False),
        ("void f() { g(); h(); }", False),
    ],
)
def test_trivial_body(source: str, trivial: bool) -> None:
    assert trivial_body(source) is trivial
