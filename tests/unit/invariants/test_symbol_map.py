"""The symbol map and its steward (D-58)."""

from __future__ import annotations

from codecoremorph.core.models import SourceRange, Symbol, SymbolKind, Visibility
from codecoremorph.invariants.symbol_map import (
    read_symbol_map,
    required_names,
    symbol_map_feedback,
    symbol_map_problems,
)

MAP = """\
# a comment
[[symbol]]
cpp = "calc::Rational"
rust = "calc_rs::rational::Rational"

[[symbol]]
cpp = "calc::operator-"
rust = ["calc_rs::rational::Rational::neg", "calc_rs::rational::Rational::sub"]

[[symbol]]
cpp = "calc::Number::operator="
rust = []
note = "a Rust Number is Copy"
"""
KNOWN = {
    "calc_rs::rational::Rational",
    "calc_rs::rational::Rational::neg",
    "calc_rs::rational::Rational::sub",
}


def _symbol(name: str, kind: SymbolKind, file: str, visibility: Visibility) -> Symbol:
    return Symbol(
        id=f"c:{name}",
        name=name.rsplit("::", 1)[-1],
        qualified_name=name,
        kind=kind,
        file=file,
        range=SourceRange(start_line=1, start_column=1, end_line=1, end_column=2),
        visibility=visibility,
    )


def test_a_whole_map() -> None:
    entries, problems = read_symbol_map(MAP, "cpp", "rust")
    assert problems == []
    assert [(e.source, e.targets, e.note) for e in entries] == [
        ("calc::Rational", ("calc_rs::rational::Rational",), None),
        (
            "calc::operator-",
            ("calc_rs::rational::Rational::neg", "calc_rs::rational::Rational::sub"),
            None,
        ),
        ("calc::Number::operator=", (), "a Rust Number is Copy"),
    ]
    required = {"calc::Rational": "include/calc/rational.h"}
    assert symbol_map_problems(entries, required, KNOWN) == []
    assert symbol_map_feedback("port_module", "rust/symbol-map.toml", []) == ""


def test_what_the_steward_refuses() -> None:
    text = MAP + (
        '\n[[symbol]]\ncpp = "calc::Rational"\nrust = "calc_rs::rational::Ghost"\n'
        '\n[[symbol]]\ncpp = "calc::Lexer"\n'
    )
    entries, problems = read_symbol_map(text, "cpp", "rust")
    assert problems == []
    required = {"calc::Rational": "include/calc/rational.h", "calc::parse": "include/calc/p.h"}
    found = symbol_map_problems(entries, required, KNOWN)
    assert found == [
        "calc::Rational is mapped twice; keep one entry",
        "calc::Rational: calc_rs::rational::Ghost is no item of the new code",
        "calc::Lexer: name the items that took it over, or say why not",
        "calc::parse (declared in include/calc/p.h) has no entry",
    ]
    feedback = symbol_map_feedback("port_module", "rust/symbol-map.toml", found)
    assert feedback.startswith("port_module: the symbol map rust/symbol-map.toml isn't whole")
    assert "- calc::parse (declared in include/calc/p.h) has no entry" in feedback


def test_malformed_maps() -> None:
    assert read_symbol_map("[[symbol]\n", "cpp", "rust")[1][0].startswith("the file isn't valid")
    assert read_symbol_map("symbol = 3\n", "cpp", "rust")[1] == [
        "`symbol` must be an array of tables: [[symbol]]"
    ]
    _, problems = read_symbol_map(
        '[[symbol]]\nrust = "x"\n\n[[symbol]]\ncpp = "a"\nrust = 3\n', "cpp", "rust"
    )
    assert problems == [
        "entry 1 has no `cpp` name",
        "a: `rust` must be a path or a list of paths",
    ]


def test_required_names() -> None:
    symbols = [
        _symbol("calc::Rational", SymbolKind.CLASS, "include/r.h", Visibility.NONE),
        _symbol("calc::Rational::str", SymbolKind.METHOD, "include/r.h", Visibility.PUBLIC),
        _symbol("calc::Rational::normalize", SymbolKind.METHOD, "include/r.h", Visibility.PRIVATE),
        _symbol("calc::Rational::num_", SymbolKind.FIELD, "include/r.h", Visibility.PRIVATE),
        _symbol("calc::(anonymous)::gcd", SymbolKind.FUNCTION, "include/r.h", Visibility.NONE),
        _symbol("calc::Lexer", SymbolKind.CLASS, "include/l.h", Visibility.NONE),
        _symbol("calc::LIMIT", SymbolKind.VARIABLE, "include/r.h", Visibility.NONE),
    ]
    assert required_names(symbols, {"include/r.h"}) == {
        "calc::Rational": "include/r.h",
        "calc::Rational::str": "include/r.h",
    }
