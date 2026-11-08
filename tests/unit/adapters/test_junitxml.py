"""JUnit XML reports, shared by the adapters whose test frameworks write them."""

from __future__ import annotations

from pathlib import Path

import pytest

from codecoremorph.adapters.junitxml import ReportError, parse_report, read_reports
from codecoremorph.core.models import TestStatus

# As cmocka writes it (CMOCKA_MESSAGE_OUTPUT=xml): suites without a classname on their cases.
CMOCKA = b"""<?xml version="1.0" encoding="UTF-8" ?>
<testsuites>
  <testsuite name="htab" time="0.000" tests="3" failures="1" errors="0" skipped="1" >
    <testcase name="grows" time="0.002" >
    </testcase>
    <testcase name="finds" time="0.00" >
      <failure><![CDATA[0 != 1
htab_test.c:22: error: Failure!]]></failure>
    </testcase>
    <testcase name="later" time="0.00" >
      <skipped/>
    </testcase>
  </testsuite>
</testsuites>
"""


def test_a_cmocka_report() -> None:
    grows, finds, later = parse_report(CMOCKA, "htab.xml")
    assert (grows.test_id, grows.status, grows.duration_s) == (
        "junit:htab#grows",
        TestStatus.PASSED,
        0.002,
    )
    assert (finds.test_id, finds.status) == ("junit:htab#finds", TestStatus.FAILED)
    assert finds.message == "0 != 1"
    assert finds.stack is not None
    assert "htab_test.c:22" in finds.stack
    assert (later.test_id, later.status) == ("junit:htab#later", TestStatus.SKIPPED)


def test_a_single_suite_with_classnames() -> None:
    report = b"""<testsuite name="all"><testcase classname="Sbuf" name="appends"/>
        <testcase classname="Sbuf" name="grows"><error message="crashed"/></testcase>
        </testsuite>"""
    appends, grows = parse_report(report, "r.xml")
    assert appends.test_id == "junit:Sbuf#appends"
    assert (grows.status, grows.message) == (TestStatus.ERROR, "crashed")


@pytest.mark.parametrize(
    ("data", "problem"),
    [
        (b'<!DOCTYPE x [<!ENTITY a "b">]><testsuite/>', "must not contain a DTD"),
        (b"<testsuite>", "not XML"),
        (b"<results><test/></results>", "not a JUnit report (its root is <results>)"),
    ],
)
def test_what_is_refused(data: bytes, problem: str) -> None:
    with pytest.raises(ReportError, match=problem.replace("(", r"\(").replace(")", r"\)")):
        parse_report(data, "r.xml")


def test_reports_merge_and_a_failure_wins(tmp_path: Path) -> None:
    first = tmp_path / "a.xml"
    first.write_bytes(b'<testsuite name="s"><testcase name="t"/></testsuite>')
    second = tmp_path / "b.xml"
    second.write_bytes(b'<testsuite name="s"><testcase name="t"><failure/></testcase></testsuite>')
    broken = tmp_path / "c.xml"
    broken.write_bytes(b"not xml")
    cases, problems = read_reports([first, second, broken])
    assert [(c.test_id, c.status) for c in cases] == [("junit:s#t", TestStatus.FAILED)]
    [problem] = problems
    assert "c.xml: not XML" in problem
