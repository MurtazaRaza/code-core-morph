"""Native toolchains: the bundled install scripts, what is installed, and the tool check that
runs before native mode (SPEC §7.8, §7.15, FR-1.3, FR-1.13)."""

from __future__ import annotations

import importlib.util
import platform
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from codecoremorph.core.errors import HostEnvironmentError, SandboxError
from codecoremorph.core.models import ToolchainSpec
from codecoremorph.core.paths import HomeLayout
from codecoremorph.sandbox.executor import COMMAND_NOT_FOUND, Executor

LANGUAGES = ("cpp", "rust")  # rust includes the cpp toolchain (D-56)
_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_VERSION = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")


def scripts_dir() -> Path:
    """The dependency scripts: bundled in the wheel, or the source checkout's scripts/."""
    for candidate in (_PACKAGE_ROOT / "_scripts", _PACKAGE_ROOT.parents[1] / "scripts"):
        if (candidate / "deps" / "manifest.txt").is_file():
            return candidate
    raise HostEnvironmentError(
        "the dependency scripts are missing from this installation",
        hint="Reinstall CodeCoreMorph, or run it from a source checkout.",
    )


def current_platform() -> str:
    """The manifest platform of this machine, e.g. windows-x64."""
    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"
    os_name = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
    return f"{os_name}-{arch}"


def install_command(
    language: str,
    *,
    force: bool = False,
    dry_run: bool = False,
    prefix: Path | None = None,
    offline: Path | None = None,
) -> list[str]:
    """The command line that runs ``install-toolchain`` for this platform. ``offline`` is a
    directory of pre-downloaded archives to install from instead of the network (FR-1.12)."""
    if language not in (*LANGUAGES, "all"):
        raise SandboxError(f"no native toolchain for {language!r}; available: cpp, rust")
    directory = scripts_dir()
    args = [language]
    if force:
        args.append("--force")
    if dry_run:
        args.append("--dry-run")
    if prefix is not None:
        args += ["--prefix", str(prefix)]
    if offline is not None:
        args += ["--offline", str(offline)]
    if sys.platform == "win32":
        return ["cmd.exe", "/d", "/c", str(directory / "install-toolchain.bat"), *args]
    return ["sh", str(directory / "install-toolchain.sh"), *args]


def download_command(language: str, directory: Path, *, platform: str | None = None) -> list[str]:
    """The command line that downloads a toolchain's pinned archives into ``directory``, for an
    offline install on a machine of ``platform`` (default: this one, FR-1.12). It runs
    ``scripts/deps/fetch-offline.py`` with this Python."""
    if language not in (*LANGUAGES, "all"):
        raise SandboxError(f"no native toolchain for {language!r}; available: cpp, rust")
    script = scripts_dir() / "deps" / "fetch-offline.py"
    argv = [sys.executable, str(script), str(directory)]
    for group in LANGUAGES if language == "all" else (language,):
        argv += ["--group", group]
    if platform is not None:
        argv += ["--platform", platform]
    return argv


def run_install(command: list[str]) -> int:
    """Run an install script in the foreground, streaming its output. Returns its exit code."""
    # The scripts are CodeCoreMorph's own, run on the host like git: they are the step that
    # creates the toolchain the executors use, so they can't go through an executor.
    return subprocess.run(command, check=False).returncode


def _load_manifest_module() -> ModuleType:
    path = scripts_dir() / "deps" / "manifest.py"
    spec = importlib.util.spec_from_file_location("codecoremorph_manifest", path)
    if spec is None or spec.loader is None:
        raise HostEnvironmentError(f"can't load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up while it executes
    spec.loader.exec_module(module)
    return module


@dataclass(frozen=True)
class InstalledTool:
    group: str
    tool: str
    version: str
    directory: Path
    installed: bool  # the marker matches the pinned hash


def installed_tools(home: HomeLayout, group: str | None = None) -> list[InstalledTool]:
    """Pinned tools for this platform, and whether each is installed."""
    manifest = _load_manifest_module()
    text = (scripts_dir() / "deps" / "manifest.txt").read_text(encoding="utf-8")
    rows, _ = manifest.parse(text)
    plat = current_platform()
    found: list[InstalledTool] = []
    for row in rows:
        if row.platform not in (plat, "any") or (group is not None and row.group != group):
            continue
        base = home.root / ("tools" if row.group == "dev" else "toolchains")
        directory = base / row.tool / row.version
        marker = directory / ".installed"
        installed = False
        if marker.is_file():
            first = marker.read_text(encoding="utf-8").splitlines()[:1]
            installed = bool(first) and first[0].strip().lower() == row.sha256
        found.append(InstalledTool(row.group, row.tool, row.version, directory, installed))
    return found


# --------------------------------------------------------------------------- tool check


@dataclass(frozen=True)
class ToolStatus:
    name: str
    found: bool
    version: str | None
    ok: bool
    detail: str


def _version_tuple(text: str) -> tuple[int, int, int] | None:
    match = _VERSION.search(text)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)


def _minimum(text: str) -> tuple[int, int, int]:
    parts = [int(p) for p in re.findall(r"\d+", text)][:3]
    while len(parts) < 3:
        parts.append(0)
    return parts[0], parts[1], parts[2]


def check_tools(executor: Executor, spec: ToolchainSpec, cwd: str) -> list[ToolStatus]:
    """Run each required tool's version command through ``executor`` (FR-1.3)."""
    results: list[ToolStatus] = []
    for tool in spec.tools:
        result = executor.run([tool.name, *tool.version_args], cwd=cwd, timeout_s=60)
        if result.exit_code == COMMAND_NOT_FOUND:
            results.append(ToolStatus(tool.name, False, None, False, "not found"))
            continue
        version = _version_tuple(result.stdout + "\n" + result.stderr)
        text = ".".join(str(v) for v in version) if version else None
        if not result.ok or version is None:
            results.append(ToolStatus(tool.name, True, text, False, "couldn't read its version"))
            continue
        if tool.min_version is not None and version < _minimum(tool.min_version):
            detail = f"version {text} is older than the required {tool.min_version}"
            results.append(ToolStatus(tool.name, True, text, False, detail))
            continue
        results.append(ToolStatus(tool.name, True, text, True, f"version {text}"))
    return results


def require_tools(executor: Executor, spec: ToolchainSpec, cwd: str) -> list[ToolStatus]:
    """:func:`check_tools`, raising one error that lists every missing or outdated tool."""
    results = check_tools(executor, spec, cwd)
    problems = [f"{r.name}: {r.detail}" for r in results if not r.ok]
    if problems:
        raise SandboxError(
            f"the native {spec.language} toolchain is incomplete",
            reason="; ".join(problems),
            hint=spec.install_hint or None,
        )
    return results
