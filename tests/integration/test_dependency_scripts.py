"""The dependency scripts (SPEC §7.15), run for real against local file:// downloads.

Each test runs once for the POSIX ``.sh`` flavor and once for the ``.bat`` flavor (Windows
only). The library functions are driven through small generated driver scripts, so the tests
exercise the same code paths as bootstrap-dev and install-toolchain.
"""

from __future__ import annotations

import hashlib
import io
import platform
import shutil
import subprocess
import sys
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
TIMEOUT_S = 120


def current_platform() -> str:
    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"
    os_name = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
    return f"{os_name}-{arch}"


def find_sh() -> str | None:
    if sys.platform != "win32":
        return shutil.which("sh")
    # Git for Windows ships sh.exe; avoid System32\bash.exe, which is WSL.
    git = shutil.which("git")
    if git:
        root = Path(git).resolve()
        for parent in root.parents:
            candidate = parent / "bin" / "sh.exe"
            if candidate.is_file():
                return str(candidate)
    return None


SH = find_sh()

SH_INSTALL_DRIVER = """\
CCM_SCRIPTS_DIR=$1
shift
. "$CCM_SCRIPTS_DIR/lib/common.sh"
ccm_parse_args "$@"
ccm_setup
set -- $CCM_POSITIONAL
ccm_install_tool "$1" "$2"
echo "bin_dir=$CCM_TOOL_BIN_DIR"
"""

BAT_INSTALL_DRIVER = """\
@echo off
setlocal
set "LIB={lib}"
call "%LIB%" :parse_args %*
if errorlevel 1 exit /b %errorlevel%
call "%LIB%" :setup
if errorlevel 1 exit /b %errorlevel%
for /f "tokens=1,2" %%a in ("%CCM_POSITIONAL%") do set "G=%%a" & set "T=%%b"
call "%LIB%" :install_tool %G% %T%
if errorlevel 1 exit /b %errorlevel%
echo bin_dir=%CCM_TOOL_BIN_DIR%
exit /b 0
"""


@dataclass(frozen=True)
class Flavor:
    name: str

    def run(
        self,
        driver: str,
        args: list[str],
        tmp_path: Path,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if self.name == "sh":
            assert SH is not None
            script = tmp_path / "driver.sh"
            script.write_text(driver, encoding="utf-8", newline="\n")
            argv = [SH, script.as_posix(), SCRIPTS.as_posix(), *args]
        else:
            script = tmp_path / "driver.bat"
            lib = SCRIPTS / "lib" / "common.bat"
            script.write_text(driver.replace("{lib}", str(lib)), encoding="utf-8", newline="\r\n")
            argv = ["cmd", "/d", "/c", str(script), *args]
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
            env=env,
            check=False,
        )

    def install(
        self, args: list[str], tmp_path: Path, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        driver = SH_INSTALL_DRIVER if self.name == "sh" else BAT_INSTALL_DRIVER
        return self.run(driver, args, tmp_path, env)


FLAVORS = [
    pytest.param(
        Flavor("sh"), id="sh", marks=pytest.mark.skipif(SH is None, reason="no POSIX sh found")
    ),
    pytest.param(
        Flavor("bat"),
        id="bat",
        marks=pytest.mark.skipif(sys.platform != "win32", reason="cmd.exe is Windows-only"),
    ),
]


@dataclass(frozen=True)
class FakeTool:
    manifest: Path
    archive: Path
    sha256: str
    prefix: Path

    @property
    def install_dir(self) -> Path:
        return self.prefix / "toolchains" / "fake" / "1.0"

    @property
    def downloads(self) -> Path:
        return self.prefix / "downloads"


def _make_archive(path: Path) -> None:
    payload = b"fake tool payload\n"
    if path.suffix == ".zip":
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("fake-1.0/bin/fake.txt", payload)
    else:
        with tarfile.open(path, "w:gz") as archive:
            info = tarfile.TarInfo("fake-1.0/bin/fake.txt")
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))


