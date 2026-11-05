"""The symbol map of a port (D-58): the source's names and the target items that took them
over, kept in the project (``rust/symbol-map.toml`` for C++ → Rust) as ``[[symbol]]`` entries
keyed by the two languages' names::

    [[symbol]]
    cpp = "calc::Rational::str"
    rust = "calc_rs::rational::Rational::to_string"   # or a list of paths
    note = "..."                                       # optional

The agent writes each unit's entries; this check is the mapping steward. Every name the unit's
headers declare publicly needs an entry, every target path must be an item of the target's
index, and no source name may appear twice. An entry may name no target item only with a
``note`` that says why. Language-neutral: Phase 11 maps Java to C# the same way.
"""

from __future__ import annotations

import tomllib
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import cast

from codecoremorph.core.models import Symbol, SymbolKind, Visibility
from codecoremorph.invariants.checks import in_private_scope, private_scopes

MAX_SHOWN = 25
# What the map must cover: types and callables (fields, constants and macros follow them).
MAPPED_KINDS = frozenset(
    {
        SymbolKind.CLASS,
        SymbolKind.STRUCT,
        SymbolKind.UNION,
        SymbolKind.ENUM,
        SymbolKind.FUNCTION,
        SymbolKind.METHOD,
        SymbolKind.CONSTRUCTOR,
        SymbolKind.CLASS_TEMPLATE,
        SymbolKind.FUNCTION_TEMPLATE,
    }
)


@dataclass(frozen=True)
class MapEntry:
    source: str
    targets: tuple[str, ...]
    note: str | None


def read_symbol_map(
    text: str, source_language: str, target_language: str
) -> tuple[list[MapEntry], list[str]]:
    """The entries of a symbol map, and what is wrong with its form."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        return [], [f"the file isn't valid TOML: {exc}"]
    raw: object = data.get("symbol", [])
    if not isinstance(raw, list):
        return [], ["`symbol` must be an array of tables: [[symbol]]"]
    entries: list[MapEntry] = []
    problems: list[str] = []
    for number, item in enumerate(cast(list[object], raw), start=1):
        if not isinstance(item, dict):
            problems.append(f"entry {number} isn't a table")
            continue
        fields = cast(dict[str, object], item)
        source = fields.get(source_language)
        target = fields.get(target_language, [])
        note = fields.get("note")
        if not isinstance(source, str) or not source:
            problems.append(f"entry {number} has no `{source_language}` name")
            continue
        targets = [target] if isinstance(target, str) else target
        if not isinstance(targets, list):
            problems.append(f"{source}: `{target_language}` must be a path or a list of paths")
            continue
        paths = cast(list[object], targets)
        if not all(isinstance(t, str) for t in paths):
            problems.append(f"{source}: `{target_language}` must be a path or a list of paths")
            continue
        entries.append(
            MapEntry(
                source=source,
                targets=tuple(str(t) for t in paths),
                note=note if isinstance(note, str) and note.strip() else None,
            )
        )
    return entries, problems


def required_names(symbols: Iterable[Symbol], headers: Collection[str]) -> dict[str, str]:
    """The names the map must cover for a unit: the types and callables its ``headers``
    declare publicly (private members, private nested types and unnamed scopes left out),
    each with its header."""
    listed = list(symbols)
    hidden = private_scopes(listed)
    names: dict[str, str] = {}
    for symbol in listed:
        if (
            symbol.file in headers
            and not in_private_scope(symbol, hidden)
            and symbol.kind in MAPPED_KINDS
            and symbol.visibility in (Visibility.PUBLIC, Visibility.NONE)
            and "(anonymous)" not in symbol.qualified_name
        ):
            names.setdefault(symbol.qualified_name, symbol.file)
    return dict(sorted(names.items()))


def symbol_map_problems(
    entries: Iterable[MapEntry], required: Mapping[str, str], known: Collection[str]
) -> list[str]:
    """What the steward refuses: missing entries for ``required`` names, target paths not in
    ``known`` (the target index), names mapped twice, entries with neither a path nor a note."""
    problems: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        if entry.source in seen:
            problems.append(f"{entry.source} is mapped twice; keep one entry")
        seen.add(entry.source)
        for target in entry.targets:
            if target not in known:
                problems.append(f"{entry.source}: {target} is no item of the new code")
        if not entry.targets and entry.note is None:
            problems.append(f"{entry.source}: name the items that took it over, or say why not")
    for name, header in required.items():
        if name not in seen:
            problems.append(f"{name} (declared in {header}) has no entry")
    return problems


def symbol_map_feedback(rule_id: str, path: str, problems: list[str]) -> str:
    """The post-condition's feedback, or an empty string when the map is whole."""
    if not problems:
        return ""
    shown = problems[:MAX_SHOWN]
    lines = [
        f"{rule_id}: the symbol map {path} isn't whole yet ({len(problems)} problem(s)). Add an "
        "entry for each name, with the full paths of the items that took it over:"
    ]
    lines += [f"- {p}" for p in shown]
    if len(problems) > MAX_SHOWN:
        lines.append(f"- ... and {len(problems) - MAX_SHOWN} more")
    return "\n".join(lines)
