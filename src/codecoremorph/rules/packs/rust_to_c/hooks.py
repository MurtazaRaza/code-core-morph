"""The ``rust_to_c`` pack's post-condition hooks (D-93)."""

from __future__ import annotations

import posixpath
import re
from pathlib import Path

from codecoremorph.adapters.rust.cargo import find_packages
from codecoremorph.adapters.rust.source import (
    cfg_test_lines,
    macro_definitions,
    proc_macro_package,
)
from codecoremorph.adapters.rust.symbols import mask
from codecoremorph.core.models import MacroKind, RuleSite

_PACKAGE_NAME = re.compile(r'(?m)^\s*name\s*=\s*"([^"]+)"')
_DERIVE = re.compile(r"#\s*\[\s*derive\s*\(([^)]*)\)\s*\]")
_ATTRIBUTE = re.compile(r"#\s*\[\s*(?:(\w+)\s*::\s*)?(\w+)\b")
_EXTERN = re.compile(r"(?m)^\s*(?:#\s*\[[^\]]*\]\s*)*extern\s+crate\b[^;]*;")


def _macro_packages(worktree: Path) -> dict[str, dict[str, MacroKind]]:
    """The worktree's procedural macro packages, by crate name, with their macros."""
    found: dict[str, dict[str, MacroKind]] = {}
    for package in find_packages(worktree):
        base = worktree / package
        try:
            manifest = (base / "Cargo.toml").read_text(encoding="utf-8")
        except OSError:
            continue
        if not proc_macro_package(manifest):
            continue
        named = _PACKAGE_NAME.search(manifest)
        name = named.group(1) if named else posixpath.basename(package)
        texts: dict[str, str] = {}
        for path in sorted(base.rglob("*.rs")):
            if "target" in path.relative_to(base).parts:
                continue
            try:
                texts[path.relative_to(base).as_posix()] = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
        found[name.replace("-", "_")] = macro_definitions(texts)
    return found


def macro_sites_left(worktree: Path, files: list[str]) -> list[RuleSite]:
    """A ported module's façade uses none of the crate's own procedural macros (D-93): no
    derive, attribute or call of one, and no path into their package (an ``extern crate`` line
    the crate root keeps for other modules doesn't count), outside its ``#[cfg(test)]``
    items, whose tests stay Rust until their own unit. Otherwise their Rust, not the C, would
    do what they write."""
    packages = _macro_packages(worktree)
    if not packages:
        return []
    derives = {n for m in packages.values() for n, k in m.items() if k is MacroKind.DERIVE}
    attributes = {n for m in packages.values() for n, k in m.items() if k is MacroKind.ATTRIBUTE}
    functions = {n for m in packages.values() for n, k in m.items() if k is MacroKind.FUNCTION}
    crate_path = re.compile(r"\b(" + "|".join(map(re.escape, sorted(packages))) + r")\s*::")
    calls = (
        re.compile(r"\b(" + "|".join(map(re.escape, sorted(functions))) + r")\s*!")
        if functions
        else None
    )
    found: list[RuleSite] = []
    for rel in sorted(files):
        if not rel.endswith(".rs"):
            continue
        try:
            text = (worktree / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        masked = _EXTERN.sub(lambda m: " " * len(m.group(0)), mask(text))
        hits: list[tuple[int, str]] = []
        own = "a macro of the crate's own"
        for match in _DERIVE.finditer(masked):
            names = {n.strip().rsplit("::", 1)[-1] for n in match.group(1).split(",")}
            hits += [(match.start(), f"derives {n}, {own}") for n in sorted(names & derives)]
        for match in _ATTRIBUTE.finditer(masked):
            if match.group(2) in attributes and (
                match.group(1) is None or match.group(1) in packages
            ):
                hits.append((match.start(), f"#[{match.group(2)}], {own}"))
        if calls is not None:
            hits += [(m.start(), f"{m.group(1)}!, {own}") for m in calls.finditer(masked)]
        hits += [
            (m.start(), f"uses {m.group(1)}, the crate's macro package")
            for m in crate_path.finditer(masked)
        ]
        lines = text.splitlines()
        tests = cfg_test_lines(text)
        for offset, what in hits:
            line = masked.count("\n", 0, offset) + 1
            if any(a <= line <= b for a, b in tests):
                continue
            shown = lines[line - 1].strip() if line <= len(lines) else ""
            found.append(RuleSite(file=rel, line=line, text=f"{what}: {shown}"))
    unique = {(s.file, s.line, s.text): s for s in found}
    return [unique[k] for k in sorted(unique)]
