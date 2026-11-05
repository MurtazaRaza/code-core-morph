"""Test reports for CMake projects: CTest JUnit and GoogleTest XML (SPEC §11.1).

Test IDs:

- ``ctest:<name>`` for every CTest entry, from ``ctest --output-junit``.
- ``gtest:<Suite>.<Case>`` for every GoogleTest case, from the XML each test binary writes
  when ``GTEST_OUTPUT=xml:<dir>/`` is set.

A CTest entry that wraps a GoogleTest binary therefore shows up both as one ``ctest:`` ID and
as its cases' ``gtest:`` IDs. Both are stable across runs, and parity is checked per ID.
"""

from __future__ import annotations

import json
import posixpath
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, cast

from codecoremorph.core.models import BuildTarget, TestCaseResult, TestEntry, TestStatus

_DTD = re.compile(rb"<!(DOCTYPE|ENTITY)", re.IGNORECASE)
_STATUS_RANK = {
    TestStatus.ERROR: 5,
    TestStatus.TIMEOUT: 4,
    TestStatus.FAILED: 3,
    TestStatus.PASSED: 2,
    TestStatus.SKIPPED: 1,
}


class ReportParseError(ValueError):
    pass


def _parse_xml(data: bytes, source: str) -> ET.Element:
    # Reports are written by code under test; refuse entity tricks instead of trusting them.
    if _DTD.search(data):
        raise ReportParseError(f"{source}: test reports must not contain a DTD")
    try:
        return ET.fromstring(data)  # noqa: S314 - DTDs rejected above
    except ET.ParseError as exc:
        raise ReportParseError(f"{source}: {exc}") from exc


def _seconds(value: str | None) -> float:
    try:
        return max(0.0, float(value)) if value else 0.0
    except ValueError:
        return 0.0


def _text(element: ET.Element | None, limit: int = 4000) -> str | None:
    if element is None:
        return None
    text = (element.get("message") or "").strip()
    body = (element.text or "").strip()
    if body and body != text:
        text = f"{text}\n{body}" if text else body
    return text[:limit] or None


def parse_ctest_junit(data: bytes, source: str = "ctest junit") -> list[TestCaseResult]:
    root = _parse_xml(data, source)
    cases: list[TestCaseResult] = []
    for case in root.iter("testcase"):
        name = case.get("name")
        if not name:
            continue
        status_attr = (case.get("status") or "").lower()
        failure = case.find("failure")
        error = case.find("error")
        skipped = case.find("skipped")
        message = _text(failure) or _text(error) or _text(skipped)
        if failure is not None or error is not None:
            lowered = (message or "").lower()
            if "timeout" in lowered:
                status = TestStatus.TIMEOUT
            elif error is not None or any(
                word in lowered for word in ("exception", "segfault", "child aborted", "sigsegv")
            ):
                status = TestStatus.ERROR
            else:
                status = TestStatus.FAILED
        elif skipped is not None or status_attr in ("disabled", "notrun", "skipped"):
            status = TestStatus.SKIPPED
        else:
            status = TestStatus.PASSED
        output = case.findtext("system-out")
        stack = output.strip()[-4000:] if output and status is not TestStatus.PASSED else None
        cases.append(
            TestCaseResult(
                test_id=f"ctest:{name}",
                status=status,
                duration_s=_seconds(case.get("time")),
                message=message,
                stack=stack,
            )
        )
    return cases


def parse_gtest_xml(data: bytes, source: str = "gtest xml") -> list[TestCaseResult]:
    root = _parse_xml(data, source)
    cases: list[TestCaseResult] = []
    for case in root.iter("testcase"):
        suite = case.get("classname")
        name = case.get("name")
        if not suite or not name:
            continue
        failures = case.findall("failure") + case.findall("error")
        result = (case.get("result") or "").lower()
        if failures:
            status = TestStatus.FAILED
            message = "\n".join(filter(None, (_text(f) for f in failures))) or None
        elif (
            case.find("skipped") is not None
            or result in ("skipped", "suppressed")
            or ((case.get("status") or "").lower() == "notrun")
        ):
            status = TestStatus.SKIPPED
            message = _text(case.find("skipped"))
        else:
            status = TestStatus.PASSED
            message = None
        cases.append(
            TestCaseResult(
                test_id=f"gtest:{suite}.{name}",
                status=status,
                duration_s=_seconds(case.get("time")),
                message=message,
            )
        )
    return cases


