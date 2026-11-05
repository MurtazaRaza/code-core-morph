"""What a module port (D-58) still has to move: the C++ definitions that don't forward to the
new code yet.

A function with external linkage defined in the unit's files is *ported* when it calls a
bridge function, or a ported function defined in the same file, or when it is trivial: one
statement at most, with no branch or loop (an accessor, a forwarding operator, an empty
destructor). Only the same file counts: C++ that calls another module's façade is still C++.
Helpers with internal linkage (an anonymous namespace, a ``static`` function) belong to the
façade and never count on their own: their callers do.

A *bridge function* is one the index declares with C linkage but defines nowhere: the new
code (a Rust crate) defines it, and C++ sees only its declaration in the bridge header.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from pathlib import Path

from codecoremorph.core.models import (
    Boundary,
    ReferenceKind,
    RuleSite,
    Symbol,
    SymbolKind,
)
from codecoremorph.rules.detectors import mask_c_family
from codecoremorph.store.index import IndexStore

FUNCTION_KINDS = (
    SymbolKind.FUNCTION,
    SymbolKind.METHOD,
    SymbolKind.CONSTRUCTOR,
    SymbolKind.DESTRUCTOR,
    SymbolKind.FUNCTION_TEMPLATE,
)
_CONTROL = re.compile(r"\b(?:if|for|while|do|switch|case|try|catch|goto)\b|\?")
_STATIC = re.compile(r"^\s*(?:template\s*<[^>]*>\s*)?(?:inline\s+)?static\b")


def unported_definitions(
    index_path: Path, files: Iterable[str], read: Callable[[str], str | None]
) -> list[RuleSite]:
    """The definitions in ``files`` that aren't ported yet, one site each (by the index at
    ``index_path``; ``read`` returns a file's text)."""
    wanted = set(files)
    with IndexStore.open(index_path) as store:
        symbols = [s for kind in FUNCTION_KINDS for s in store.symbols(kind=kind)]
        calls: dict[str, set[str]] = {}
        for path in sorted(wanted):
            for ref in store.references_from(path):
                if ref.kind is ReferenceKind.CALL and ref.from_symbol is not None:
                    calls.setdefault(ref.from_symbol, set()).add(ref.to_symbol)
    defined = {s.id for s in symbols if s.is_definition}
    bridge = {
        s.id
        for s in symbols
        if s.kind is SymbolKind.FUNCTION and s.boundary is Boundary.C_ABI and s.id not in defined
    }
    definitions = [s for s in symbols if s.is_definition and s.file in wanted]
    file_of = {s.id: s.file for s in definitions}
    ported: set[str] = set()
    changed = True
    while changed:
        changed = False
        for symbol_id in sorted(set(file_of) - ported):
            callees = calls.get(symbol_id, set())
            same_file = {c for c in callees & ported if file_of[c] == file_of[symbol_id]}
            if callees & bridge or same_file:
                ported.add(symbol_id)
                changed = True

    texts: dict[str, list[str] | None] = {}

    def lines_of(path: str) -> list[str] | None:
        if path not in texts:
            text = read(path)
            texts[path] = None if text is None else mask_c_family(text).splitlines()
        return texts[path]

    sites: list[RuleSite] = []
    seen: set[str] = set()
    for symbol in sorted(definitions, key=lambda s: (s.file, s.range.start_line, s.id)):
        if symbol.id in ported or symbol.id in seen:
            continue
        seen.add(symbol.id)
        lines = lines_of(symbol.file)
        if lines is None:
            continue
        source = _extent(lines, symbol)
        if _internal(symbol, source) or trivial_body(source):
            continue
        sites.append(
            RuleSite(
                file=symbol.file,
                line=symbol.range.start_line,
                column=symbol.range.start_column,
                text=f"{symbol.qualified_name} is still C++",
            )
        )
    return sites


def _extent(lines: list[str], symbol: Symbol) -> str:
    r = symbol.range
    picked = lines[r.start_line - 1 : r.end_line]
    if not picked:
        return ""
    if len(picked) == 1:
        return picked[0][r.start_column - 1 : r.end_column - 1]
    picked[0] = picked[0][r.start_column - 1 :]
    picked[-1] = picked[-1][: r.end_column - 1]
    return "\n".join(picked)


def _internal(symbol: Symbol, source: str) -> bool:
    if "(anonymous)" in symbol.qualified_name:
        return True
    return symbol.kind is SymbolKind.FUNCTION and _STATIC.match(source) is not None


def trivial_body(source: str) -> bool:
    """One statement at most and no branch or loop in the definition's body (its last braced
    block; comments and strings already masked). No body counts as trivial."""
    end = source.rfind("}")
    if end < 0:
        return True
    depth = 0
    start = -1
    for i in range(end, -1, -1):
        if source[i] == "}":
            depth += 1
        elif source[i] == "{":
            depth -= 1
            if depth == 0:
                start = i
                break
    if start < 0:
        return True
    body = source[start + 1 : end]
    return _CONTROL.search(body) is None and body.count(";") <= 1
