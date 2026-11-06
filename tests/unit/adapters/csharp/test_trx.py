"""TRX reports, test IDs and selection (SPEC §11.3)."""

# ruff: noqa: E501 - real dotnet output and TRX lines, kept whole

from __future__ import annotations

from pathlib import Path

import pytest

from codecoremorph.adapters.csharp.trx import (
    parse_report,
    read_reports,
    selection_filter,
    split_id,
)
from codecoremorph.adapters.csharp.trx import test_id as make_id
from codecoremorph.core.models import TestStatus

DATA = Path(__file__).resolve().parents[3] / "data" / "csharp" / "trx"

MSTEST = """<?xml version="1.0" encoding="utf-8"?>
<TestRun xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010">
  <Results>
    <UnitTestResult testId="a" testName="Adds" outcome="Failed" duration="00:00:01.5000000">
      <Output><ErrorInfo><Message>Assert.AreEqual failed. Expected:&lt;3&gt;. Actual:&lt;4&gt;.</Message>
      <StackTrace>at Shop.Tests.CartTests.Adds() in CartTests.cs:line 12</StackTrace></ErrorInfo></Output>
    </UnitTestResult>
    <UnitTestResult testId="b" testName="Rows" outcome="Passed" duration="00:00:00.0100000">
      <InnerResults>
        <UnitTestResult testId="b" testName="Rows (1,2)" outcome="Passed" duration="00:00:00.0010000" />
        <UnitTestResult testId="b" testName="Rows (3,4)" outcome="Error" duration="00:00:00.0010000" />
      </InnerResults>
    </UnitTestResult>
    <UnitTestResult testId="c" testName="Later" outcome="NotExecuted" />
  </Results>
  <TestDefinitions>
    <UnitTest name="Adds" id="a"><TestMethod className="Shop.Tests.CartTests" name="Adds" /></UnitTest>
    <UnitTest name="Rows" id="b"><TestMethod className="Shop.Tests.CartTests" name="Rows" /></UnitTest>
    <UnitTest name="Later" id="c"><TestMethod className="Shop.Tests.CartTests" name="Later" /></UnitTest>
  </TestDefinitions>
</TestRun>
"""


def test_nunit_results_name_nested_classes_and_cases() -> None:
    cases = {c.test_id: c for c in parse_report(DATA / "nunit_net6.trx")}
    assert "dotnet:OrderBook.Tests.ReportTests#FinalStatuses(Shipped,True)" in cases
    assert all(c.status is TestStatus.PASSED for c in cases.values())
    assert all(c.message is None for c in cases.values())


def test_xunit_names_lose_their_class() -> None:
    ids = {c.test_id for c in parse_report(DATA / "xunit_netcoreapp3.1.trx")}
    assert "dotnet:Rates.Tests.RateTableTests+Parcels#AParcelWeighsSomething" in ids
    assert (
        'dotnet:Rates.Tests.QuoterTests#ExplainsWhatItCantPrice(line: "p1,500", '
        'problem: "expected reference,grams,zone")'
    ) in ids


def test_mstest_outcomes_messages_and_data_rows(tmp_path: Path) -> None:
    path = tmp_path / "r.trx"
    path.write_text(MSTEST, encoding="utf-8")
    cases = {c.test_id: c for c in parse_report(path)}
    adds = cases["dotnet:Shop.Tests.CartTests#Adds"]
    assert adds.status is TestStatus.FAILED
    assert adds.duration_s == pytest.approx(1.5)
    assert adds.message is not None
    assert "Expected:<3>" in adds.message
    assert "line 12" in adds.message
    assert cases["dotnet:Shop.Tests.CartTests#Rows (1,2)"].status is TestStatus.PASSED
    assert cases["dotnet:Shop.Tests.CartTests#Rows (3,4)"].status is TestStatus.ERROR
    assert cases["dotnet:Shop.Tests.CartTests#Later"].status is TestStatus.SKIPPED


def test_a_report_with_a_dtd_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "evil.trx"
    path.write_text(
        '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><TestRun/>', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="DTD"):
        parse_report(path)
    cases, problems = read_reports([path])
    assert cases == []
    assert problems
    assert "DTD" in problems[0]


def test_repeated_ids_are_numbered(tmp_path: Path) -> None:
    for name in ("a_net6.0.trx", "b_net8.0.trx"):
        (tmp_path / name).write_text(MSTEST, encoding="utf-8")
    cases, problems = read_reports(sorted(tmp_path.glob("*.trx")))
    ids = [c.test_id for c in cases]
    assert problems == []
    assert "dotnet:Shop.Tests.CartTests#Adds" in ids
    assert "dotnet:Shop.Tests.CartTests#Adds[2]" in ids
    assert len(ids) == len(set(ids))


def test_ids_split_and_build() -> None:
    assert make_id("Ns.Cls", "Ns.Cls.M(1)") == "dotnet:Ns.Cls#M(1)"
    assert split_id("dotnet:Ns.Outer+Inner#M") == ("Ns.Outer+Inner", "M")
    assert split_id("junit:x#y") is None


def test_a_selection_takes_whole_methods() -> None:
    chosen = selection_filter(
        [
            "dotnet:Ns.Cls#M(1,2)",
            "dotnet:Ns.Cls#M(3,4)",
            "dotnet:Ns.Outer+Inner#N",
            "dotnet:Ns.Cls#M(5)[2]",
            "gtest:Other.Case",
        ]
    )
    assert chosen == "FullyQualifiedName=Ns.Cls.M|FullyQualifiedName=Ns.Outer+Inner.N"
    assert selection_filter(["gtest:Other.Case"]) is None


def test_filter_values_are_escaped() -> None:
    assert selection_filter(["dotnet:Ns.A#B"]) == "FullyQualifiedName=Ns.A.B"
    assert "\\!" in (selection_filter(["dotnet:Ns.A!x#B"]) or "")
