"""Host units from the plan, rule skip lists and the standard a session builds at (D-42)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from codecoremorph.adapters.base import AdapterContext
from codecoremorph.agent.host import HostFiles
from codecoremorph.config.models import MigrationConfig
from codecoremorph.core.errors import UsageError
from codecoremorph.core.models import (
    FileEntry,
    FileRole,
    HostStatus,
    HostUnitState,
    LanguageConfig,
    LoadedRule,
    Plan,
    Rule,
    RuleKind,
    SkipCondition,
    UnitFlag,
    UnitStatus,
    WorkUnit,
    owned_files,
)
from codecoremorph.orchestrator.session import build_standard
from codecoremorph.orchestrator.unit import HostRun, skipped_files
from codecoremorph.store.layout import StateDir

COMMIT = "0" * 40


def _plan(root: Path, run_id: str, units: list[WorkUnit]) -> None:
    plan = Plan(language="cpp", source_standard="cpp11", target_standard="cpp20", units=units)
    run = StateDir.for_repo(root).run(run_id).ensure()
    run.plan_json.write_text(plan.model_dump_json(indent=2), encoding="utf-8")


def _units() -> list[WorkUnit]:
    return [
        WorkUnit(id="U-0001", seq=1, rules=["bump_standard"], files=["CMakeLists.txt"]),
        WorkUnit(
            id="U-0002",
            seq=2,
            rules=["convert_raw_pointers_to_smart_pointers"],
            files=["db/db_impl.cc", "db/db_impl.h", "util/status.cc"],
            reference_files=["util/status.cc"],
        ),
        WorkUnit(
            id="U-0003",
            seq=3,
            rules=["replace_typedefs_with_using"],
            files=["include/leveldb/c.h"],
            flags=[UnitFlag.C_COMPATIBLE],
            status=UnitStatus.SKIPPED,
            note="skipped: replace_typedefs_with_using doesn't apply to headers shared with C",
        ),
    ]


def test_plan_units_come_from_the_latest_plan(tmp_path: Path) -> None:
    run = HostRun(tmp_path, StateDir.for_repo(tmp_path))
    with pytest.raises(UsageError, match="no plan yet"):
        run.plan()
    _plan(tmp_path, "20260101-000000-aaaa", _units()[:1])
    _plan(tmp_path, "20260102-000000-bbbb", _units())
    path, plan = run.plan()
    assert path.parent.name == "20260102-000000-bbbb"
    assert len(plan.units) == 3
    unit = run.plan_unit("u-0002")
    assert unit.reference_files == ["util/status.cc"]
    assert owned_files(unit) == ["db/db_impl.cc", "db/db_impl.h"]


def test_a_skipped_plan_unit_is_refused(tmp_path: Path) -> None:
    """Regression: the agent started the plan's skipped unit for the C header leveldb/c.h."""
    _plan(tmp_path, "20260102-000000-bbbb", _units())
    run = HostRun(tmp_path, StateDir.for_repo(tmp_path))
    with pytest.raises(UsageError, match="U-0003 is skipped") as caught:
        run.plan_unit("U-0003")
    assert caught.value.hint is not None
    assert "next pending unit" in caught.value.hint
    assert caught.value.reason == (
        "replace_typedefs_with_using doesn't apply to headers shared with C"
    )
    with pytest.raises(UsageError, match="no unit U-0009"):
        run.plan_unit("U-0009")
    # A hand-picked unit is checked against the plan before any build.
    run.refuse_skipped(["replace_typedefs_with_using"], ["db/db_impl.cc"])
    run.refuse_skipped(["bump_standard"], ["include/leveldb/c.h"])
    with pytest.raises(UsageError, match=r"the plan skips include/leveldb/c\.h"):
        run.refuse_skipped(["replace_typedefs_with_using"], ["include/leveldb/c.h", "a.h"])
    HostRun(tmp_path / "none", StateDir.for_repo(tmp_path / "none")).refuse_skipped(["x"], ["y"])


