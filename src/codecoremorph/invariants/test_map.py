"""The test map of a port (D-60): each source test and the target test that took it over, kept
in the project (``rust/test-map.toml`` for C++ → Rust) as ``[[test]]`` entries keyed by the two
languages' names::

    [[test]]
    cpp = "gtest:Rational.AddsFractions"
    rust = "nextest:calc_rs::rational_test::adds_fractions"
    note = "..."                                       # optional

A source test may map to several target tests (``rust = ["...", "..."]``): a coarse C test,
such as the exit status of ``make test``, splits into the tests that took its checks over
(D-69). A target test stands for one source test. An entry without a target test needs a
``note`` that says why there is none (a CTest entry that only ran a GoogleTest binary, whose
cases are mapped one by one; behavior the target language can't have). The map is the port's
parity: a baseline test passes while it still runs, and so do the tests mapped to it; once it
no longer runs, its target tests stand for it. Language-neutral: Phase 11 maps Java tests to
C# the same way.
"""

from __future__ import annotations

import tomllib
from collections import Counter
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from codecoremorph.adapters.base import AdapterNotSupportedError, LanguageAdapter
from codecoremorph.core.models import TestAssertions, TestCaseResult, TestStatus

MAX_SHOWN = 25


@dataclass(frozen=True)
class TestPair:
    """One entry: a source test ID, the target test IDs that took it over (none, one, or
    several, D-69) and why."""

    __test__ = False  # not a pytest test class

    source: str
    targets: tuple[str, ...]
    note: str | None


def read_test_map(
    text: str, source_language: str, target_language: str
) -> tuple[list[TestPair], list[str]]:
    """The entries of a test map, and what is wrong with its form."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        return [], [f"the file isn't valid TOML: {exc}"]
    raw: object = data.get("test", [])
    if not isinstance(raw, list):
        return [], ["`test` must be an array of tables: [[test]]"]
    entries: list[TestPair] = []
    problems: list[str] = []
    for number, item in enumerate(cast(list[object], raw), start=1):
        if not isinstance(item, dict):
            problems.append(f"entry {number} isn't a table")
            continue
        fields = cast(dict[str, object], item)
        source = fields.get(source_language)
        target = fields.get(target_language)
        note = fields.get("note")
        if not isinstance(source, str) or not source:
            problems.append(f"entry {number} has no `{source_language}` test ID")
            continue
        if target is None:
            targets: list[object] = []
        elif isinstance(target, list):
            targets = cast(list[object], target)
        else:
            targets = [target]
        if (target is not None and not targets) or not all(
            isinstance(t, str) and t for t in targets
        ):
            problems.append(f"{source}: `{target_language}` must be a test ID or a list of them")
            continue
        entries.append(
            TestPair(
                source=source,
                targets=tuple(str(t) for t in targets),
                note=note if isinstance(note, str) and note.strip() else None,
            )
        )
    return entries, problems


def by_source(entries: Iterable[TestPair]) -> dict[str, TestPair]:
    """The entries by source test ID (the first, when one is mapped twice)."""
    found: dict[str, TestPair] = {}
    for entry in entries:
        found.setdefault(entry.source, entry)
    return found


def test_map_problems(
    entries: Iterable[TestPair],
    required: Collection[str],
    statuses: Mapping[str, TestStatus],
) -> list[str]:
    """What the ``test_map`` post-condition refuses: a ``required`` test (one the unit
    translates) without an entry; a test mapped twice, or a target test standing for two; an
    entry with neither a target test nor a note; and a target test of a required entry that
    didn't run or didn't pass in this attempt (``statuses``)."""
    listed = list(entries)
    problems: list[str] = []
    sources = Counter(e.source for e in listed)
    targets = Counter(t for e in listed for t in e.targets)
    for source, count in sorted(sources.items()):
        if count > 1:
            problems.append(f"{source} is mapped {count} times; keep one entry")
    for target, count in sorted(targets.items()):
        if count > 1:
            problems.append(f"{target} stands for {count} tests; map each test to its own")
    wanted = set(required)
    for entry in listed:
        if not entry.targets:
            if entry.note is None:
                problems.append(f"{entry.source}: name the test that took it over, or say why not")
            continue
        if entry.source not in wanted:
            continue
        for target in entry.targets:
            status = statuses.get(target)
            if status is None:
                problems.append(f"{entry.source}: its test {target} didn't run")
            elif status is not TestStatus.PASSED:
                problems.append(f"{entry.source}: its test {target} {status.value}")
    mapped = set(sources)
    for test_id in sorted(wanted - mapped):
        problems.append(f"{test_id} has no entry")
    return problems


def test_map_feedback(rule_id: str, path: str, problems: list[str]) -> str:
    """The post-condition's feedback, or an empty string when the map is whole."""
    if not problems:
        return ""
    lines = [
        f"{rule_id}: the test map {path} isn't whole yet ({len(problems)} problem(s)). Map each "
        "test to the passing test that took it over (its full test ID), or say in a note why "
        "it has none:"
    ]
    lines += [f"- {p}" for p in problems[:MAX_SHOWN]]
    if len(problems) > MAX_SHOWN:
        lines.append(f"- ... and {len(problems) - MAX_SHOWN} more")
    return "\n".join(lines)


@dataclass
class Parity:
    """Baseline-passing tests against a test run, through the map."""

    failing: list[str]  # IDs that ran and didn't pass (source tests, or their target tests)
    missing: list[str]  # IDs that should have run and didn't
    retired: list[str]  # source tests gone, mapped to nothing with a note (reported)


