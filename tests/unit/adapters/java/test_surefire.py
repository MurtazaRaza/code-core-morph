"""Surefire reports and test selection (SPEC §11.2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from codecoremorph.adapters.java.surefire import (
    ReportError,
    parse_report,
    read_reports,
    selection_argument,
    split_id,
)
from codecoremorph.adapters.java.surefire import test_id as make_id
from codecoremorph.core.models import TestStatus

DATA = Path(__file__).resolve().parents[3] / "data" / "java" / "maven"

REPORT = b"""<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="org.example.CodecTest" tests="4">
  <testcase name="passes" classname="org.example.CodecTest" time="0.012"/>
  <testcase name="fails" classname="org.example.CodecTest" time="0.001">
    <failure message="expected:&lt;2&gt; but was:&lt;1&gt;"
      type="java.lang.AssertionError">java.lang.AssertionError: expected:&lt;2&gt;
	at org.example.CodecTest.fails(CodecTest.java:21)</failure>
  </testcase>
  <testcase name="breaks" classname="org.example.CodecTest" time="0">
    <error type="java.lang.NullPointerException">java.lang.NullPointerException</error>
  </testcase>
  <testcase name="ignored" classname="org.example.CodecTest" time="0">
    <skipped message="flaky"/>
  </testcase>
</testsuite>
"""


def test_a_report_gives_each_case_its_status() -> None:
    cases = {c.test_id: c for c in parse_report(REPORT, "TEST-x.xml")}
    assert cases["junit:org.example.CodecTest#passes"].status is TestStatus.PASSED
    assert cases["junit:org.example.CodecTest#passes"].duration_s == pytest.approx(0.012)
    failed = cases["junit:org.example.CodecTest#fails"]
    assert failed.status is TestStatus.FAILED
    assert failed.message == "expected:<2> but was:<1>"
    assert failed.stack is not None
    assert "CodecTest.java:21" in failed.stack
    errored = cases["junit:org.example.CodecTest#breaks"]
    assert (errored.status, errored.message) == (TestStatus.ERROR, "java.lang.NullPointerException")
    assert cases["junit:org.example.CodecTest#ignored"].status is TestStatus.SKIPPED


def test_junit5_names_parameterized_and_nested_tests_as_surefire_does(tmp_path: Path) -> None:
    for name in ("surefire_junit5_parameterized.xml", "surefire_junit5_nested.xml"):
        (tmp_path / f"TEST-{name}").write_bytes((DATA / name).read_bytes())
    cases, problems = read_reports([tmp_path])
    ids = [c.test_id for c in cases]
    assert problems == []
    assert "junit:org.example.tasks.EffortTest#describesEstimates(int, String)[7]" in ids
    assert "junit:org.example.tasks.TaskQueueTest$WhenEmpty#hasNoNext" in ids
    assert "junit:org.example.tasks.TaskQueueTest#refusesABadPriority" in ids
    assert len(ids) == len(set(ids))  # the nested class's report repeats the outer cases


def test_a_report_with_a_dtd_or_no_xml_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ReportError, match="DTD"):
        parse_report(b'<!DOCTYPE x [<!ENTITY a "b">]><testsuite/>', "TEST-a.xml")
    (tmp_path / "TEST-b.xml").write_bytes(b"not xml")
    cases, problems = read_reports([tmp_path, tmp_path / "missing"])
    assert cases == []
    assert problems[0].startswith("TEST-b.xml: not XML")


def test_a_failure_outranks_a_pass_of_the_same_id(tmp_path: Path) -> None:
    (tmp_path / "TEST-a.xml").write_bytes(
        b'<testsuite><testcase name="t" classname="C" time="0"/></testsuite>'
    )
    (tmp_path / "TEST-b.xml").write_bytes(
        b'<testsuite><testcase name="t" classname="C"><failure message="no"/></testcase>'
        b"</testsuite>"
    )
    [case], _ = read_reports([tmp_path])
    assert case.status is TestStatus.FAILED


def test_ids_and_selection() -> None:
    assert make_id("a.B$C", "m") == "junit:a.B$C#m"
    assert split_id("junit:a.B#m") == ("a.B", "m")
    assert split_id("gtest:A.B") is None
    chosen = selection_argument(
        [
            "junit:a.B#one",
            "junit:a.B#two(int)[3]",
            "junit:a.B#two(int)[4]",
            "junit:a.C$Inner#x",
            "gtest:Other.Case",
        ]
    )
    assert chosen == "a.B#one+two,a.C$Inner#x"
    assert selection_argument(["ctest:x"]) is None
