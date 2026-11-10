"""VSTest's TRX reports (SPEC §11.3): test results with stable IDs, for NUnit, xUnit and MSTest.

A TRX file lists each run test as a ``UnitTestResult`` (its outcome, duration and error) and
defines it in ``TestDefinitions`` (its class and name). The test ID is
``dotnet:<class>#<test>``: the class as the TRX spells it (``Outer+Inner`` for a nested class),
the test as the framework names it, with a parameterized case's arguments
(``Describes(3,"number 3")``); xUnit's names, which repeat the class, lose it.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ElementTree
from collections.abc import Iterable
from pathlib import Path

from codecoremorph.core.models import TestCaseResult, TestStatus

PREFIX = "dotnet:"
_NS = {"t": "http://microsoft.com/schemas/VisualStudio/TeamTest/2010"}
_OUTCOMES = {
    "passed": TestStatus.PASSED,
    "failed": TestStatus.FAILED,
    "error": TestStatus.ERROR,
    "timeout": TestStatus.TIMEOUT,
    "aborted": TestStatus.ERROR,
    "notexecuted": TestStatus.SKIPPED,
    "inconclusive": TestStatus.SKIPPED,
    "notrunnable": TestStatus.ERROR,
    "passedbutrunaborted": TestStatus.PASSED,
    "warning": TestStatus.PASSED,
    "completed": TestStatus.PASSED,
}
_DURATION = re.compile(r"^(?:(\d+)\.)?(\d+):(\d+):(\d+(?:\.\d+)?)$")
_MAX_MESSAGE = 4000


def test_id(class_name: str, name: str) -> str:
    """The ID of ``name`` in ``class_name`` (xUnit's ``Ns.Class.Method`` names lose the class)."""
    if name.startswith(class_name + "."):
        name = name[len(class_name) + 1 :]
    return f"{PREFIX}{class_name}#{name}"


def split_id(test: str) -> tuple[str, str] | None:
    """``(class, test)`` of an ID, or None for one that isn't a C# test's."""
    if not test.startswith(PREFIX) or "#" not in test:
        return None
    class_name, name = test[len(PREFIX) :].split("#", 1)
    return class_name, name


def _seconds(text: str | None) -> float:
    if not text:
        return 0.0
    match = _DURATION.match(text.strip())
    if match is None:
        return 0.0
    days, hours, minutes, seconds = match.groups()
    return int(days or 0) * 86400 + int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def parse_report(path: Path) -> list[TestCaseResult]:
    """The results in one TRX file. A file with a DTD is refused (no entity expansion)."""
    data = path.read_bytes()
    if b"<!DOCTYPE" in data[:4096] or b"<!ENTITY" in data:
        raise ValueError(f"{path.name}: a TRX file with a DTD")
    root = ElementTree.fromstring(data)  # noqa: S314 - DTDs rejected above
    definitions: dict[str, tuple[str, str]] = {}
    for unit in root.iterfind("t:TestDefinitions/t:UnitTest", _NS):
        method = unit.find("t:TestMethod", _NS)
        if method is None:
            continue
        definitions[unit.get("id", "")] = (
            method.get("className", ""),
            unit.get("name") or method.get("name", ""),
        )
    cases: list[TestCaseResult] = []
    for result in root.iterfind("t:Results/t:UnitTestResult", _NS):
        # Data-driven MSTest results nest their rows as inner results.
        inner = result.findall("t:InnerResults/t:UnitTestResult", _NS)
        for item in inner or [result]:
            class_name, name = definitions.get(result.get("testId", ""), ("", ""))
            name = item.get("testName") or name
            if not class_name or not name:
                continue
            outcome = (item.get("outcome") or "").lower()
            status = _OUTCOMES.get(outcome, TestStatus.ERROR)
            message = _message(item)
            cases.append(
                TestCaseResult(
                    test_id=test_id(class_name, name),
                    status=status,
                    duration_s=_seconds(item.get("duration")),
                    message=message if status is not TestStatus.PASSED else None,
                )
            )
    return cases


def _message(result: ElementTree.Element) -> str | None:
    info = result.find("t:Output/t:ErrorInfo", _NS)
    if info is None:
        return None
    parts = [
        (info.findtext("t:Message", default="", namespaces=_NS) or "").strip(),
        (info.findtext("t:StackTrace", default="", namespaces=_NS) or "").strip(),
    ]
    text = "\n".join(p for p in parts if p)
    return text[:_MAX_MESSAGE] if text else None


def read_reports(paths: Iterable[Path]) -> tuple[list[TestCaseResult], list[str]]:
    """Every result in the TRX files, IDs made unique (a repeated one gets ``[2]``, ``[3]``:
    NUnit cases with the same arguments, a test project built for two frameworks), and the
    problems of files that couldn't be read."""
    cases: list[TestCaseResult] = []
    problems: list[str] = []
    seen: dict[str, int] = {}
    for path in sorted(paths):
        try:
            found = parse_report(path)
        except (OSError, ValueError, ElementTree.ParseError) as exc:
            problems.append(f"{path.name}: {exc}")
            continue
        for case in found:
            count = seen.get(case.test_id, 0) + 1
            seen[case.test_id] = count
            if count > 1:
                case = case.model_copy(update={"test_id": f"{case.test_id}[{count}]"})
            cases.append(case)
    return cases, problems


def run_errors(paths: Iterable[Path]) -> list[str]:
    """The run-level errors the TRX files record (``RunInfo`` entries with an error outcome),
    such as a test host that crashed: a sanitizer's report from native code ends the run there,
    with no result for the test that was running (D-78)."""
    found: list[str] = []
    for path in sorted(paths):
        try:
            data = path.read_bytes()
            if b"<!DOCTYPE" in data[:4096] or b"<!ENTITY" in data:
                continue
            root = ElementTree.fromstring(data)  # noqa: S314 - DTDs rejected above
        except (OSError, ElementTree.ParseError):
            continue
        for info in root.iterfind("t:ResultSummary/t:RunInfos/t:RunInfo", _NS):
            if (info.get("outcome") or "").lower() != "error":
                continue
            text = (info.findtext("t:Text", default="", namespaces=_NS) or "").strip()
            if text:
                found.append(text[:_MAX_MESSAGE])
    return found


def _escape(value: str) -> str:
    return re.sub(r"([\\()&|=!~,])", r"\\\1", value)


def selection_filter(test_ids: Iterable[str]) -> str | None:
    """A ``--filter`` taking ``test_ids``: ``FullyQualifiedName=<class>.<method>`` for each,
    which takes every case of a parameterized method; None when none is a C# test's."""
    names: list[str] = []
    for test in test_ids:
        parts = split_id(re.sub(r"\[\d+\]$", "", test))
        if parts is None:
            continue
        class_name, name = parts
        method = name.split("(", 1)[0].strip()
        full = f"{class_name}.{method}"
        if full not in names:
            names.append(full)
    if not names:
        return None
    return "|".join(f"FullyQualifiedName={_escape(n)}" for n in names)