def parity(
    passing: Iterable[str], statuses: Mapping[str, TestStatus], pairs: Mapping[str, TestPair]
) -> Parity:
    """Each baseline-passing test passes while it runs, and so do the target tests mapped to
    it; once it no longer runs, its target tests stand for it, and an entry without one
    retires it (I-5 through the map, D-60)."""
    failing: list[str] = []
    missing: list[str] = []
    retired: list[str] = []
    seen: set[str] = set()

    def needs(test_id: str) -> None:
        if test_id in seen:
            return
        seen.add(test_id)
        status = statuses.get(test_id)
        if status is None:
            missing.append(test_id)
        elif status is not TestStatus.PASSED:
            failing.append(test_id)

    for test_id in passing:
        pair = pairs.get(test_id)
        if test_id in statuses:
            needs(test_id)
            for target in pair.targets if pair is not None else ():
                needs(target)
        elif pair is None:
            needs(test_id)
        elif pair.targets:
            for target in pair.targets:
                needs(target)
        else:
            retired.append(test_id)
    return Parity(failing=failing, missing=missing, retired=retired)


def new_failures(known: Collection[str], cases: Iterable[TestCaseResult]) -> list[TestCaseResult]:
    """The cases of a run its baseline doesn't know that didn't pass (nor skip): a test the
    attempt adds must pass, or the commit carries it failing and every later unit excuses it as
    failing before (I-5, D-71; M9's Rust test of crctable.c asserted a wrong entry)."""
    return [
        c
        for c in cases
        if c.test_id not in known and c.status not in (TestStatus.PASSED, TestStatus.SKIPPED)
    ]


def gone_but_mapped(
    gone: Iterable[str], seen: Collection[str], pairs: Mapping[str, TestPair]
) -> list[str]:
    """Of the tests ``gone`` from a run, those the map doesn't account for: unmapped, or mapped
    to target tests that aren't all in the run either (I-3 through the map)."""
    left: list[str] = []
    for test_id in gone:
        pair = pairs.get(test_id)
        if pair is None or any(t not in seen for t in pair.targets):
            left.append(test_id)
    return left


def weaker_translations(
    pairs: Iterable[TestPair],
    sources: Iterable[TestAssertions],
    targets: Iterable[TestAssertions],
    case_of: Callable[[str], str] | None = None,
) -> list[str]:
    """I-4 across languages: for each pair, target tests that make fewer checks together than
    their source test, or none at all. Kinds don't translate one to one (``EXPECT_THROW``
    becomes an ``assert!`` on a ``Result``), so counts are compared; a test split into several
    keeps its checks among them. ``case_of`` gives the case in the target's sources that a
    reported test ID runs (a parameterized case's instance: the target adapter's
    ``test_case_of``), which makes the checks of each of its instances."""
    pairs = list(pairs)
    source_counts = {a.test_id: len(a.assertions) for a in sources}
    by_case = {a.test_id: len(a.assertions) for a in targets}
    target_counts: dict[str, int] = {}
    for pair in pairs:
        for target in pair.targets:
            case = target if target in by_case or case_of is None else case_of(target)
            if case in by_case:
                target_counts[target] = by_case[case]
    found: list[str] = []
    for pair in pairs:
        before = source_counts.get(pair.source)
        if not pair.targets:
            note = f" ({pair.note})" if pair.note else ""
            found.append(f"{pair.source} has no translation{note}")
            continue
        if before is None:
            continue  # not a case the source adapter reads (a CTest entry): nothing to count
        absent = [t for t in pair.targets if t not in target_counts]
        if absent:
            found.append(f"{pair.source}: its test {', '.join(absent)} isn't in the sources")
            continue
        after = sum(target_counts[t] for t in pair.targets)
        if after < before:
            tests = (
                f"its test {pair.targets[0]} makes"
                if len(pair.targets) == 1
                else f"its {len(pair.targets)} tests make"
            )
            found.append(f"{pair.source} made {before} check(s); {tests} {after}")
    return found


_NOT_THE_PORTS = frozenset({".git", "target", "bin", "obj"})  # VCS and build output


def port_tree_files(root: Path, test_map: str) -> list[str]:
    """The files (relative to ``root``) of a port's new tree: the directory its test map is in
    (``rust/``, ``c/``, ``cpp/``), where the target's tests are, whatever its language; the
    whole tree for a map at the top. Build output and VCS directories are left out."""
    parent = Path(test_map).parent
    tree = root if parent == Path() else root / parent
    if not tree.is_dir():
        return []
    found: list[str] = []
    for path in tree.rglob("*"):
        rel = path.relative_to(root)
        if path.is_file() and not _NOT_THE_PORTS.intersection(rel.parts[:-1]):
            found.append(rel.as_posix())
    return sorted(found)


def project_test_pairs(
    root: Path, adapter: LanguageAdapter | None, source_language: str, target_language: str
) -> dict[str, TestPair]:
    """The test map in a tree (``root``), by source test ID: empty without a port's target
    ``adapter`` that keeps one, or without the file."""
    if adapter is None:
        return {}
    try:
        relative = adapter.test_map_path()
    except AdapterNotSupportedError:
        return {}
    path = root / relative
    if not path.is_file():
        return {}
    text = path.read_bytes().decode("utf-8-sig", errors="replace")
    entries, _ = read_test_map(text, source_language, target_language)
    return by_source(entries)
