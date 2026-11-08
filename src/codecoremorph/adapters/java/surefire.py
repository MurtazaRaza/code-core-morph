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
