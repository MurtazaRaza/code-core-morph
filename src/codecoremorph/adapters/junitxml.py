"""JUnit XML test reports, as JUnit's Ant task and Maven's Surefire made them and C, C++ and
other test frameworks write them (cmocka, Unity's scripts, CppUTest, Catch2, GoogleTest's
``--gtest_output=xml``): ``<testsuites>`` or ``<testsuite>`` with a ``<testcase>`` per test,
which holds a ``<failure>``, an ``<error>`` or a ``<skipped>`` when it didn't pass.

A test's ID is ``junit:<class>#<name>``: the case's ``classname``, else its suite's ``name``.
Reports hold the output of the code under test, so a report with a DTD is refused (no entity
tricks) and messages are cut short.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from pathlib import Path

from codecoremorph.core.models import TestCaseResult, TestStatus

PREFIX = "junit:"
MAX_MESSAGE = 4000
_DTD = re.compile(r"<!(?:DOCTYPE|ENTITY)", re.IGNORECASE)
_RANK = {
    TestStatus.SKIPPED: 0,
    TestStatus.PASSED: 1,
    TestStatus.FAILED: 2,
    TestStatus.TIMEOUT: 3,
    TestStatus.ERROR: 4,
}


class ReportError(ValueError):
    """A report that isn't JUnit XML."""


def test_id(class_name: str, name: str) -> str:
    return f"{PREFIX}{class_name}#{name}"


def parse_report(data: bytes, name: str) -> list[TestCaseResult]:
    """One report's test cases, in document order."""
    text = data.decode("utf-8", errors="replace")
    if _DTD.search(text):
        raise ReportError(f"{name}: test reports must not contain a DTD")
    try:
        root = ET.fromstring(text)  # noqa: S314 - DTDs rejected above
    except ET.ParseError as exc:
        raise ReportError(f"{name}: not XML ({exc})") from None
    if root.tag not in ("testsuites", "testsuite"):
        raise ReportError(f"{name}: not a JUnit report (its root is <{root.tag}>)")
    cases: list[TestCaseResult] = []
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite")) or [root]
    for suite in suites:
        for case in suite.iter("testcase"):
            cases.append(_case(case, suite.get("name") or root.get("name") or "?"))
    return cases


def _case(case: ET.Element, suite: str) -> TestCaseResult:
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
    return TestCaseResult(
        test_id=test_id(case.get("classname") or suite, case.get("name") or "?"),
        status=status,
        duration_s=max(duration, 0.0),
        message=message[:MAX_MESSAGE] if message else None,
        stack=stack,
    )


def read_reports(paths: Iterable[Path]) -> tuple[list[TestCaseResult], list[str]]:
    """The test cases of every report in ``paths``, one result per ID (the most significant
    status wins: a test run twice that failed once failed), and the reports it couldn't read."""
    merged: dict[str, TestCaseResult] = {}
    problems: list[str] = []
    for path in paths:
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
