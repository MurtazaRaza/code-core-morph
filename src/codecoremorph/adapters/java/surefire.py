"""Surefire's XML reports and its test selection (SPEC §11.2, D-63).

Every module writes ``target/surefire-reports/TEST-<class>.xml``, one ``<testcase>`` per test
method (JUnit 3, 4 and 5 alike) with the class Surefire ran (``Outer$Inner`` for a nested
class). A test's ID is ``junit:<class>#<method>``. A selection is Surefire's ``-Dtest``:
``<class>#<method>+<method>``, classes separated by commas.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from codecoremorph.adapters import junitxml
from codecoremorph.adapters.junitxml import PREFIX, ReportError, parse_report, test_id
from codecoremorph.core.models import TestCaseResult

__all__ = ["PREFIX", "ReportError", "parse_report", "read_reports", "split_id", "test_id"]


def split_id(test: str) -> tuple[str, str] | None:
    """``(class, method)`` of a ``junit:`` test ID, or None for another kind of ID."""
    if not test.startswith(PREFIX) or "#" not in test:
        return None
    class_name, _, method = test.removeprefix(PREFIX).partition("#")
    return class_name, method


def read_reports(directories: Iterable[Path]) -> tuple[list[TestCaseResult], list[str]]:
    """Every ``TEST-*.xml`` in the report directories, one result per ID (the most
    significant status wins), and the reports it couldn't read. Surefire's reports are JUnit
    XML (:mod:`codecoremorph.adapters.junitxml`)."""
    return junitxml.read_reports(
        path for directory in directories for path in sorted(directory.glob("TEST-*.xml"))
    )


_FORK_CRASHED = "The forked VM terminated without properly saying goodbye"
# Where a sanitizer's report starts: ASan's (and LSan's, TSan's) banner, or UBSan's runtime error.
_SANITIZER = re.compile(r"==\d+==ERROR: \w*Sanitizer|:\d+:\d+: runtime error: ")
_MAX_REPORT = 6000


def crashed_fork(output: str) -> str | None:
    """Why Surefire's forked test JVM died, from Maven's output, or None when it didn't. In a
    port to C++ the JVM runs native code under the sanitizers (D-92): a sanitizer's abort ends
    the fork with no report for the test that was running, and Maven's output has the
    sanitizer's report, which this returns up to its SUMMARY line."""
    if _FORK_CRASHED not in output:
        return None
    lines = output.splitlines()
    for at, line in enumerate(lines):
        if not _SANITIZER.search(line):
            continue
        report: list[str] = []
        for text in lines[at:]:
            report.append(text)
            if text.startswith("SUMMARY: "):
                break
        return (
            "the native code under the sanitizers aborted the forked test JVM:\n"
            + "\n".join(report)[:_MAX_REPORT]
        )
    return (
        "Surefire's forked test JVM terminated without reporting (a crash, or System.exit): "
        "no sanitizer report in Maven's output; see the test run's log and the "
        "*.dumpstream files beside its Surefire reports"
    )


def selection_argument(test_ids: Iterable[str]) -> str | None:
    """Surefire's ``-Dtest`` value for ``test_ids`` (other kinds of IDs are left out), or
    None when none is a ``junit:`` ID."""
    by_class: dict[str, list[str]] = {}
    for test in test_ids:
        parts = split_id(test)
        if parts is None:
            continue
        methods = by_class.setdefault(parts[0], [])
        # A parameterized test's report name adds its arguments: describes(int, String)[1].
        method = re.split(r"[(\[]", parts[1], maxsplit=1)[0].strip()
        if method and method not in methods:
            methods.append(method)
    if not by_class:
        return None
    return ",".join(
        f"{name}#{'+'.join(methods)}" if methods else name for name, methods in by_class.items()
    )
