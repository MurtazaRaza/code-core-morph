"""CTest JUnit and GoogleTest XML parsing (SPEC §11.1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from codecoremorph.adapters.cpp.testing import (
    ReportParseError,
    ctest_regex,
    gtest_filter,
    merge_cases,
    parse_ctest_junit,
    parse_gtest_xml,
    read_gtest_dir,
)
from codecoremorph.core.models import TestCaseResult, TestStatus

CTEST = b"""<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="(empty)" tests="5" failures="2" disabled="1" skipped="1" time="0">
  <testcase name="geo_tests" classname="geo_tests" time="0.06" status="run">
    <system-out>[==========] 23 tests ran.</system-out>
  </testcase>
  <testcase name="slow" classname="slow" time="10.0" status="fail">
    <failure message="Timeout"/>
    <system-out>still running...</system-out>
  </testcase>
  <testcase name="crashy" classname="crashy" time="0.1" status="fail">
    <failure message="Failed"/>
    <system-out>Segmentation fault</system-out>
  </testcase>
  <testcase name="off" classname="off" time="0" status="disabled">
    <skipped message="Disabled"/>
  </testcase>
  <testcase name="never" classname="never" time="0" status="notrun"/>
</testsuite>
"""

GTEST = b"""<?xml version="1.0" encoding="UTF-8"?>
<testsuites tests="4" failures="1" disabled="1" errors="0" time="0.01" name="AllTests">
  <testsuite name="VecTest" tests="4" failures="1" disabled="1" skipped="1" errors="0">
    <testcase name="Add" status="run" result="completed" time="0.002" classname="VecTest" />
    <testcase name="Sub" status="run" result="completed" time="0" classname="VecTest">
      <failure message="a.cpp:20&#x0A;Expected equality" type=""><![CDATA[a.cpp:20
Expected equality of these values]]></failure>
    </testcase>
    <testcase name="DISABLED_Mul" status="notrun" result="suppressed" time="0"
              classname="VecTest" />
    <testcase name="Div" status="run" result="skipped" time="0" classname="VecTest">
      <skipped message="not on this platform"/>
    </testcase>
  </testsuite>
  <testsuite name="Inst/ParamTest" tests="1">
    <testcase name="Works/0" status="run" result="completed" time="0" classname="Inst/ParamTest" />
  </testsuite>
</testsuites>
"""


def _statuses(cases: list[TestCaseResult]) -> dict[str, TestStatus]:
    return {c.test_id: c.status for c in cases}


def test_ctest_junit() -> None:
    cases = parse_ctest_junit(CTEST)
    assert _statuses(cases) == {
        "ctest:geo_tests": TestStatus.PASSED,
        "ctest:slow": TestStatus.TIMEOUT,
        "ctest:crashy": TestStatus.FAILED,
        "ctest:off": TestStatus.SKIPPED,
        "ctest:never": TestStatus.SKIPPED,
    }
    by_id = {c.test_id: c for c in cases}
    assert by_id["ctest:geo_tests"].duration_s == pytest.approx(0.06)
    assert by_id["ctest:geo_tests"].stack is None
    assert by_id["ctest:crashy"].stack == "Segmentation fault"


def test_gtest_xml() -> None:
    cases = parse_gtest_xml(GTEST)
    assert _statuses(cases) == {
        "gtest:VecTest.Add": TestStatus.PASSED,
        "gtest:VecTest.Sub": TestStatus.FAILED,
        "gtest:VecTest.DISABLED_Mul": TestStatus.SKIPPED,
        "gtest:VecTest.Div": TestStatus.SKIPPED,
        "gtest:Inst/ParamTest.Works/0": TestStatus.PASSED,
    }
    failed = next(c for c in cases if c.test_id == "gtest:VecTest.Sub")
    assert failed.message is not None
    assert "Expected equality" in failed.message


def test_merge_prefers_the_most_significant_status() -> None:
    def case(status: TestStatus) -> TestCaseResult:
        return TestCaseResult(test_id="gtest:A.b", status=status, duration_s=0)

    merged = merge_cases([[case(TestStatus.SKIPPED)], [case(TestStatus.PASSED)]])
    assert [c.status for c in merged] == [TestStatus.PASSED]
    merged = merge_cases([[case(TestStatus.PASSED)], [case(TestStatus.FAILED)]])
    assert [c.status for c in merged] == [TestStatus.FAILED]


def test_reports_with_a_dtd_are_refused() -> None:
    evil = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><testsuite/>'
    with pytest.raises(ReportParseError, match="DTD"):
        parse_ctest_junit(evil)
    with pytest.raises(ReportParseError):
        parse_gtest_xml(b"<not-closed>")


def test_read_gtest_dir_merges_files_and_reports_bad_ones(tmp_path: Path) -> None:
    (tmp_path / "a.xml").write_bytes(GTEST)
    (tmp_path / "a_1.xml").write_bytes(
        b'<testsuites><testsuite name="VecTest"><testcase name="DISABLED_Mul" '
        b'classname="VecTest" status="run" result="completed"/></testsuite></testsuites>'
    )
    (tmp_path / "broken.xml").write_bytes(b"<testsuites>")
    cases, problems = read_gtest_dir(tmp_path)
    assert _statuses(cases)["gtest:VecTest.DISABLED_Mul"] is TestStatus.PASSED
    assert len(problems) == 1
    assert read_gtest_dir(tmp_path / "missing") == ([], [])


def test_selection_helpers() -> None:
    assert ctest_regex(["a.b", "c+"]) == r"^(a\.b|c\+)$"
    assert gtest_filter(["gtest:A.b", "gtest:C.d"]) == "A.b:C.d"
