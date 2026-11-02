"""Coverage lines (``#!platforms``) in the dependency manifest, and the image requirements'
agreement with uv.lock."""

from __future__ import annotations

import importlib.util
import re
import sys
import tomllib
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[3]
SHA = "a" * 64


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "manifest_for_coverage_tests", ROOT / "scripts" / "deps" / "manifest.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


manifest = _load()


def _row(tool: str, platform: str) -> str:
    archive = "zip" if platform.startswith("windows") else "tar.gz"
    return f"cpp|{tool}|1.0|{platform}|https://example.com/{tool}-{platform}.x|{SHA}|{archive}|."


def _problems(text: str) -> list[str]:
    rows, row_errors = manifest.parse(text)
    coverage, coverage_errors = manifest.parse_coverage(text)
    return row_errors + coverage_errors + manifest.check(rows, coverage)


def test_coverage_lines_limit_the_required_platforms() -> None:
    text = "\n".join(
        [
            "#!platforms cpp|llvm linux-x64,macos-arm64",
            _row("llvm", "linux-x64"),
            _row("llvm", "macos-arm64"),
        ]
    )
    assert _problems(text) == []


def test_coverage_problems() -> None:
    text = "\n".join(
        [
            "#!platforms cpp|llvm linux-x64",
            "#!platforms cpp|llvm linux-x64",
            "#!platforms cpp|ghost linux-x64",
            "#!platforms cpp|odd solaris-x64",
            "#!platforms nonsense",
            _row("llvm", "linux-x64"),
            _row("llvm", "windows-x64"),
        ]
    )
    problems = "\n".join(_problems(text))
    assert "already has a coverage line" in problems
    assert "cpp|ghost: a coverage line names a tool with no rows" in problems
    assert "unknown platform solaris-x64" in problems
    assert "expected '#!platforms group|tool platform,platform'" in problems
    assert "cpp|llvm: its coverage line excludes windows-x64" in problems


def test_image_requirements_match_uv_lock() -> None:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    package = next(p for p in lock["package"] if p["name"] == "libclang")
    locked = {w["hash"] for w in package["wheels"]}
    requirements = (ROOT / "images" / "cpp" / "requirements.txt").read_text(encoding="utf-8")
    assert f"libclang=={package['version']}" in requirements
    pinned = set(re.findall(r"--hash=(sha256:[0-9a-f]{64})", requirements))
    assert pinned
    assert pinned <= locked
