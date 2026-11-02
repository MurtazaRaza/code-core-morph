"""scripts/deps: the manifest format check and the hash refresher."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

DEPS = Path(__file__).resolve().parents[3] / "scripts" / "deps"
SHA = "a" * 64


def _load(name: str, filename: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, DEPS / filename)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# refresh-hashes.py and check-manifest.py import the shared parser as `manifest`.
manifest = _load("manifest", "manifest.py")
refresh_hashes = _load("refresh_hashes", "refresh-hashes.py")
check_manifest = _load("check_manifest", "check-manifest.py")

PLATFORMS: tuple[str, ...] = manifest.PLATFORMS


def _row(platform: str, version: str = "1.0", **overrides: str) -> str:
    fields = {
        "group": "dev",
        "tool": "uv",
        "version": version,
        "platform": platform,
        "url": f"https://example.com/uv/{version}/uv-{platform}.tar.gz",
        "sha256": SHA,
        "archive": "zip" if platform.startswith("windows") else "tar.gz",
        "bin_dir": f"uv-{version}",
    } | overrides
    return "|".join(fields.values())


def _full(version: str = "1.0") -> str:
    return "# header\n\n" + "\n".join(_row(p, version) for p in PLATFORMS) + "\n"


def _problems(text: str) -> list[str]:
    rows, errors = manifest.parse(text)
    return errors + manifest.check(rows)


def test_shipped_manifest_is_valid(capsys: pytest.CaptureFixture[str]) -> None:
    assert check_manifest.main([]) == 0
    assert "rows OK" in capsys.readouterr().out


def test_complete_manifest_has_no_problems() -> None:
    assert _problems(_full()) == []


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (_row("linux-x64") + "|extra", "expected 8 fields"),
        (_row("linux-x64", url="http://example.com/x.tar.gz"), "url must be https://"),
        (_row("linux-x64", url="https://example.com/x?y=1"), "url must be https://"),
        (_row("linux-x64", url="https://example.com/a%20b.tar.gz"), "url must be https://"),
        (_row("linux-x64", sha256="ABC"), "sha256 must be 64 lowercase hex"),
        (_row("linux-x64", archive="rar"), "archive 'rar'"),
        (_row("windows-x64", archive="tar.xz"), "Windows rows can't use tar.xz"),
        (_row("linux-x64", bin_dir="../escape"), "bin_dir '../escape'"),
        (_row("linux-x64", bin_dir="/abs"), "bin_dir '/abs'"),
        (_row("solaris-x64"), "platform 'solaris-x64'"),
        (_row("linux-x64", tool="UV"), "tool 'UV'"),
        (_row("linux-x64", bin_dir=""), "bin_dir is empty"),
        (_row("linux-x64") + " ", "whitespace is not allowed"),
    ],
)
def test_malformed_rows(line: str, expected: str) -> None:
    _, errors = manifest.parse(line + "\n")
    assert any(expected in e for e in errors), errors


def test_missing_platform_and_mixed_versions() -> None:
    text = "\n".join([_row("linux-x64"), _row("macos-arm64", version="2.0")])
    problems = _problems(text)
    assert any("no row for windows-x64, linux-arm64, macos-x64" in p for p in problems)
    assert any("rows pin different versions (1.0, 2.0)" in p for p in problems)


def test_duplicates_and_any_rows() -> None:
    assert any("more than one row" in p for p in _problems(_full() + _row("linux-x64") + "\n"))
    any_only = _row("any", archive="file", bin_dir=".")
    assert _problems(any_only + "\n") == []
    assert any("must be the tool's only row" in p for p in _problems(_full() + any_only))


def test_check_manifest_rejects_crlf(tmp_path: Path) -> None:
    path = tmp_path / "manifest.txt"
    path.write_bytes(_full().replace("\n", "\r\n").encode())
    assert check_manifest.main([str(path)]) == 1


def test_refresh_rewrites_hashes_and_keeps_layout() -> None:
    text = _full()
    new_text, changes = refresh_hashes.refresh(text, fetch=lambda url: "b" * 64)
    assert len(changes) == len(PLATFORMS)
    assert new_text.startswith("# header\n\n")
    assert new_text.count("b" * 64) == len(PLATFORMS)
    assert SHA not in new_text


def test_refresh_bumps_a_version() -> None:
    fetched: list[str] = []

    def fetch(url: str) -> str:
        fetched.append(url)
        return "c" * 64

    new_text, changes = refresh_hashes.refresh(_full(), tool="uv", new_version="1.1", fetch=fetch)
    assert all("/uv/1.1/" in url for url in fetched)
    assert "dev|uv|linux-x64: 1.0 -> 1.1" in changes
    rows, errors = manifest.parse(new_text)
    assert errors == []
    assert {r.version for r in rows} == {"1.1"}
    assert {r.bin_dir for r in rows} == {"uv-1.1"}


def test_refresh_refuses_unknown_tools_and_plain_http() -> None:
    with pytest.raises(ValueError, match="no manifest rows match"):
        refresh_hashes.refresh(_full(), tool="nope", fetch=lambda url: SHA)
    with pytest.raises(ValueError, match="non-HTTPS"):
        refresh_hashes.fetch_sha256("http://example.com/x")
