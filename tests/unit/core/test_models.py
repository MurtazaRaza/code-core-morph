from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from codecoremorph.core.models import (
    Attempt,
    AttemptOutcome,
    BuildResult,
    Diagnostic,
    DiagnosticOrigin,
    EditBatch,
    Event,
    EventType,
    InvariantResult,
    Plan,
    ReplaceEdit,
    Role,
    RunStatus,
    Severity,
    TestCaseResult,
    TestReport,
    TestStatus,
    UnitFlag,
    UnitStatus,
    WorktreePath,
    WorkUnit,
)

RUN = "20261102-141500-a3f9"
NOW = datetime(2026, 11, 2, 14, 15, tzinfo=UTC)
_path = TypeAdapter(WorktreePath)


@pytest.mark.parametrize("good", ["a.cpp", "src/geometry/vector.h", ".clang-tidy", "a/.gitignore"])
def test_worktree_path_accepts(good: str) -> None:
    assert _path.validate_python(good) == good


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "/etc/passwd",
        "C:/Windows/x",
        "c:relative",
        "src\\a.cpp",
        "../outside",
        "src/../../x",
        "./src/a.cpp",
        "src//a.cpp",
        "src/",
        ".git/config",
        "sub/.GIT/hooks/pre-commit",
        "a\x00b",
    ],
)
def test_worktree_path_rejects(bad: str) -> None:
    with pytest.raises(ValidationError):
        _path.validate_python(bad)


def test_edit_batch_parses_each_op() -> None:
    batch = EditBatch.model_validate(
        {
            "edits": [
                {"op": "replace", "path": "src/a.cpp", "search": "int*", "replace": "auto"},
                {"op": "create", "path": "src/b.h", "content": "#pragma once\n"},
                {"op": "delete", "path": "src/legacy.h"},
                {"op": "replace", "path": "src/a.cpp", "search": "x", "replace": "y"},
            ],
            "summary": "s",
            "review_notes": ["note"],
        }
    )
    assert [type(e).__name__ for e in batch.edits] == [
        "ReplaceEdit",
        "CreateEdit",
        "DeleteEdit",
        "ReplaceEdit",
    ]
    assert batch.paths == ["src/a.cpp", "src/b.h", "src/legacy.h"]
    assert EditBatch.model_validate_json(batch.model_dump_json()) == batch


