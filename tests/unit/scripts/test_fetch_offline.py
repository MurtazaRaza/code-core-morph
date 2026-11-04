"""scripts/deps/fetch-offline.py: archives for an offline install elsewhere (FR-1.12)."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

DEPS = Path(__file__).resolve().parents[3] / "scripts" / "deps"


def _load(name: str, filename: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, DEPS / filename)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_load("manifest", "manifest.py")  # fetch-offline.py imports the shared parser as `manifest`
fetch_offline = _load("fetch_offline", "fetch-offline.py")

# What each (fake) URL serves.
SERVED = {
    "https://example.com/tool-win.zip": b"windows build",
    "https://example.com/tool-linux.tar.gz": b"linux build",
    "https://example.com/lib-src.tar.gz": b"sources for every platform",
    "https://example.com/uv.zip": b"a dev tool",
}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    def serve(url: str, dest: Path) -> None:
        dest.write_bytes(SERVED[url])

    monkeypatch.setattr(fetch_offline, "download", serve)
    rows = [
        ("cpp", "tool", "1.0", "windows-x64", "tool-win.zip", "zip"),
        ("cpp", "tool", "1.0", "linux-x64", "tool-linux.tar.gz", "tar.gz"),
        ("cpp", "lib", "2.0", "any", "lib-src.tar.gz", "tar.gz"),
        ("dev", "uv", "3.0", "windows-x64", "uv.zip", "zip"),
    ]
    lines = [
        f"{g}|{t}|{v}|{p}|https://example.com/{f}|{_sha(SERVED[f'https://example.com/{f}'])}|{a}|."
        for g, t, v, p, f, a in rows
    ]
    path = tmp_path / "manifest.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def run(manifest: Path, out: Path, platform: str, *groups: str) -> int:
    args = [str(out), "--platform", platform, "--manifest", str(manifest)]
    for group in groups:
        args += ["--group", group]
    return fetch_offline.main(args)


def test_fetches_one_platforms_rows_under_their_cache_names(
    manifest: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "bundle"
    assert run(manifest, out, "linux-x64") == 0
    assert sorted(p.name for p in out.iterdir()) == sorted(
        [f"{_sha(b'linux build')}.tar.gz", f"{_sha(b'sources for every platform')}.tar.gz"]
    )
    assert "install-toolchain --offline DIR cpp" in capsys.readouterr().out


def test_groups_and_reruns(
    manifest: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "bundle"
    assert run(manifest, out, "windows-x64", "dev") == 0
    assert [p.name for p in out.iterdir()] == [f"{_sha(b'a dev tool')}.zip"]
    assert "bootstrap-dev --offline DIR" in capsys.readouterr().out
    assert run(manifest, out, "windows-x64", "dev") == 0
    assert "is already in" in capsys.readouterr().out


def test_a_checksum_mismatch_keeps_nothing(manifest: Path, tmp_path: Path) -> None:
    text = manifest.read_text(encoding="utf-8")
    manifest.write_text(text.replace(_sha(b"linux build"), "0" * 64), encoding="utf-8")
    out = tmp_path / "bundle"
    assert run(manifest, out, "linux-x64") == 1
    assert [p.name for p in out.iterdir()] == [f"{_sha(b'sources for every platform')}.tar.gz"]


def test_nothing_pinned_is_a_usage_error(manifest: Path, tmp_path: Path) -> None:
    assert run(manifest, tmp_path / "x", "macos-x64", "dev") == 2


def test_refuses_plain_http(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="non-HTTPS"):
        fetch_offline.download("http://example.com/x.zip", tmp_path / "x.zip")


def test_the_real_manifest_has_a_bundle_for_every_platform() -> None:
    rows, errors = fetch_offline.parse((DEPS / "manifest.txt").read_text(encoding="utf-8"))
    assert errors == []
    for platform in ("windows-x64", "linux-x64", "linux-arm64", "macos-arm64"):
        tools = {r.tool for r in fetch_offline.rows_for(rows, platform, ["cpp"])}
        assert {"cmake", "ninja", "googletest", "googletest-cxx98"} <= tools, platform
