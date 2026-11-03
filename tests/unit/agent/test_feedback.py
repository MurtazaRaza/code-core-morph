"""Feedback formatting, against golden files in tests/data/feedback/ (FR-4.2).

Regenerate a golden file after an intended change by deleting it and running the test once.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codecoremorph.agent.context import build_context, fenced
from codecoremorph.agent.feedback import (
    MAX_ITEMS,
    compiler_feedback,
    order_diagnostics,
    regression_feedback,
    scrub_paths,
    sites_feedback,
)
from codecoremorph.core.errors import ContextOverflowError
from codecoremorph.core.models import (
    Diagnostic,
    DiagnosticOrigin,
    RuleSite,
    Severity,
    TestCaseResult,
    TestStatus,
    WorkUnit,
)

GOLDEN = Path(__file__).resolve().parents[2] / "data" / "feedback"


def golden(name: str, text: str) -> None:
    path = GOLDEN / name
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
        pytest.fail(f"wrote the new golden file {path}; check it and run again")
    assert text == path.read_bytes().decode("utf-8").replace("\r\n", "\n")


def diag(
    path: str | None,
    line: int | None,
    message: str,
    *,
    code: str | None = None,
    severity: Severity = Severity.ERROR,
    notes: list[Diagnostic] | None = None,
) -> Diagnostic:
    return Diagnostic(
        file_path=path,
        line=line,
        column=5 if line else None,
        severity=severity,
        code=code,
        message=message,
        notes=notes or [],
        origin=DiagnosticOrigin.COMPILER if path else DiagnosticOrigin.LINKER,
    )


@pytest.fixture
def worktree(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.cpp").write_bytes(
        b"".join(f"line {i}\r\n".encode() for i in range(1, 11))
    )
    (tmp_path / "src" / "b.cpp").write_text("int b() { return a(); }\n")
    return tmp_path


def test_ordering_dedupes_and_puts_first_errors_first() -> None:
    a1 = diag("src/a.cpp", 2, "first in a")
    a2 = diag("src/a.cpp", 7, "cascade in a")
    b1 = diag("src/b.cpp", 1, "first in b")
    warn = diag("src/a.cpp", 3, "unused", severity=Severity.WARNING, code="-Wunused")
    note = diag("src/a.cpp", 1, "a note", severity=Severity.NOTE)
    ordered = order_diagnostics([warn, a1, a2, a1, b1, note])
    assert [d.message for d in ordered] == ["first in a", "first in b", "cascade in a", "unused"]


def test_ordering_doesnt_depend_on_the_build_order() -> None:
    """A parallel build prints each compile's errors as it finishes: an error in a shared
    header comes from either compile, with its own "included from" note."""
    header = "include/h.h"
    via_a = diag(header, 3, "bad", notes=[diag("src/a.cpp", 1, "included from here",
                                                severity=Severity.NOTE)])  # fmt: skip
    via_b = diag(header, 3, "bad", notes=[diag("src/b.cpp", 1, "included from here",
                                                severity=Severity.NOTE)])  # fmt: skip
    in_b = diag("src/b.cpp", 2, "first in b")
    in_a = diag("src/a.cpp", 5, "first in a")
    one = order_diagnostics([via_b, in_b, via_a, in_a])
    other = order_diagnostics([in_a, via_a, in_b, via_b])
    assert one == other
    assert [d.file_path for d in one] == [header, "src/a.cpp", "src/b.cpp"]
    assert one[0].notes[0].file_path == "src/a.cpp"  # the same representative every time


def test_compiler_feedback_golden(worktree: Path) -> None:
    diagnostics = [
        diag("src/a.cpp", 4, "no matching function for call to 'make'", code=None,
             notes=[diag("src/a.cpp", 9, "candidate function not viable",
                         severity=Severity.NOTE)]),
        diag("src/a.cpp", 4, "no matching function for call to 'make'"),  # duplicate
        diag("src/a.cpp", 3, "unused variable 'x'", code="-Wunused-variable",
             severity=Severity.WARNING),
        diag("src/b.cpp", 1, "use of undeclared identifier 'a'"),
        diag("src/c.cpp", 2, "warning outside the unit", severity=Severity.WARNING),
        diag(None, None, "undefined reference to `geo::area()'", code="undefined-reference"),
    ]  # fmt: skip
    text = compiler_feedback(
        diagnostics, unit_files=["src/a.cpp"], worktree=worktree, changed_files=["src/a.cpp"]
    )
    golden("compiler.md", text)


def test_compiler_feedback_caps_and_counts(worktree: Path) -> None:
    many = [diag(f"src/f{i:02d}.cpp", 1, f"error {i}") for i in range(30)]
    text = compiler_feedback(many, unit_files=[], worktree=worktree)
    assert text.startswith(f"The build failed with 30 errors. Showing the first {MAX_ITEMS} of 30")
    assert "error 19" in text
    assert "error 20" not in text
    empty = compiler_feedback([], unit_files=[], worktree=worktree, step="configure")
    assert empty == "The configure failed without a diagnostic CodeCoreMorph could read."


def test_regression_and_sites_feedback_golden() -> None:
    cases = [
        TestCaseResult(test_id="gtest:Geo.Area", status=TestStatus.FAILED, duration_s=0.1,
                       message="Expected: 4\n  Actual: 5", stack="geo_test.cpp:12"),
        TestCaseResult(test_id="ctest:geo_test", status=TestStatus.TIMEOUT, duration_s=60),
    ]  # fmt: skip
    text = regression_feedback(cases, missing=["gtest:Geo.Removed"])
    sites = [RuleSite(file="include/a.h", line=3, column=1, text="typedef int Id;")]
    golden("regressions.md", text + "\n\n---\n\n" + sites_feedback("typedefs", sites))


def test_files_are_fenced_safely() -> None:
    text = fenced("doc/readme.md", "```cpp\nint a;\n```\n")
    assert text.startswith("### doc/readme.md\n\n````\n")
    assert text.endswith("\n````")
    assert fenced("src/a.cpp", "int a;").splitlines()[2] == "```cpp"


def test_an_oversized_context_is_an_error_not_a_truncation(worktree: Path) -> None:
    unit = WorkUnit(id="U-0007", seq=7, rules=["r"], files=["src/a.cpp"])
    with pytest.raises(ContextOverflowError, match="U-0007 needs about"):
        build_context(
            unit,
            [],
            worktree=worktree,
            index_path=None,
            language="cpp",
            source="cpp11",
            target="cpp20",
            codemods=None,
            remaining={},
            build=None,
            tools=[],
            token_budget=100,
        )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "note: E:/codecoremorph-home/toolchains/llvm/include/c++/v1/__functional/"
            "unary_function.h:46:1: declared here",
            "note: <system>/__functional/unary_function.h:46:1: declared here",
        ),
        (
            "In file included from /usr/include/c++/11/bits/stl_function.h:60:",
            "In file included from <system>/bits/stl_function.h:60:",
        ),
        (r"C:\Users\bob\proj\x\y.h:3: error", r"<system>/x\y.h:3: error"),
        ("src/a.cpp:1: relative paths stay", "src/a.cpp:1: relative paths stay"),
        ("ratio 3/4 and a/b stay", "ratio 3/4 and a/b stay"),
    ],
)
def test_absolute_paths_are_scrubbed(text: str, expected: str) -> None:
    assert scrub_paths(text) == expected


def test_worktree_paths_become_relative() -> None:
    text = r"/workspace/tests/a_test.cpp:27: Failure; E:\wt\run\src\b.cpp:3: x"
    assert (
        scrub_paths(text, ["/workspace", r"E:\wt\run"])
        == r"tests/a_test.cpp:27: Failure; src\b.cpp:3: x"
    )