def _state(run_id: str, plan_unit: str | None, status: HostStatus, minute: int) -> HostUnitState:
    at = datetime(2026, 1, 1, 0, minute, tzinfo=UTC)
    return HostUnitState(
        run_id=run_id,
        config_path="/p/migration_config.yaml",
        worktree="/w",
        sandbox="docker",
        base_commit=COMMIT,
        unit=WorkUnit(id="U-0001", seq=1, rules=["bump_standard"], files=["CMakeLists.txt"]),
        source="cpp11",
        target="cpp20",
        snapshot=COMMIT,
        head=COMMIT,
        plan_unit=plan_unit,
        configured_standard="cpp20",
        max_retry_budget=2,
        max_edit_retries=1,
        status=status,
        commit="1" * 40 if status is HostStatus.SUCCEEDED else None,
        started_at=at,
        updated_at=at,
    )


def test_progress_is_the_last_run_of_each_plan_unit(tmp_path: Path) -> None:
    state_dir = StateDir.for_repo(tmp_path)
    runs = [
        _state("20260101-000100-aaaa", "U-0001", HostStatus.FAILED, 1),
        _state("20260101-000200-bbbb", "U-0001", HostStatus.SUCCEEDED, 2),
        _state("20260101-000300-cccc", "U-0002", HostStatus.ABORTED, 3),
        _state("20260101-000400-dddd", None, HostStatus.SUCCEEDED, 4),
    ]
    for state in runs:
        HostFiles(state_dir.run(state.run_id).ensure().root).save(state)
    progress = HostRun(tmp_path, state_dir).plan_progress()
    assert {k: (v.run_id, v.status) for k, v in progress.items()} == {
        "U-0001": ("20260101-000200-bbbb", HostStatus.SUCCEEDED),
        "U-0002": ("20260101-000300-cccc", HostStatus.ABORTED),
    }


def _entry(path: str, *, c_compatible: bool = False, role: FileRole = FileRole.HEADER) -> FileEntry:
    return FileEntry(
        path=path, language="cpp", sha256="a" * 64, role=role, c_compatible=c_compatible
    )


def test_unit_start_refuses_files_the_rule_skips() -> None:
    rule = LoadedRule(
        rule=Rule(
            id="replace_typedefs_with_using",
            language="cpp",
            kind=RuleKind.API_PRESERVING,
            description="typedef to using",
            skip=[SkipCondition.C_COMPATIBLE],
        ),
        pack="test",
        source=Path("r.yaml"),
    )
    entries = {
        "include/leveldb/c.h": _entry("include/leveldb/c.h", c_compatible=True),
        "db/dbformat.h": _entry("db/dbformat.h"),
    }
    skipped_files([rule], entries, ["db/dbformat.h"])
    with pytest.raises(UsageError, match=r"headers shared with C.*include/leveldb/c\.h"):
        skipped_files([rule], entries, ["db/dbformat.h", "include/leveldb/c.h"])


class _Adapter:
    """Only what build_standard uses."""

    def __init__(self, found: str | None):
        self.found = found

    def project_standard(self, ctx: AdapterContext) -> str | None:
        return self.found

    def capabilities(self) -> object:
        class _Caps:
            standards = ("cpp98", "cpp03", "cpp11", "cpp14", "cpp17", "cpp20", "cpp23")

        return _Caps()


def _config() -> MigrationConfig:
    return MigrationConfig.model_validate(
        {
            "version": "1.0",
            "project": {"name": "x"},
            "migration": {
                "source": {"language": "cpp", "standard": "cpp11"},
                "target": {"language": "cpp", "standard": "cpp20"},
            },
        }
    )


@pytest.mark.parametrize(
    ("found", "expected"),
    [(None, "cpp11"), ("cpp98", "cpp11"), ("cpp11", "cpp11"), ("cpp20", "cpp20"),
     ("cpp17", "cpp17"), ("cpp26", "cpp11")],
)  # fmt: skip
def test_a_session_builds_at_the_standard_its_base_already_has(
    found: str | None, expected: str
) -> None:
    """Regression: units started from the bump's branch were configured with
    -DCMAKE_CXX_STANDARD=11, so they were verified as C++11."""
    ctx = AdapterContext(
        worktree=Path(),
        build_dir=Path(),
        cache_dir=Path(),
        log_dir=Path(),
        executor=None,  # type: ignore[arg-type]  # never runs a command
        lang=LanguageConfig(language="cpp", standard="cpp11"),
    )
    adapter = _Adapter(found)
    assert build_standard(adapter, ctx, _config()) == expected  # type: ignore[arg-type]
