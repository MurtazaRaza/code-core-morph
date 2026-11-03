"""The safety invariants as pure checks (SPEC §7.10)."""

from __future__ import annotations

from pathlib import Path

from codecoremorph.core.models import (
    Boundary,
    Severity,
    SourceRange,
    Symbol,
    SymbolKind,
    TestBaseline,
    TestCaseResult,
    TestReport,
    TestStatus,
    Visibility,
)
from codecoremorph.invariants.checks import (
    InvariantOutcome,
    api_changes,
    check_api,
    check_diff_size,
    check_path_scope,
    check_suppressions,
    check_tests_kept,
    check_unit_scope,
    check_warnings,
    patch_lines,
)

PATCH = """\
diff --git a/src/a.cpp b/src/a.cpp
index 1111111..2222222 100644
--- a/src/a.cpp
+++ b/src/a.cpp
@@ -1,3 +1,3 @@
 int a() {
-    return 1;  // NOLINT(old)
+    return 2;  // NOLINT(old)
 }
diff --git a/src/b.cpp b/src/b.cpp
new file mode 100644
--- /dev/null
+++ b/src/b.cpp
@@ -0,0 +1,2 @@
+#pragma GCC diagnostic ignored "-Wunused"
+int b() { return 0; }
diff --git a/src/gone.cpp b/src/gone.cpp
deleted file mode 100644
--- a/src/gone.cpp
+++ /dev/null
@@ -1 +0,0 @@
-int gone();
"""


def report(*ids: str, status: TestStatus = TestStatus.PASSED) -> TestReport:
    return TestReport(
        cases=[TestCaseResult(test_id=i, status=status, duration_s=0) for i in ids],
        duration_s=0,
        log_path=Path("test.log"),
    )


def symbol(name: str, signature: str | None, **extra: object) -> Symbol:
    fields: dict[str, object] = {
        "id": f"c:@F@{name}#{signature}",
        "name": name.rsplit("::", 1)[-1],
        "qualified_name": name,
        "kind": SymbolKind.FUNCTION,
        "file": "include/api.h",
        "range": SourceRange(start_line=1, start_column=1, end_line=1, end_column=2),
        "signature": signature,
        "boundary": Boundary.PUBLIC_API,
    }
    return Symbol.model_validate(fields | extra)


def test_patch_lines_by_file() -> None:
    lines = patch_lines(PATCH)
    assert lines.added == {
        "src/a.cpp": ["    return 2;  // NOLINT(old)"],
        "src/b.cpp": ['#pragma GCC diagnostic ignored "-Wunused"', "int b() { return 0; }"],
    }
    assert lines.removed == {
        "src/a.cpp": ["    return 1;  // NOLINT(old)"],
        "src/gone.cpp": ["int gone();"],
    }


def test_path_scope() -> None:
    assert check_path_scope(["src/a.cpp"], excluded=lambda p: p.startswith("third_party/")).passed
    bad = check_path_scope(
        ["third_party/x.h", ".git/config", "src/ok.cpp"],
        excluded=lambda p: p.startswith("third_party/"),
    )
    assert not bad.passed
    assert bad.severity is Severity.ERROR
    assert ".git/config" in bad.message
    assert "third_party/x.h" in bad.message
    assert "src/ok.cpp" not in bad.message


def test_unit_scope() -> None:
    allowed = {"src/a.cpp", "src/a.h"}.__contains__
    assert check_unit_scope(["src/a.cpp"], allowed=allowed).passed
    result = check_unit_scope(["src/a.cpp", "src/other.cpp"], allowed=allowed)
    assert not result.passed
    assert "src/other.cpp" in result.message


def test_tests_kept_counts_and_markers() -> None:
    baseline = TestBaseline(runs=2, passing=["t:A", "t:B"], failing=["t:F"], flaky=["t:X"])
    assert check_tests_kept(baseline, report("t:A", "t:B", "t:F")).passed  # flaky may be missing
    gone = check_tests_kept(baseline, report("t:A", "t:F"))
    assert not gone.passed
    assert "t:B" in gone.message
    # A marker that only moved to another line is not new; one more is.
    before = ["tests/a_test.cpp:10: GTEST_SKIP"]
    moved = ["tests/a_test.cpp:12: GTEST_SKIP"]
    added = [*moved, "tests/a_test.cpp:30: disabled test Geo.DISABLED_Area"]
    ok = report("t:A", "t:B", "t:F")
    assert check_tests_kept(baseline, ok, markers_before=before, markers_after=moved).passed
    disabled = check_tests_kept(baseline, ok, markers_before=before, markers_after=added)
    assert not disabled.passed
    assert "DISABLED_Area" in disabled.message