@pytest.mark.parametrize(
    "payload",
    [
        {"summary": "s"},
        {"edits": [{"op": "rename", "path": "a"}], "summary": "s"},
        {"edits": [{"op": "replace", "path": "a", "search": "", "replace": "y"}], "summary": "s"},
        {"edits": [{"op": "delete", "path": "../a"}], "summary": "s"},
        {"edits": [{"op": "delete", "path": "a", "extra": 1}], "summary": "s"},
        {"edits": [{"op": "delete", "path": "a"}], "summary": ""},
    ],
)
def test_edit_batch_rejects(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        EditBatch.model_validate(payload)


def test_an_empty_edit_batch_says_the_unit_is_done() -> None:
    # The agent judged what is left fine as it is (D-64); it is verified like any other.
    batch = EditBatch.model_validate({"edits": [], "summary": "nothing safe to change"})
    assert batch.edits == []
    assert batch.paths == []


def test_models_are_immutable() -> None:
    edit = ReplaceEdit(path="a", search="x", replace="y")
    with pytest.raises(ValidationError):
        edit.path = "b"  # type: ignore[misc]


def test_diagnostic_round_trip_with_notes() -> None:
    note = Diagnostic(
        file_path="a.h",
        line=3,
        severity=Severity.NOTE,
        message="here",
        origin=DiagnosticOrigin.COMPILER,
    )
    diag = Diagnostic(
        file_path="a.cpp",
        line=10,
        column=5,
        severity=Severity.ERROR,
        code="-Wfoo",
        message="bad",
        notes=[note],
        origin=DiagnosticOrigin.COMPILER,
    )
    assert Diagnostic.model_validate_json(diag.model_dump_json()) == diag


def test_build_result_errors_and_warnings() -> None:
    def diag(severity: str) -> Diagnostic:
        return Diagnostic.model_validate(
            {"file_path": None, "severity": severity, "message": "m", "origin": "linker"}
        )

    result = BuildResult(
        success=False,
        diagnostics=[diag("error"), diag("warning"), diag("note")],
        duration_s=1.5,
        log_path=Path("build.log"),
    )
    assert len(result.errors) == 1
    assert len(result.warnings) == 1


def test_test_report() -> None:
    report = TestReport(
        cases=[
            TestCaseResult(test_id="gtest:VectorTest.Add", status=TestStatus.PASSED, duration_s=0),
            TestCaseResult(test_id="ctest:smoke", status=TestStatus.FAILED, duration_s=1),
        ],
        duration_s=1,
        log_path=Path("test.log"),
    )
    assert report.ids_with_status(TestStatus.PASSED) == {"gtest:VectorTest.Add"}
    with pytest.raises(ValidationError):
        TestCaseResult(test_id="VectorTest.Add", status=TestStatus.PASSED, duration_s=0)


def _unit(seq: int, flags: list[UnitFlag] | None = None) -> WorkUnit:
    return WorkUnit(
        id=f"U-{seq:04d}",
        seq=seq,
        rules=["replace_typedefs_with_using"],
        files=["src/a.h"],
        flags=flags or [],
    )


def test_unit_flags_are_canonical() -> None:
    unit = _unit(1, [UnitFlag.TOUCHES_TESTS, UnitFlag.BOUNDARY, UnitFlag.BOUNDARY])
    assert unit.flags == [UnitFlag.BOUNDARY, UnitFlag.TOUCHES_TESTS]


def test_plan_requires_unique_ordered_units() -> None:
    plan = Plan(units=[_unit(1), _unit(2)])
    assert plan.unit("U-0002").seq == 2
    with pytest.raises(ValidationError, match="strictly increasing"):
        Plan(units=[_unit(2), _unit(1)])
    with pytest.raises(ValidationError, match="unique"):
        Plan(units=[_unit(1), _unit(1)])


def test_plan_serialization_is_deterministic() -> None:
    a = Plan(units=[_unit(1, [UnitFlag.PUBLIC_API, UnitFlag.BOUNDARY])])
    b = Plan(units=[_unit(1, [UnitFlag.BOUNDARY, UnitFlag.PUBLIC_API])])
    assert a.model_dump_json() == b.model_dump_json()


def test_terminal_statuses() -> None:
    assert RunStatus.COMPLETED.is_terminal
    assert not RunStatus.MIGRATING.is_terminal
    assert UnitStatus.TOO_LARGE.is_terminal
    assert not UnitStatus.AWAITING_APPROVAL.is_terminal


def test_attempt_round_trip() -> None:
    attempt = Attempt(
        run_id=RUN,
        unit_id="U-0001",
        number=2,
        role=Role.FIXER,
        model="anthropic/claude-opus-5-5",
        started_at=NOW,
        finished_at=NOW,
        outcome=AttemptOutcome.TESTS_FAILED,
        changed_files=["src/a.cpp"],
        regressions=["gtest:A.B"],
        invariant_results=[InvariantResult(invariant="I-5", passed=False, message="regressed")],
    )
    assert Attempt.model_validate_json(attempt.model_dump_json()) == attempt


def test_attempt_rejects_naive_times() -> None:
    with pytest.raises(ValidationError):
        Attempt(
            run_id=RUN,
            unit_id="U-0001",
            number=1,
            role=Role.EDITOR,
            started_at=datetime(2026, 11, 2),
        )


def test_event_round_trip() -> None:
    event = Event.now(RUN, EventType.UNIT_STATUS, unit_id="U-0003", data={"status": "failed"})
    assert event.ts.tzinfo is not None
    assert Event.model_validate_json(event.model_dump_json()) == event
    with pytest.raises(ValidationError):
        Event.now("not-a-run", EventType.LOG)
