"""Phase 2 dependency scripts: member selection, markers shared between the .bat and .sh
flavors, install-toolchain, build-image and pull-images (SPEC §7.15). No real downloads."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
from types import ModuleType

import pytest


def _shared() -> ModuleType:
    """The flavors and drivers of test_dependency_scripts.py (tests aren't a package)."""
    path = Path(__file__).with_name("test_dependency_scripts.py")
    spec = importlib.util.spec_from_file_location("ccm_dependency_script_helpers", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_helpers = _shared()
FLAVORS = _helpers.FLAVORS
SCRIPTS: Path = _helpers.SCRIPTS
SH: str | None = _helpers.SH
TIMEOUT_S: int = _helpers.TIMEOUT_S
Flavor = _helpers.Flavor
current_platform = _helpers.current_platform

SH_PATTERN_DRIVER = """\
CCM_SCRIPTS_DIR=$1
shift
. "$CCM_SCRIPTS_DIR/lib/common.sh"
ccm_parse_args "$@"
ccm_setup
set -f
set -- $CCM_POSITIONAL
ccm_install_tool "$@"
"""

BAT_PATTERN_DRIVER = """\
@echo off
setlocal
set "LIB={lib}"
call "%LIB%" :parse_args %*
if errorlevel 1 exit /b %errorlevel%
call "%LIB%" :setup
if errorlevel 1 exit /b %errorlevel%
call "%LIB%" :install_tool %CCM_POSITIONAL%
exit /b %errorlevel%
"""


@pytest.fixture
def two_file_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """A manifest with one tool whose archive holds bin/tool.txt and doc/readme.txt."""
    plat = current_platform()
    kind = "zip" if plat.startswith("windows-") else "tar.gz"
    archive = tmp_path / "source" / f"tool.{kind}"
    archive.parent.mkdir()
    members = {"tool-1.0/bin/tool.txt": b"binary\n", "tool-1.0/doc/readme.txt": b"docs\n"}
    if kind == "zip":
        with zipfile.ZipFile(archive, "w") as z:
            for name, data in members.items():
                z.writestr(name, data)
    else:
        with tarfile.open(archive, "w:gz") as t:
            for name, data in members.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                t.addfile(info, io.BytesIO(data))
    sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    manifest = tmp_path / "manifest.txt"
    manifest.write_text(
        f"test|tool|1.0|{plat}|{archive.as_uri()}|{sha}|{kind}|tool-1.0/bin\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setenv("CODECOREMORPH_MANIFEST", str(manifest))
    return tmp_path / "home", tmp_path / "home" / "toolchains" / "tool" / "1.0"


def _install(flavor: Flavor, args: list[str], tmp_path: Path) -> subprocess.CompletedProcess[str]:
    driver = SH_PATTERN_DRIVER if flavor.name == "sh" else BAT_PATTERN_DRIVER
    return flavor.run(driver, args, tmp_path)


@pytest.mark.parametrize("flavor", FLAVORS)
def test_member_patterns_extract_only_matching_files(
    flavor: Flavor, two_file_tool: tuple[Path, Path], tmp_path: Path
) -> None:
    home, installed = two_file_tool
    result = _install(flavor, ["--prefix", str(home), "test", "tool", "*/bin/*"], tmp_path)
    assert result.returncode == 0, result.stderr
    assert (installed / "tool-1.0" / "bin" / "tool.txt").is_file()
    assert not (installed / "tool-1.0" / "doc").exists()
    marker = (installed / ".installed").read_text(encoding="utf-8").splitlines()
    assert marker[1] == "members: */bin/*"

    again = _install(flavor, ["--prefix", str(home), "test", "tool", "*/bin/*"], tmp_path)
    assert "already installed" in again.stdout
    everything = _install(flavor, ["--prefix", str(home), "test", "tool"], tmp_path)
    assert "installed tool 1.0 into" in everything.stdout  # a different selection reinstalls
    assert (installed / "tool-1.0" / "doc" / "readme.txt").is_file()


@pytest.mark.skipif(SH is None or sys.platform != "win32", reason="needs cmd.exe and sh")
@pytest.mark.parametrize(("first", "second"), [("bat", "sh"), ("sh", "bat")])
def test_markers_written_by_one_flavor_are_read_by_the_other(
    first: str, second: str, two_file_tool: tuple[Path, Path], tmp_path: Path
) -> None:
    home, _ = two_file_tool
    args = ["--prefix", str(home), "test", "tool", "*/bin/*"]
    assert _install(Flavor(first), args, tmp_path).returncode == 0
    result = _install(Flavor(second), args, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "already installed" in result.stdout


def _script(flavor: Flavor, name: str, args: list[str], env: dict[str, str] | None = None):
    if flavor.name == "sh":
        assert SH is not None
        argv = [SH, (SCRIPTS / f"{name}.sh").as_posix(), *args]
    else:
        argv = ["cmd", "/d", "/c", str(SCRIPTS / f"{name}.bat"), *args]
    return subprocess.run(
        argv, capture_output=True, text=True, timeout=TIMEOUT_S, env=env, check=False
    )


@pytest.mark.skipif(SH is None or sys.platform != "win32", reason="needs cmd.exe and sh")
@pytest.mark.parametrize("name", ["install-toolchain", "build-image", "pull-images"])
def test_bat_and_sh_print_the_same_help(name: str) -> None:
    sh = _script(Flavor("sh"), name, ["--help"])
    bat = _script(Flavor("bat"), name, ["--help"])
    assert sh.returncode == bat.returncode == 0
    assert bat.stdout.replace("\r\n", "\n") == sh.stdout
    assert sh.stdout.startswith(f"Usage: {name} ")


@pytest.mark.parametrize("flavor", FLAVORS)
@pytest.mark.parametrize(
    ("args", "message"),
    [
        ([], "expected one language: cpp, rust, java, csharp or all"),
        (["cpp", "all"], "expected one language: cpp, rust, java, csharp or all"),
        (["java", "--offline", "."], "--offline doesn't apply to java"),
        (["cobol"], "unknown language 'cobol'"),
    ],
)
def test_install_toolchain_usage_errors(flavor: Flavor, args: list[str], message: str) -> None:
    result = _script(flavor, "install-toolchain", args)
    assert result.returncode == 2
    assert message in result.stderr


@pytest.mark.parametrize("flavor", FLAVORS)
def test_install_toolchain_dry_run_lists_the_pinned_tools(flavor: Flavor, tmp_path: Path) -> None:
    import os

    env = {k: v for k, v in os.environ.items() if k != "CODECOREMORPH_MANIFEST"}
    prefix = tmp_path / "home"
    result = _script(
        flavor, "install-toolchain", ["--dry-run", "--prefix", str(prefix), "cpp"], env
    )
    if result.returncode == 3:
        pytest.skip(f"a system prerequisite is missing here: {result.stderr.strip()}")
    assert result.returncode == 0, result.stderr
    for tool in ("cmake", "ninja", "googletest"):
        assert f"would install {tool} " in result.stdout
    assert "would build GoogleTest 1.12.1" in result.stdout
    assert "would write " in result.stdout
    assert not prefix.exists()


@pytest.mark.parametrize("flavor", FLAVORS)
def test_pull_images_dry_run(flavor: Flavor) -> None:
    result = _script(flavor, "pull-images", ["--dry-run"])
    assert result.returncode == 0, result.stderr
    assert "cpp: ghcr.io/codecoremorph/codecoremorph-cpp isn't published yet" in result.stderr
    unknown = _script(flavor, "pull-images", ["--dry-run", "cobol"])
    assert unknown.returncode == 2
    assert "there is no image for 'cobol'" in unknown.stderr


@pytest.mark.parametrize("flavor", FLAVORS)
def test_build_image_dry_run_and_usage(flavor: Flavor) -> None:
    result = _script(flavor, "build-image", ["--dry-run", "cpp"])
    assert result.returncode == 0, result.stderr
    assert "would run: docker build -f " in result.stdout
    assert "-t codecoremorph-cpp:local" in result.stdout
    assert _script(flavor, "build-image", []).returncode == 2
    rust = _script(flavor, "build-image", ["--dry-run", "rust"])  # the port image (D-57)
    assert rust.returncode == 0, rust.stderr
    assert "-t codecoremorph-rust:local" in rust.stdout
    missing = _script(flavor, "build-image", ["cobol"])
    assert missing.returncode == 2
    assert "there is no image definition for 'cobol'" in missing.stderr


@pytest.mark.parametrize("flavor", FLAVORS)
def test_install_toolchain_rust_dry_run(flavor: Flavor, tmp_path: Path) -> None:
    """The Rust toolchain (D-56): the C++ one, then rustup and its pinned Rust release,
    cargo-nextest, cbindgen built by cargo, Corrosion, and rust.env."""
    import os

    env = {k: v for k, v in os.environ.items() if k != "CODECOREMORPH_MANIFEST"}
    prefix = tmp_path / "home"
    result = _script(
        flavor, "install-toolchain", ["--dry-run", "--prefix", str(prefix), "rust"], env
    )
    if result.returncode == 3:
        pytest.skip(f"a system prerequisite is missing here: {result.stderr.strip()}")
    assert result.returncode == 0, result.stderr
    for line in (
        "would install cmake ",
        "would install rustup 1.29.0",
        "would install Rust 1.99.1 with rustup 1.29.0",
        "would install cargo-nextest 0.9.151",
        "would build cbindgen 0.29.4 with cargo install --locked",
        "would install Corrosion 0.6.1",
    ):
        assert line in result.stdout
    assert "rust.env" in result.stdout
    assert not prefix.exists()


@pytest.mark.parametrize("flavor", FLAVORS)
def test_install_toolchain_csharp_dry_run(flavor: Flavor, tmp_path: Path) -> None:
    """The C# toolchain (D-65): the .NET SDK, the .NET 8 and 6 runtimes added to its dotnet
    root, NuGet's config and csharp.env."""
    import os

    env = {k: v for k, v in os.environ.items() if k != "CODECOREMORPH_MANIFEST"}
    prefix = tmp_path / "home"
    result = _script(
        flavor, "install-toolchain", ["--dry-run", "--prefix", str(prefix), "csharp"], env
    )
    assert result.returncode == 0, result.stderr
    for line in (
        "would install dotnet-sdk 10.0.403",
        "would install dotnet-runtime8 8.0.33",
        "would add the .NET 8.0.33 runtime to",
        "would install dotnet-runtime6 6.0.36",
        "would add the .NET 6.0.36 runtime to",
        "nuget.config",
        "csharp.env",
    ):
        assert line in result.stdout
    assert not prefix.exists()


@pytest.mark.parametrize("flavor", FLAVORS)
def test_install_toolchain_rust_refuses_offline(flavor: Flavor, tmp_path: Path) -> None:
    result = _script(flavor, "install-toolchain", ["--offline", str(tmp_path), "rust"])
    assert result.returncode == 2
    assert "--offline doesn't apply to rust: rustup downloads the Rust release" in result.stderr