def test_api_changes_ignore_moves_and_additions() -> None:
    old = [symbol("geo::area", "double geo::area(double, double)")]
    same = [symbol("geo::area", "double geo::area(double, double)", file="include/other.h")]
    assert api_changes(old, same, files=["include/api.h"]) == []
    added = [*old, symbol("geo::perimeter", "double geo::perimeter(double)")]
    assert api_changes(old, added, files=["include/api.h"]) == []
    changed = [symbol("geo::area", "float geo::area(float, float)")]
    (change,) = api_changes(old, changed, files=["include/api.h"])
    assert "changed from `double geo::area(double, double)`" in change.describe()
    (removed,) = api_changes(old, [], files=["include/api.h"])
    assert "was removed" in removed.describe()


def test_api_changes_skip_private_members_and_macros() -> None:
    private = symbol(
        "geo::Cache::index_", "std::map<int, Node *> index_", visibility=Visibility.PRIVATE
    )
    macro = symbol("DECLARE", None, kind=SymbolKind.MACRO)
    assert api_changes([private, macro], [], files=["include/api.h"]) == []


def test_api_change_needs_every_side_or_approval() -> None:
    old = [symbol("geo::area", "double geo::area(double, double)")]
    (change,) = api_changes(old, [], files=["include/api.h"])
    kept = check_api([change], api_changing=True, covered=lambda _c: True, gated=False)
    assert kept.passed
    blocked = check_api([change], api_changing=False, covered=lambda _c: True, gated=False)
    assert not blocked.passed
    assert blocked.severity is Severity.ERROR
    gated = check_api([change], api_changing=False, covered=lambda _c: True, gated=True)
    assert not gated.passed
    assert gated.needs_approval
    assert gated.severity is Severity.WARNING
    uncovered = check_api([change], api_changing=True, covered=lambda _c: False, gated=False)
    assert not uncovered.passed


def test_a_c_abi_change_is_never_covered() -> None:
    old = [symbol("make_widget", "widget * make_widget(int)", boundary=Boundary.C_ABI)]
    (change,) = api_changes(old, [], files=["include/api.h"])
    result = check_api([change], api_changing=True, covered=lambda _c: True, gated=False)
    assert not result.passed


def test_suppressions() -> None:
    lines = patch_lines(PATCH)
    found = check_suppressions(lines, allowed=False)
    assert not found.passed
    assert "#pragma diagnostic ignored in src/b.cpp" in found.message
    assert "NOLINT" not in found.message  # only edited on its line, not added
    assert check_suppressions(lines, allowed=True).passed


def test_diff_size() -> None:
    assert check_diff_size(800, limit=800).passed
    big = check_diff_size(801, limit=800)
    assert not big.passed
    assert "801" in big.message


def test_warnings_warn_or_fail() -> None:
    before = {"src/a.cpp": 1}
    after = {"src/a.cpp": 2, "src/b.cpp": 0}
    flagged = check_warnings(before, after, files=["src/a.cpp", "src/b.cpp"], fail=False)
    assert not flagged.passed
    assert flagged.severity is Severity.WARNING
    assert "src/a.cpp (1 -> 2)" in flagged.message
    failed = check_warnings(before, after, files=["src/a.cpp"], fail=True)
    assert failed.severity is Severity.ERROR
    assert check_warnings(before, {"src/a.cpp": 1}, files=["src/a.cpp"], fail=True).passed


def test_outcome_sorts_errors_warnings_and_approvals() -> None:
    old = [symbol("geo::area", "double geo::area(double, double)")]
    (change,) = api_changes(old, [], files=["include/api.h"])
    outcome = InvariantOutcome(
        [
            check_unit_scope(["x.cpp"], allowed=lambda _p: False),
            check_warnings({}, {"a.cpp": 1}, files=["a.cpp"], fail=False),
            check_api([change], api_changing=False, covered=lambda _c: True, gated=True),
        ]
    )
    assert not outcome.ok
    assert [r.invariant for r in outcome.errors] == ["I-2"]
    assert outcome.warnings[0].startswith("I-10: ")
    assert outcome.approvals[0].startswith("I-6: ")
    assert outcome.feedback().startswith("The changes break CodeCoreMorph's safety rules:")
