"""The dashboard's two sides of a module port's unit (D-58): the C++ as it was before the unit,
the Rust the unit committed."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from codecoremorph.core.errors import NotFoundError
from codecoremorph.core.models import PortUnit, UnitResult, UnitStatus, WorkUnit
from codecoremorph.dashboard import data
from codecoremorph.dashboard.data import DashboardContext
from codecoremorph.dashboard.models import UnitGate, UnitView
from codecoremorph.orchestrator.lifecycle import RunPlace
from codecoremorph.store.layout import StateDir

GitRunner = Callable[..., str]
RUN = "20261104-120000-ab12"


def _view(unit: WorkUnit, commit: str | None, changed: list[str]) -> UnitView:
    now = datetime.now(UTC)
    result = (
        UnitResult(
            unit_id=unit.id,
            status=UnitStatus.COMMITTED,
            commit=commit,
            changed_files=changed,
            started_at=now,
            finished_at=now,
            duration_s=1.0,
        )
        if commit
        else None
    )
    return UnitView(
        unit=unit,
        result=result,
        attempts=[],
        attempt_files=[],
        gate=UnitGate(waiting=False, reasons=[], commit=commit, pending=None),
    )


def test_the_two_sides(tmp_path: Path, git: GitRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "a.cpp").write_bytes(b"int a() { return 1 + 1; }\n")
    (repo / "CMakeLists.txt").write_bytes(b"add_library(m src/a.cpp)\n")
    git(tmp_path, "init", "--quiet", str(repo))
    git(repo, "add", "--all")
    git(repo, "commit", "--quiet", "-m", "legacy")
    (repo / "src" / "a.cpp").write_bytes(b"int a() { return m_rs_a(); }\n")
    for rel, text in {
        "rust/m_rs/src/a.rs": "pub fn a() -> i32 { 2 }\n",
        "rust/m_rs/src/bridge/a.rs": '#[no_mangle] pub extern "C" fn m_rs_a() -> i32 { 2 }\n',
        "rust/m_rs/Cargo.toml": "[package]\n",
        "rust/Cargo.lock": "version = 4\n",
    }.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_bytes((text).encode())
    git(repo, "add", "--all")
    git(repo, "commit", "--quiet", "-m", "port")
    commit = git(repo, "rev-parse", "HEAD").strip()
    changed = [
        "rust/Cargo.lock",
        "rust/m_rs/Cargo.toml",
        "rust/m_rs/src/a.rs",
        "rust/m_rs/src/bridge/a.rs",
        "src/a.cpp",
    ]
    unit = WorkUnit(
        id="U-0001",
        seq=1,
        rules=["port_module"],
        files=["CMakeLists.txt", "src/a.cpp"],
        port=PortUnit(build_target="m", crate="m_rs", module="a"),
    )
    ctx = DashboardContext(
        place=RunPlace(repo / "migration_config.yaml", repo, StateDir.for_repo(repo)), project="m"
    )
    monkeypatch.setattr(data, "unit_view", lambda *_args: _view(unit, commit, changed))

    view = data.port_view(ctx, RUN, "U-0001")
    assert (view.crate, view.module, view.build_target) == ("m_rs", "a", "m")
    assert view.after == commit
    assert view.before == git(repo, "rev-parse", "HEAD^").strip()
    assert [(f.path, f.text) for f in view.sources] == [
        ("src/a.cpp", "int a() { return 1 + 1; }\n")  # the C++ before the unit
    ]
    # The module's own file first, then its bridge, then the rest; no lockfile.
    assert [f.path for f in view.targets] == [
        "rust/m_rs/src/a.rs",
        "rust/m_rs/src/bridge/a.rs",
        "rust/m_rs/Cargo.toml",
    ]
    assert view.targets[0].text == "pub fn a() -> i32 { 2 }\n"

    plain = unit.model_copy(update={"port": None})
    monkeypatch.setattr(data, "unit_view", lambda *_args: _view(plain, commit, changed))
    with pytest.raises(NotFoundError, match="isn't a module port"):
        data.port_view(ctx, RUN, "U-0001")
