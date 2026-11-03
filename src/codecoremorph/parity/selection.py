"""Which code gets characterization tests (SPEC §7.11 step 1, D-46).

A unit's symbols are *untested* when no test file references them, directly or through one
call: a test calls ``f``, and ``f`` references the symbol. Only code a test can reach through
the project's interface counts: functions, methods, constructors and function templates that
are declared or defined in the unit's own files, public (or not class members), defined in the
project, and declared in a header. A header unit (a typedef rewrite, say) thus characterizes
the functions its header declares, wherever they are defined: changing the header's types can
change what they do. ``scope: all`` takes every such symbol, tested or not.

The plan made before characterization groups the symbols into jobs, one per unit with any, in
plan order; each job's tests go into one new file.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import PurePosixPath

from codecoremorph.core.ids import job_id
from codecoremorph.core.models import (
    CharacterizationJob,
    CharacterizedSymbol,
    FileRole,
    Plan,
    Symbol,
    SymbolKind,
    UnitStatus,
    Visibility,
    owned_files,
)
from codecoremorph.store.index import IndexStore

CALLABLE = frozenset(
    {SymbolKind.FUNCTION, SymbolKind.METHOD, SymbolKind.CONSTRUCTOR, SymbolKind.FUNCTION_TEMPLATE}
)
_REACHABLE = frozenset({Visibility.PUBLIC, Visibility.NONE})
SYMBOLS_PER_TEST = 3  # a job lists at most this many symbols per test it may write


class _Reach:
    """Whether tests reach a symbol, with the index lookups cached."""

    def __init__(self, store: IndexStore):
        self.store = store
        self.tests = {f.path for f in store.files(FileRole.TEST)}
        self._direct: dict[str, bool] = {}

    def direct(self, symbol_id: str) -> bool:
        if symbol_id not in self._direct:
            refs = self.store.references_to(symbol_id)
            self._direct[symbol_id] = any(r.from_file in self.tests for r in refs)
        return self._direct[symbol_id]

    def tested(self, symbol_id: str) -> bool:
        if self.direct(symbol_id):
            return True
        callers = {
            r.from_symbol
            for r in self.store.references_to(symbol_id)
            if r.from_symbol and r.from_symbol != symbol_id and r.from_file not in self.tests
        }
        return any(self.direct(caller) for caller in sorted(callers))


def _candidate(symbol: Symbol) -> bool:
    return (
        symbol.kind in CALLABLE
        and symbol.visibility in _REACHABLE
        and "(anonymous" not in symbol.qualified_name
        and not (symbol.kind is SymbolKind.FUNCTION and symbol.qualified_name == "main")
    )


def untested_symbols(
    store: IndexStore, files: Iterable[str], *, scope_all: bool = False
) -> list[CharacterizedSymbol]:
    """The symbols of ``files`` (non-test files) to characterize, in the order ``files``
    declare or define them."""
    reach = _Reach(store)
    headers = {f.path for f in store.files(FileRole.HEADER)}
    found: list[CharacterizedSymbol] = []
    seen: set[str] = set()
    for path in files:
        if path in reach.tests:
            continue
        for row in store.symbols(file=path):
            if row.id in seen or not _candidate(row):
                continue
            seen.add(row.id)
            rows = store.symbols(symbol_id=row.id)
            definition = next((r for r in rows if r.is_definition), None)
            if definition is None or definition.file in reach.tests:
                continue  # defined outside the project (a library), or by a test
            declared = sorted({r.file for r in rows if r.file in headers})
            if definition.file not in headers and not declared:
                continue  # file-local: no test can call it
            if not scope_all and reach.tested(row.id):
                continue
            found.append(
                CharacterizedSymbol(
                    id=row.id,
                    name=definition.name,
                    qualified_name=definition.qualified_name,
                    kind=definition.kind,
                    file=definition.file,
                    line=definition.range.start_line,
                    signature=definition.signature,
                    declared_in=declared,
                )
            )
    return found


def plan_jobs(
    plan: Plan,
    store: IndexStore,
    *,
    test_dir: str,
    max_tests: int,
    scope_all: bool,
    test_file: Callable[[str, str], str],
    taken: Callable[[str], bool] = lambda _path: False,
) -> list[CharacterizationJob]:
    """One job per pending unit of ``plan`` with untested symbols. ``test_file(dir, stem)`` is
    the adapter's name for a new test file; ``taken(path)`` says a path already exists."""
    jobs: list[CharacterizationJob] = []
    used: set[str] = set()
    for unit in plan.units:
        if unit.status is not UnitStatus.PENDING:
            continue
        symbols = untested_symbols(store, owned_files(unit), scope_all=scope_all)
        if not symbols:
            continue
        symbols = symbols[: max_tests * SYMBOLS_PER_TEST]
        stem = PurePosixPath(symbols[0].file).stem
        path = test_file(test_dir, stem)
        number = 2
        while path in used or taken(path):
            path = test_file(test_dir, f"{stem}_{number}")
            number += 1
        used.add(path)
        jobs.append(
            CharacterizationJob(
                id=job_id(len(jobs) + 1),
                plan_unit=unit.id,
                test_file=path,
                symbols=symbols,
                sources=sorted({s.file for s in symbols}),
                max_tests=max_tests,
            )
        )
    return jobs
