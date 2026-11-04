"""``codecoremorph dashboard`` (SPEC §7.14)."""

from __future__ import annotations

from pathlib import Path

import pytest
import uvicorn
from typer.testing import CliRunner

from codecoremorph.cli import app

from .conftest import EngineWorld

runner = CliRunner()

pytestmark = pytest.mark.usefixtures("restore_logging")


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> list[uvicorn.Server]:
    started: list[uvicorn.Server] = []
    monkeypatch.setattr(uvicorn.Server, "run", lambda self, sockets=None: started.append(self))
    return started


def test_the_command_prints_the_link(engine_world: EngineWorld, served: list[object]) -> None:
    config = engine_world.manifest()
    result = runner.invoke(app, ["dashboard", "-c", str(config), "--port", "0", "--no-browser"])
    assert result.exit_code == 0, result.output
    assert "CodeCoreMorph dashboard" in result.output
    assert "http://127.0.0.1:" in result.output
    assert "?token=" in result.output
    assert "Ctrl-C" in result.output
    assert len(served) == 1


def test_another_interface_is_a_warning(engine_world: EngineWorld, served: list[object]) -> None:
    config = engine_world.manifest()
    result = runner.invoke(
        app,
        ["dashboard", "-c", str(config), "--host", "0.0.0.0", "--port", "0", "--no-browser"],  # noqa: S104
    )
    assert result.exit_code == 0, result.output
    assert "other machines on this network can reach the dashboard" in result.output


def test_a_missing_manifest_is_a_usage_error(tmp_path: Path, served: list[object]) -> None:
    result = runner.invoke(app, ["dashboard", "-c", str(tmp_path / "nope.yaml")])
    assert result.exit_code == 2
    assert "not found" in result.output
    assert served == []
