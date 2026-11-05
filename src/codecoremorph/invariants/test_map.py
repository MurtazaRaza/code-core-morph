"""The test map of a port (D-60): each source test and the target test that took it over, kept
in the project (``rust/test-map.toml`` for C++ → Rust) as ``[[test]]`` entries keyed by the two
languages' names::

    [[test]]
    cpp = "gtest:Rational.AddsFractions"
    rust = "nextest:calc_rs::rational_test::adds_fractions"
    note = "..."                                       # optional

An entry without a target test needs a ``note`` that says why there is none (a CTest entry that
only ran a GoogleTest binary, whose cases are mapped one by one; behavior the target language
can't have). The map is the port's parity: a baseline test passes while it still runs, and so
does the test mapped to it; once it no longer runs, its target test stands for it.
Language-neutral: Phase 11 maps Java tests to C# the same way.
"""

from __future__ import annotations

import tomllib
from collections import Counter
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from codecoremorph.adapters.base import AdapterNotSupportedError, LanguageAdapter
from codecoremorph.core.models import TestAssertions, TestStatus

MAX_SHOWN = 25


@dataclass(frozen=True)
class TestPair:
    """One entry: a source test ID, the target test ID that took it over (or None) and why."""

    __test__ = False  # not a pytest test class

    source: str
    target: str | None
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
        if target is not None and (not isinstance(target, str) or not target):
            problems.append(f"{source}: `{target_language}` must be one test ID")
            continue
        entries.append(
            TestPair(
                source=source,
                target=target if isinstance(target, str) else None,
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
    targets = Counter(e.target for e in listed if e.target is not None)
    for source, count in sorted(sources.items()):
        if count > 1:
            problems.append(f"{source} is mapped {count} times; keep one entry")
    for target, count in sorted(targets.items()):
        if count > 1:
            problems.append(f"{target} stands for {count} tests; map each test to its own")
    wanted = set(required)
    for entry in listed:
        if entry.target is None:
            if entry.note is None:
                problems.append(f"{entry.source}: name the test that took it over, or say why not")
            continue
        if entry.source not in wanted:
            continue
        status = statuses.get(entry.target)
        if status is None:
            problems.append(f"{entry.source}: its test {entry.target} didn't run")
        elif status is not TestStatus.PASSED:
            problems.append(f"{entry.source}: its test {entry.target} {status.value}")
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
    """Each baseline-passing test passes while it runs, and so does the target test mapped to
    it; once it no longer runs, its target test stands for it, and an entry without one
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
            if pair is not None and pair.target is not None:
                needs(pair.target)
        elif pair is None:
            needs(test_id)
        elif pair.target is not None:
            needs(pair.target)
        else:
            retired.append(test_id)
    return Parity(failing=failing, missing=missing, retired=retired)


def gone_but_mapped(
    gone: Iterable[str], seen: Collection[str], pairs: Mapping[str, TestPair]
) -> list[str]:
    """Of the tests ``gone`` from a run, those the map doesn't account for: unmapped, or mapped
    to a target test that isn't in the run either (I-3 through the map)."""
    left: list[str] = []
    for test_id in gone:
        pair = pairs.get(test_id)
        if pair is None or (pair.target is not None and pair.target not in seen):
            left.append(test_id)
    return left


def weaker_translations(
    pairs: Iterable[TestPair],
    sources: Iterable[TestAssertions],
    targets: Iterable[TestAssertions],
) -> list[str]:
    """I-4 across languages: for each pair, a target test that makes fewer checks than its
    source test, or none at all. Kinds don't translate one to one (``EXPECT_THROW`` becomes an
    ``assert!`` on a ``Result``), so counts are compared."""
    source_counts = {a.test_id: len(a.assertions) for a in sources}
    target_counts = {a.test_id: len(a.assertions) for a in targets}
    found: list[str] = []
    for pair in pairs:
        before = source_counts.get(pair.source)
        if pair.target is None:
            note = f" ({pair.note})" if pair.note else ""
            found.append(f"{pair.source} has no translation{note}")
            continue
        if before is None:
            continue  # not a case the source adapter reads (a CTest entry): nothing to count
        after = target_counts.get(pair.target)
        if after is None:
            found.append(f"{pair.source}: its test {pair.target} isn't in the sources")
        elif after < before:
            found.append(
                f"{pair.source} made {before} check(s); its test {pair.target} makes {after}"
            )
    return found


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
