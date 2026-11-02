"""``codecoremorph doctor``: environment checks (FR-1.5). More checks join in later phases."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer
from pydantic import BaseModel, ConfigDict
from rich.table import Table
from rich.text import Text

from codecoremorph.cli.output import Status, handle_errors, status_label, stdout
from codecoremorph.config import ConfigValidationError, load_config
from codecoremorph.core.errors import (
    CodeCoreMorphError,
    ExitCode,
    HostEnvironmentError,
)
from codecoremorph.core.paths import HOME_ENV_VAR, codecoremorph_home
from codecoremorph.sandbox.docker.probe import DockerProbe, probe_docker
from codecoremorph.vcs import MIN_GIT_VERSION, git_version

MIN_PYTHON = (3, 12)


class CheckResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    status: Status
    detail: str
    hint: str | None = None


def check_python(version: tuple[int, int, int] | None = None) -> CheckResult:
    major, minor, micro = version or sys.version_info[:3]
    text = f"{major}.{minor}.{micro}"
    if (major, minor) >= MIN_PYTHON:
        return CheckResult(name="python", status="ok", detail=text)
    return CheckResult(
        name="python",
        status="fail",
        detail=f"{text} (need >= 3.12)",
        hint="Run scripts/bootstrap-dev, or `uv python install 3.12`.",
    )


def check_git() -> CheckResult:
    try:
        version = git_version()
    except HostEnvironmentError as exc:
        return CheckResult(name="git", status="fail", detail=exc.message, hint=exc.hint)
    text = ".".join(map(str, version))
    if version[:2] >= MIN_GIT_VERSION:
        return CheckResult(name="git", status="ok", detail=text)
    return CheckResult(
        name="git",
        status="fail",
        detail=f"{text} (need >= {'.'.join(map(str, MIN_GIT_VERSION))})",
        hint="Upgrade git (https://git-scm.com/downloads).",
    )


def check_docker(probe: DockerProbe | None = None) -> CheckResult:
    probe = probe if probe is not None else probe_docker()
    if not probe.reachable:
        return CheckResult(
            name="docker",
            status="warn",
            detail="daemon not reachable; runs will use native mode",
            hint="Install or start Docker (Docker Desktop on Windows and macOS) to run "
            "toolchains in the sandbox.",
        )
    if probe.os_type and probe.os_type != "linux":
        return CheckResult(
            name="docker",
            status="warn",
            detail=f"{probe.server_version}, {probe.os_type} containers",
            hint="Switch Docker Desktop to Linux containers; toolchain images are Linux images.",
        )
    parts = [f"{probe.server_version}, linux containers"]
    if probe.cpus:
        parts.append(f"{probe.cpus} CPUs")
    if probe.memory_bytes:
        parts.append(f"{probe.memory_bytes / 2**30:.1f} GiB")
    if probe.operating_system:
        parts.append(probe.operating_system)
    return CheckResult(name="docker", status="ok", detail=", ".join(parts))


def check_bind_mounts(probe: DockerProbe, platform: str = sys.platform) -> CheckResult | None:
    """Docker Desktop shares Windows and macOS folders slowly (SPEC §15)."""
    if not probe.reachable:
        return None
    if platform == "win32":
        return CheckResult(
            name="mounts",
            status="warn",
            detail="bind mounts from Windows folders are slow in Docker Desktop",
            hint="Builds in the sandbox will be slower. For large repositories, keep the clone "
            "inside WSL2 (\\\\wsl$\\...) or use sandbox.mode: native.",
        )
    if platform == "darwin":
        return CheckResult(
            name="mounts",
            status="info",
            detail="Docker Desktop shares macOS folders more slowly than Linux bind mounts",
            hint="Use VirtioFS file sharing in Docker Desktop's settings for faster builds.",
        )
    return None


def check_home() -> CheckResult:
    home = codecoremorph_home()
    hint = None if home.exists() else "Created on first use (or by scripts/bootstrap-dev)."
    return CheckResult(name="home", status="info", detail=f"{home} ({HOME_ENV_VAR})", hint=hint)


def check_config(path: Path) -> CheckResult:
    try:
        loaded = load_config(path)
    except ConfigValidationError as exc:
        errors = sum(1 for i in exc.issues if i.severity == "error")
        return CheckResult(
            name="config",
            status="fail",
            detail=f"{path}: {errors} error(s)",
            hint=f"Run `codecoremorph validate {path}` for details.",
        )
    except CodeCoreMorphError as exc:
        return CheckResult(name="config", status="fail", detail=exc.message, hint=exc.hint)
    status: Status = "warn" if loaded.warnings else "ok"
    return CheckResult(name="config", status=status, detail=f"{path} is valid")


@handle_errors
def doctor_command(
    config: Annotated[
        Path | None,
        typer.Argument(help="Also validate this manifest.", show_default=False),
    ] = None,
) -> None:
    """Check the environment: Python, git, Docker and (optionally) a manifest."""
    probe = probe_docker()
    results = [check_python(), check_git(), check_docker(probe)]
    mounts = check_bind_mounts(probe)
    if mounts is not None:
        results.append(mounts)
    results.append(check_home())
    if config is not None:
        results.append(check_config(config))

    table = Table(box=None, show_header=False, pad_edge=False)
    table.add_column("status")
    table.add_column("check")
    table.add_column("detail")
    for result in results:
        table.add_row(status_label(result.status), result.name, Text(result.detail))
        if result.hint:
            table.add_row("", "", Text(f"-> {result.hint}", style="dim"))
    stdout().print(table)

    failed = [r.name for r in results if r.status == "fail"]
    if failed:
        raise typer.Exit(int(ExitCode.ENVIRONMENT))
