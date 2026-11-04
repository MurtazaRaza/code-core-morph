"""The docs site (Phase 8): it builds without warnings, and its generated references cover
every manifest key and CLI command. Needs the `docs` dependency group (`uv sync --group docs`).
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]

pytest.importorskip("mkdocs")
pytest.importorskip("material")


def _hooks() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "docs_reference", ROOT / "docs/hooks/reference.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _keys(data: Any, prefix: str = "") -> list[str]:
    if not isinstance(data, dict):
        return [prefix]
    found: list[str] = []
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if prefix in ("llm.roles", "llm.providers", "build"):
            path = f"{prefix}.<{'role' if prefix == 'llm.roles' else 'name'}>"
        found += _keys(value, path)
    return found


def test_the_configuration_reference_covers_the_example_manifest() -> None:
    page = _hooks().configuration_page()
    example = yaml.safe_load((ROOT / "examples" / "cpp11-to-cpp20.yaml").read_text("utf-8"))
    missing = [k for k in _keys(example) if f"`{k}`" not in page]
    assert missing == []


def test_the_cli_reference_covers_every_command() -> None:
    page = _hooks().cli_page()
    from typer.main import get_command

    from codecoremorph.cli.app import app

    root: Any = get_command(app)
    for name, command in root.commands.items():
        if getattr(command, "hidden", False):
            continue
        assert f"## `codecoremorph {name}`" in page
    assert "## `codecoremorph toolchain download`" in page
    assert "`--offline`" in page
    assert "--dev" not in page  # hidden


def test_the_site_builds_without_warnings(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "mkdocs", "build", "--strict", "-d", str(tmp_path / "site")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        env={**__import__("os").environ, "NO_MKDOCS_2_WARNING": "1"},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "site" / "reference" / "configuration" / "index.html").is_file()
    security = (tmp_path / "site" / "security" / "index.html").read_text(encoding="utf-8")
    assert re.search(r"Reporting a vulnerability", security)  # SECURITY.md is included