def merge_cases(groups: Iterable[Iterable[TestCaseResult]]) -> list[TestCaseResult]:
    """One result per ID; when an ID appears more than once (a GoogleTest binary run once per
    case), the most significant status wins: error, timeout, failure, pass, skip."""
    merged: dict[str, TestCaseResult] = {}
    for group in groups:
        for case in group:
            existing = merged.get(case.test_id)
            if existing is None or _STATUS_RANK[case.status] > _STATUS_RANK[existing.status]:
                merged[case.test_id] = case
    return [merged[key] for key in sorted(merged)]


def read_gtest_dir(directory: Path) -> tuple[list[TestCaseResult], list[str]]:
    """Every ``*.xml`` report in ``directory``; unreadable ones are listed as problems."""
    groups: list[list[TestCaseResult]] = []
    problems: list[str] = []
    if directory.is_dir():
        for path in sorted(directory.glob("*.xml")):
            try:
                groups.append(parse_gtest_xml(path.read_bytes(), str(path)))
            except (OSError, ReportParseError) as exc:
                problems.append(str(exc))
    return merge_cases(groups), problems


def ctest_regex(names: Iterable[str]) -> str:
    return "^(" + "|".join(re.escape(n) for n in names) + ")$"


def gtest_filter(ids: Iterable[str]) -> str:
    return ":".join(i.removeprefix("gtest:") for i in ids)


# --------------------------------------------------------------------------- CTest's listing


def _posix(path: str) -> str:
    return path.replace("\\", "/").rstrip("/")


def _relative(path: str, root: str) -> str | None:
    """``path`` under ``root``, both as CTest and the sandbox spell them; case-insensitive
    when only the case differs (Windows)."""
    path, root = _posix(path), _posix(root)
    for p, r in ((path, root), (path.casefold(), root.casefold())):
        if p.startswith(r + "/"):
            return path[len(r) + 1 :]
    return None


def parse_ctest_listing(
    text: str, *, build_dir: str, source_dir: str, targets: Sequence[BuildTarget]
) -> list[TestEntry]:
    """``ctest --show-only=json-v1``: each entry, the project target its command runs (by the
    target's artifacts, relative to ``build_dir``) and the build file that adds it (relative to
    ``source_dir``). Paths are as the sandbox spells them (D-60)."""
    try:
        data = json.loads(text.lstrip("﻿"))
    except ValueError:
        return []
    if not isinstance(data, dict):
        return []
    listing = cast(dict[str, Any], data)
    graph = cast(dict[str, Any], listing.get("backtraceGraph") or {})
    nodes = cast(list[dict[str, Any]], graph.get("nodes") or [])
    files = cast(list[str], graph.get("files") or [])
    by_artifact: dict[str, str] = {}
    for target in targets:
        for artifact in target.artifacts:
            by_artifact.setdefault(_posix(artifact), target.name)
            by_artifact.setdefault(_posix(artifact).casefold(), target.name)
    entries: list[TestEntry] = []
    for test in cast(list[dict[str, Any]], listing.get("tests") or []):
        name = test.get("name")
        if not isinstance(name, str) or not name:
            continue
        runs: str | None = None
        command = cast(list[str], test.get("command") or [])
        if command:
            rel = _relative(str(command[0]), build_dir)
            if rel is not None:
                runs = by_artifact.get(rel) or by_artifact.get(rel.casefold())
        file: str | None = None
        node = test.get("backtrace")
        if isinstance(node, int) and 0 <= node < len(nodes):
            index = nodes[node].get("file")
            if isinstance(index, int) and 0 <= index < len(files):
                rel = _relative(files[index], source_dir)
                file = posixpath.normpath(rel) if rel is not None else None
        entries.append(TestEntry(test_id=f"ctest:{name}", runs=runs, file=file))
    return sorted(entries, key=lambda e: e.test_id)
