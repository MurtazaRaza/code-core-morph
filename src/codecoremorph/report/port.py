"""A port's summary for the report (D-60), from the branch's last tree: the symbol and test
maps with their notes, the tests without a counterpart, the unsafe code, and the source code
left with the reason. Language-neutral: the target adapter names its maps and its unsafe code."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from contextlib import suppress
from pathlib import Path

from codecoremorph.adapters.base import AdapterContext, AdapterNotSupportedError, LanguageAdapter
from codecoremorph.core.models import PortSummary, UnitStatus, WorkUnit
from codecoremorph.invariants.symbol_map import read_symbol_map
from codecoremorph.invariants.test_map import read_test_map


def _text(root: Path, relative: str) -> str | None:
    path = root / relative
    return path.read_bytes().decode("utf-8-sig", errors="replace") if path.is_file() else None


def port_summary(
    *,
    worktree: Path,
    target: LanguageAdapter,
    target_ctx: AdapterContext,
    source_language: str,
    target_language: str,
    source_files: Iterable[str],
    units: Sequence[WorkUnit],
    keep_source_api: bool,
    excluded: Mapping[str, int] | None = None,
) -> PortSummary:
    """The summary of the port in ``worktree``. ``source_files`` are the source language's code
    files the run started with; ``units`` the plan's, with their statuses; ``excluded`` the
    files left under each of the manifest's ``project.exclude`` patterns, which no unit
    touches (snappy's benchmark and fuzzers, a vendored googletest)."""
    symbols = 0
    tests_mapped = 0
    without: list[str] = []
    deviations: list[str] = []
    unsafe: list[str] = []
    try:
        symbol_map = _text(worktree, target.symbol_map_path())
    except AdapterNotSupportedError:
        symbol_map = None
    if symbol_map is not None:
        entries, _ = read_symbol_map(symbol_map, source_language, target_language)
        symbols = len(entries)
        deviations += [f"{e.source}: {e.note}" for e in entries if e.note]
    try:
        test_map = _text(worktree, target.test_map_path())
    except AdapterNotSupportedError:
        test_map = None
    if test_map is not None:
        pairs, _ = read_test_map(test_map, source_language, target_language)
        tests_mapped = sum(1 for p in pairs if p.targets)
        without = [f"{p.source}: {p.note}" for p in pairs if not p.targets and p.note]
        deviations += [f"{p.source}: {p.note}" for p in pairs if p.targets and p.note]
    with suppress(AdapterNotSupportedError):
        unsafe = [f"{s.file}:{s.line}" for s in target.unsafe_uses(target_ctx)]
    cells: list[str] = []
    with suppress(AdapterNotSupportedError):
        cells = [f"{s.file}:{s.line}: {s.text}" for s in target.shared_cells(target_ctx)]
    left: list[str] = []
    for path in sorted(set(source_files)):
        if not (worktree / path).is_file():
            continue
        owner = next(
            (u for u in units if path in u.files and u.status is not UnitStatus.COMMITTED), None
        )
        if owner is not None:
            reason = f"{owner.id} is {owner.status.value}"
        elif keep_source_api:
            reason = "kept for outside consumers (keep_source_api)"
        else:
            reason = "no unit of the port removes it"
        left.append(f"{path} ({reason})")
    for pattern, count in sorted((excluded or {}).items()):
        if count:
            files = "1 file, which" if count == 1 else f"{count} files, which"
            left.append(f"{pattern} ({files} project.exclude keeps out of the port)")
    return PortSummary(
        symbols=symbols,
        tests_mapped=tests_mapped,
        tests_without_counterpart=without,
        deviations=deviations,
        unsafe=unsafe,
        shared_cells=cells,
        source_left=left,
    )