@pytest.fixture
def fake_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeTool:
    plat = current_platform()
    kind = "zip" if plat.startswith("windows-") else "tar.gz"
    source = tmp_path / "source"
    source.mkdir()
    archive = source / f"fake-1.0.{kind}"
    _make_archive(archive)
    sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    url = archive.as_uri()
    manifest = tmp_path / "manifest.txt"
    manifest.write_text(
        "# test manifest\n"
        f"test|fake|1.0|{plat}|{url}|{sha}|{kind}|fake-1.0/bin\n"
        f"test|badsum|1.0|{plat}|{url}|{'0' * 64}|{kind}|.\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setenv("CODECOREMORPH_MANIFEST", str(manifest))
    return FakeTool(manifest=manifest, archive=archive, sha256=sha, prefix=tmp_path / "home")


@pytest.mark.parametrize("flavor", FLAVORS)
def test_install_verifies_extracts_and_marks(
    flavor: Flavor, fake_tool: FakeTool, tmp_path: Path
) -> None:
    result = flavor.install(["--prefix", str(fake_tool.prefix), "test", "fake"], tmp_path)
    assert result.returncode == 0, result.stderr
    assert (fake_tool.install_dir / "fake-1.0" / "bin" / "fake.txt").read_bytes().strip() == (
        b"fake tool payload"
    )
    marker = (fake_tool.install_dir / ".installed").read_text().splitlines()
    assert marker == [fake_tool.sha256, "members:"]
    assert "installed fake 1.0 into" in result.stdout
    assert result.stdout.strip().splitlines()[-1].replace("\\", "/").endswith("fake-1.0/bin")
    # The verified archive is kept for reuse; no partial files or temp dirs are left.
    assert [p.name.split(".")[0] for p in fake_tool.downloads.iterdir()] == [fake_tool.sha256]
    assert sorted(p.name for p in fake_tool.install_dir.parent.iterdir()) == ["1.0"]


@pytest.mark.parametrize("flavor", FLAVORS)
def test_second_run_is_a_no_op(flavor: Flavor, fake_tool: FakeTool, tmp_path: Path) -> None:
    args = ["--prefix", str(fake_tool.prefix), "test", "fake"]
    assert flavor.install(args, tmp_path).returncode == 0
    fake_tool.archive.unlink()  # a second download would now fail
    shutil.rmtree(fake_tool.downloads)
    result = flavor.install(args, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "fake 1.0 is already installed in" in result.stdout
    assert "downloading" not in result.stdout
    assert not fake_tool.downloads.exists()


@pytest.mark.parametrize("flavor", FLAVORS)
def test_force_reinstalls_from_the_cache(
    flavor: Flavor, fake_tool: FakeTool, tmp_path: Path
) -> None:
    args = ["--prefix", str(fake_tool.prefix), "test", "fake"]
    assert flavor.install(args, tmp_path).returncode == 0
    stray = fake_tool.install_dir / "stray.txt"
    stray.write_text("left over", encoding="utf-8")
    result = flavor.install([*args, "--force"], tmp_path)
    assert result.returncode == 0, result.stderr
    assert "using cached" in result.stdout
    assert not stray.exists()
    assert (fake_tool.install_dir / ".installed").is_file()


@pytest.mark.parametrize("flavor", FLAVORS)
def test_checksum_mismatch_deletes_the_file_and_exits_1(
    flavor: Flavor, fake_tool: FakeTool, tmp_path: Path
) -> None:
    result = flavor.install(["--prefix", str(fake_tool.prefix), "test", "badsum"], tmp_path)
    assert result.returncode == 1
    assert "checksum mismatch" in result.stderr
    assert "The file was deleted." in result.stderr
    assert list(fake_tool.downloads.iterdir()) == []
    assert not (fake_tool.prefix / "toolchains" / "badsum" / "1.0").exists()


@pytest.mark.parametrize("flavor", FLAVORS)
def test_dry_run_downloads_nothing(flavor: Flavor, fake_tool: FakeTool, tmp_path: Path) -> None:
    result = flavor.install(
        ["--dry-run", "--prefix", str(fake_tool.prefix), "test", "fake"], tmp_path
    )
    assert result.returncode == 0, result.stderr
    assert f"would download {fake_tool.archive.as_uri()}" in result.stdout
    assert "would install fake 1.0 into" in result.stdout
    assert not fake_tool.prefix.exists()


@pytest.mark.parametrize("flavor", FLAVORS)
def test_missing_prerequisite_exits_3_with_instructions(flavor: Flavor, tmp_path: Path) -> None:
    if flavor.name == "sh":
        driver = (
            'CCM_SCRIPTS_DIR=$1\n. "$CCM_SCRIPTS_DIR/lib/common.sh"\n'
            "PATH=/nonexistent-ccm-dir\nccm_require_download_tools\n"
        )
        result = flavor.run(driver, [], tmp_path)
        assert result.returncode == 3
        assert "neither curl nor wget was found. Install curl" in result.stderr
    else:
        driver = (
            '@echo off\ncall "{lib}" :require_cmd ccm-no-such-tool "Install it from example.org."\n'
            "exit /b %errorlevel%\n"
        )
        result = flavor.run(driver, [], tmp_path)
        assert result.returncode == 3
        assert "'ccm-no-such-tool' was not found. Install it from example.org." in result.stderr


@pytest.mark.skipif(sys.platform != "win32", reason="cmd.exe is Windows-only")
def test_bat_without_builtin_tools_exits_3(fake_tool: FakeTool, tmp_path: Path) -> None:
    import os

    empty_root = tmp_path / "fake-windows"
    empty_root.mkdir()
    env = {**os.environ, "SYSTEMROOT": str(empty_root)}
    result = Flavor("bat").install(
        ["--prefix", str(fake_tool.prefix), "test", "fake"], tmp_path, env
    )
    assert result.returncode == 3
    assert "curl.exe was not found" in result.stderr
    assert "Windows 10 version 1803 or later" in result.stderr


@pytest.mark.parametrize("flavor", FLAVORS)
@pytest.mark.parametrize(
    ("args", "code", "message"),
    [
        (["--bogus", "test", "fake"], 2, "unknown option '--bogus'"),
        (["test", "fake", "--prefix"], 2, "--prefix needs a directory"),
        (["test", "nosuchtool"], 3, "no pinned download of nosuchtool"),
    ],
)
def test_usage_and_lookup_errors(
    flavor: Flavor,
    fake_tool: FakeTool,
    tmp_path: Path,
    args: list[str],
    code: int,
    message: str,
) -> None:
    result = flavor.install(args, tmp_path)
    assert result.returncode == code
    assert message in result.stderr


@pytest.mark.parametrize("flavor", FLAVORS)
def test_missing_manifest_is_a_usage_error(
    flavor: Flavor, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODECOREMORPH_MANIFEST", str(tmp_path / "missing.txt"))
    result = flavor.install(["test", "fake"], tmp_path)
    assert result.returncode == 2
    assert "dependency manifest not found" in result.stderr


# --------------------------------------------------------------------------- bootstrap-dev


def _bootstrap(flavor: Flavor, args: list[str]) -> subprocess.CompletedProcess[str]:
    if flavor.name == "sh":
        assert SH is not None
        argv = [SH, (SCRIPTS / "bootstrap-dev.sh").as_posix(), *args]
    else:
        argv = ["cmd", "/d", "/c", str(SCRIPTS / "bootstrap-dev.bat"), *args]
    return subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT_S, check=False)


@pytest.mark.parametrize("flavor", FLAVORS)
def test_bootstrap_help(flavor: Flavor) -> None:
    result = _bootstrap(flavor, ["--help"])
    assert result.returncode == 0
    assert result.stdout.startswith("Usage: bootstrap-dev [--prefix DIR]")
    assert "3 missing system prerequisite" in result.stdout


@pytest.mark.parametrize("flavor", FLAVORS)
def test_bootstrap_rejects_bad_usage(flavor: Flavor) -> None:
    assert _bootstrap(flavor, ["--bogus"]).returncode == 2
    assert _bootstrap(flavor, ["extra"]).returncode == 2


@pytest.mark.parametrize("flavor", FLAVORS)
def test_bootstrap_dry_run_changes_nothing(flavor: Flavor, tmp_path: Path) -> None:
    prefix = tmp_path / "home"
    result = _bootstrap(flavor, ["--dry-run", "--prefix", str(prefix)])
    assert result.returncode == 0, result.stderr
    for step in ("uv python install 3.12", "uv sync", "uv run codecoremorph --version"):
        assert f"would run: {step}" in result.stdout
    assert not prefix.exists()


def test_bat_and_sh_print_the_same_bootstrap_help() -> None:
    if SH is None or sys.platform != "win32":
        pytest.skip("needs both cmd.exe and sh")
    sh = _bootstrap(Flavor("sh"), ["--help"]).stdout
    bat = _bootstrap(Flavor("bat"), ["--help"]).stdout
    assert bat.replace("\r\n", "\n") == sh
