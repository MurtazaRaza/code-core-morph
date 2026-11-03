"""Selecting untested symbols and grouping them into characterization jobs (SPEC §7.11)."""

from __future__ import annotations

from pathlib import Path

import pytest

from codecoremorph.core.models import Plan, UnitStatus, WorkUnit
from codecoremorph.parity.selection import plan_jobs, untested_symbols
from codecoremorph.store.index import IndexStore

H = "0" * 64


def _file(path: str, role: str) -> tuple[str, str, str, str, int, int, str, str]:
    return (path, "cpp", H, role, 0, 0, "utf-8", "lf")


def _symbol(
    sid: str,
    name: str,
    file: str,
    line: int,
    *,
    kind: str = "function",
    definition: bool = True,
    visibility: str = "none",
    qualified: str | None = None,
) -> tuple[str, str, str, str, str, int, int, int, int, str | None, str, int, str]:
    return (sid, name, qualified or name, kind, file, line, 1, line, 2, f"int {name}()",
            visibility, int(definition), "none")  # fmt: skip


@pytest.fixture
def store(tmp_path: Path) -> IndexStore:
    s = IndexStore.open(tmp_path / "index.sqlite", reset=True)
    s.write(
        files=[
            _file("include/calc.h", "header"),
            _file("src/calc.cpp", "source"),
            _file("src/util.cpp", "source"),
            _file("tests/calc_test.cpp", "test"),
        ],
        symbols=[
            # declarations in the header
            _symbol("c:@F@add", "add", "include/calc.h", 2, definition=False),
            _symbol("c:@F@scale", "scale", "include/calc.h", 3, definition=False),
            _symbol("c:@F@clamp", "clamp", "include/calc.h", 4, definition=False),
            _symbol("c:@F@ratio", "ratio", "include/calc.h", 5, definition=False),
            _symbol("c:@S@Calc@F@hidden#", "hidden", "include/calc.h", 7, kind="method",
                    definition=False, visibility="private", qualified="Calc::hidden"),
            _symbol("c:@S@Calc@F@total#", "total", "include/calc.h", 8, kind="method",
                    definition=False, visibility="public", qualified="Calc::total"),
            # definitions
            _symbol("c:@F@add", "add", "src/calc.cpp", 3),  # tested directly
            _symbol("c:@F@scale", "scale", "src/calc.cpp", 6),  # called by add: one call level
            _symbol("c:@F@clamp", "clamp", "src/calc.cpp", 9),  # called only by ratio
            _symbol("c:@F@ratio", "ratio", "src/calc.cpp", 12),  # untested
            _symbol("c:calc.cpp@F@local", "local", "src/calc.cpp", 15),  # static: no header
            _symbol("c:@aN@F@anon", "anon", "src/calc.cpp", 17,
                    qualified="(anonymous namespace)::anon"),
            _symbol("c:@S@Calc@F@hidden#", "hidden", "src/calc.cpp", 20, kind="method",
                    visibility="private", qualified="Calc::hidden"),
            _symbol("c:@S@Calc@F@total#", "total", "src/calc.cpp", 23, kind="method",
                    visibility="public", qualified="Calc::total"),
            _symbol("c:@F@main", "main", "src/util.cpp", 1),
            _symbol("c:@F@helper", "helper", "src/util.cpp", 5),  # no header declares it
        ],
        references=[
            ("tests/calc_test.cpp", "c:@F@TestBody", "c:@F@add", "call", 4, 3),
            ("src/calc.cpp", "c:@F@add", "c:@F@scale", "call", 4, 10),
            ("src/calc.cpp", "c:@F@ratio", "c:@F@clamp", "call", 13, 10),
        ],
    )  # fmt: skip
    return s


def test_untested_means_no_test_reaches_it_directly_or_through_one_call(
    store: IndexStore,
) -> None:
    found = untested_symbols(store, ["src/calc.cpp", "src/util.cpp"])
    assert [(s.name, s.line, s.declared_in) for s in found] == [
        ("clamp", 9, ["include/calc.h"]),
        ("ratio", 12, ["include/calc.h"]),
        ("total", 23, ["include/calc.h"]),
    ]
    assert found[2].qualified_name == "Calc::total"


def test_a_header_units_symbols_are_found_by_their_definitions(store: IndexStore) -> None:
    found = untested_symbols(store, ["include/calc.h"])
    assert [(s.name, s.file, s.line) for s in found] == [
        ("clamp", "src/calc.cpp", 9),
        ("ratio", "src/calc.cpp", 12),
        ("total", "src/calc.cpp", 23),
    ]


def test_scope_all_takes_tested_symbols_too(store: IndexStore) -> None:
    found = untested_symbols(store, ["src/calc.cpp"], scope_all=True)
    assert [s.name for s in found] == ["add", "scale", "clamp", "ratio", "total"]


def test_test_files_have_nothing_to_characterize(store: IndexStore) -> None:
    assert untested_symbols(store, ["tests/calc_test.cpp"]) == []


def _plan(*units: WorkUnit) -> Plan:
    return Plan(units=list(units))


def _unit(seq: int, *files: str, status: UnitStatus = UnitStatus.PENDING) -> WorkUnit:
    return WorkUnit(
        id=f"U-{seq:04d}", seq=seq, rules=["replace_typedefs_with_using"], files=list(files),
        status=status,
    )  # fmt: skip


def _name(test_dir: str, stem: str) -> str:
    return f"{test_dir}/{stem}_test.cpp"


def test_one_job_per_pending_unit_with_untested_symbols(store: IndexStore) -> None:
    plan = _plan(
        _unit(1, "CMakeLists.txt"),
        _unit(2, "src/calc.cpp", "include/calc.h"),
        _unit(3, "src/util.cpp"),
        _unit(4, "src/calc.cpp", status=UnitStatus.SKIPPED),
    )
    jobs = plan_jobs(plan, store, test_dir="tests/ccm", max_tests=10, scope_all=False,
                     test_file=_name)  # fmt: skip
    assert [(j.id, j.plan_unit, j.test_file, j.sources) for j in jobs] == [
        ("C-0001", "U-0002", "tests/ccm/calc_test.cpp", ["src/calc.cpp"]),
    ]
    assert [s.name for s in jobs[0].symbols] == ["clamp", "ratio", "total"]
    assert jobs[0].max_tests == 10


def test_jobs_get_distinct_new_file_names_and_a_symbol_limit(store: IndexStore) -> None:
    plan = _plan(_unit(1, "src/calc.cpp"), _unit(2, "src/calc.cpp"))
    jobs = plan_jobs(
        plan, store, test_dir="t", max_tests=1, scope_all=False, test_file=_name,
        taken=lambda path: path == "t/calc_test.cpp",
    )  # fmt: skip
    assert [j.test_file for j in jobs] == ["t/calc_2_test.cpp", "t/calc_3_test.cpp"]
    assert [len(j.symbols) for j in jobs] == [3, 3]  # max_tests x 3
