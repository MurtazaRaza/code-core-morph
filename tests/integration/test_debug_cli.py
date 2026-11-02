"""``codecoremorph debug`` against a temporary repository with the native toolchain."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from codecoremorph.cli import app

GitRunner = Callable[..., str]

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "cpp" / "typedefs"
MANIFEST = """\
version: "1.0"
project:
  name: GeoTypedefs
  source_dirs: [src, include]
  test_dirs: [tests]
  public_api: ["include/**"]
migration:
  source: {language: cpp, standard: cpp11, build_system: cmake}
  target: {language: cpp, standard: cpp20, rules: [replace_typedefs_with_using]}
sandbox: {mode: native}
"""

pytestmark = [pytest.mark.toolchain, pytest.mark.usefixtures("restore_logging")]


@pytest.fixture
def project(tmp_path: Path, git: GitRunner, isolated_home: Path, cpp_toolchain_env: Path) -> Path:
    # The isolated home gets the real toolchain's environment file (absolute paths).
    (isolated_home / "toolchains").mkdir(parents=True)
    shutil.copyfile(cpp_toolchain_env, isolated_home / "toolchains" / "cpp.env")
    root = tmp_path / "project"
    shutil.copytree(FIXTURE, root)
    (root / "migration_config.yaml").write_text(MANIFEST, encoding="utf-8")
    git(root.parent, "init", "--quiet", str(root))
    git(root, "add", "--all")
    git(root, "commit", "--quiet", "-m", "legacy")
    return root


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and ".git" not in p.parts and ".codecoremorph" not in p.parts
    }


def test_debug_test_and_codemod_leave_the_working_tree_alone(project: Path, git: GitRunner) -> None:
    before = _snapshot(project)
    runner = CliRunner()
    config = str(project / "migration_config.yaml")

    result = runner.invoke(app, ["debug", "test", config])
    assert result.exit_code == 0, result.output
    assert "gtest:VectorTest.Arithmetic" in result.output
    assert "24 passed" in result.output

    result = runner.invoke(
        app, ["debug", "codemod", "--rule", "replace_typedefs_with_using", "-c", config]
    )
    assert result.exit_code == 0, result.output
    assert "+using Scalar = double;" in result.output
    assert "0 sites left for the agent" in result.output

    assert _snapshot(project) == before
    assert git(project, "branch", "--list").split() == ["*", "main"]  # run branches deleted
