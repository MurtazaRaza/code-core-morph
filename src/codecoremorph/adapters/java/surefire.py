"""Surefire's XML reports and its test selection (SPEC §11.2, D-63).

Every module writes ``target/surefire-reports/TEST-<class>.xml``, one ``<testcase>`` per test
method (JUnit 3, 4 and 5 alike) with the class Surefire ran (``Outer$Inner`` for a nested
class). A test's ID is ``junit:<class>#<method>``. A selection is Surefire's ``-Dtest``:
``<class>#<method>+<method>``, classes separated by commas.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from pathlib import Path

from codecoremorph.core.models import TestCaseResult, TestStatus

PREFIX = "junit:"
_DTD = re.compile(r"<!(?:DOCTYPE|ENTITY)", re.IGNORECASE)
_RANK = {
    TestStatus.SKIPPED: 0,
    TestStatus.PASSED: 1,
    TestStatus.FAILED: 2,
    TestStatus.TIMEOUT: 3,
    TestStatus.ERROR: 4,
}
MAX_MESSAGE = 4000


class ReportError(ValueError):
    """A report that isn't Surefire XML."""


def test_id(class_name: str, method: str) -> str:
    return f"{PREFIX}{class_name}#{method}"


def split_id(test: str) -> tuple[str, str] | None:
    """``(class, method)`` of a ``junit:`` test ID, or None for another kind of ID."""
    if not test.startswith(PREFIX) or "#" not in test:
        return None
    class_name, _, method = test.removeprefix(PREFIX).partition("#")
    return class_name, method


def parse_report(data: bytes, name: str) -> list[TestCaseResult]:
    """One Surefire report's test cases."""
    text = data.decode("utf-8", errors="replace")
    # The report holds the output of code under test; refuse entity tricks.
    if _DTD.search(text):
        raise ReportError(f"{name}: test reports must not contain a DTD")
    try:
        root = ET.fromstring(text)  # noqa: S314 - DTDs rejected above
    except ET.ParseError as exc:
        raise ReportError(f"{name}: not XML ({exc})") from None
    cases: list[TestCaseResult] = []
    for case in root.iter("testcase"):
        class_name = case.get("classname") or root.get("name") or "?"
        method = case.get("name") or "?"
        status = TestStatus.PASSED
        message: str | None = None
        stack: str | None = None
        for child in case:
            if child.tag in ("failure", "error"):
                status = TestStatus.FAILED if child.tag == "failure" else TestStatus.ERROR
                message = (child.get("message") or "").strip() or None
                stack = (child.text or "").strip()[:MAX_MESSAGE] or None
                message = message or (stack.splitlines()[0] if stack else None)
            elif child.tag == "skipped" and status is TestStatus.PASSED:
                status = TestStatus.SKIPPED
                message = (child.get("message") or "").strip() or None
        try:
            duration = float(case.get("time") or 0.0)
        except ValueError:
            duration = 0.0
        cases.append(
            TestCaseResult(
                test_id=test_id(class_name, method),
                status=status,
                duration_s=max(duration, 0.0),
                message=message[:MAX_MESSAGE] if message else None,
                stack=stack,
            )
        )
    return cases


def read_reports(directories: Iterable[Path]) -> tuple[list[TestCaseResult], list[str]]:
    """Every ``TEST-*.xml`` in the report directories, one result per ID (the most
    significant status wins), and the reports it couldn't read."""
    merged: dict[str, TestCaseResult] = {}
    problems: list[str] = []
    for directory in directories:
        for path in sorted(directory.glob("TEST-*.xml")):
            try:
                cases = parse_report(path.read_bytes(), path.name)
            except (OSError, ReportError) as exc:
                problems.append(str(exc))
                continue
            for case in cases:
                existing = merged.get(case.test_id)
                if existing is None or _RANK[case.status] > _RANK[existing.status]:
                    merged[case.test_id] = case
    return [merged[k] for k in sorted(merged)], problems


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
