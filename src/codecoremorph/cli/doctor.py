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
from codecoremorph.llm.base import ProviderRegistry, close_provider
from codecoremorph.llm.catalog import LOCAL_PROVIDERS
from codecoremorph.llm.routing import Router
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


def pull_command(tags: list[str], platform: str = sys.platform) -> str:
    script = r"scripts\pull-models.bat" if platform == "win32" else "./scripts/pull-models.sh"
    return " ".join([script, *tags])


def check_providers(path: Path, registry: ProviderRegistry | None = None) -> list[CheckResult]:
    """Each configured model: do credentials resolve, is the server up, is the model pulled?
    (FR-1.5; one cheap discovery call per model, no tokens.)

    A manifest without an ``llm`` section configures no providers: a host agent such as
    Claude Code makes the edits (D-41), so there is nothing to check.
    """
    try:
        config = load_config(path).config
    except CodeCoreMorphError:
        return []  # check_config reports it
    if "llm" not in config.model_fields_set:
        return [
            CheckResult(
                name="models",
                status="info",
                detail="the manifest configures no model providers",
                hint="None are needed when Claude Code drives CodeCoreMorph through its skill; "
                "add an llm section for unattended runs.",
            )
        ]
    llm = config.llm
    registry = registry or ProviderRegistry.discover()
    router = Router(llm)
    results: list[CheckResult] = []
    missing_tags: list[str] = []
    seen: set[tuple[str, str]] = set()
    for config in router.all_configs():
        key = (config.provider, config.model)
        if key in seen:
            continue
        seen.add(key)
        name = f"model {config.provider}/{config.model}"
        try:
            provider = registry.create(config.provider, llm)
            try:
                health = provider.health_check(config.model)
            finally:
                close_provider(provider)
        except CodeCoreMorphError as exc:
            results.append(CheckResult(name=name, status="fail", detail=exc.message, hint=exc.hint))
            continue
        hint = None
        if not health.ok and config.provider in LOCAL_PROVIDERS and "pull" in health.detail:
            missing_tags.append(config.model)
            hint = f"Run: {pull_command([config.model])}"
        status: Status = "ok" if health.ok else "fail"
        results.append(CheckResult(name=name, status=status, detail=health.detail, hint=hint))
    if len(missing_tags) > 1:
        results.append(
            CheckResult(
                name="models",
                status="info",
                detail=f"{len(missing_tags)} local models aren't pulled",
                hint=f"Run: {pull_command(missing_tags)}",
            )
        )
    return results


@handle_errors
def doctor_command(
    config: Annotated[
        Path | None,
        typer.Argument(help="Also validate this manifest.", show_default=False),
    ] = None,
) -> None:
    """Check the environment: Python, git, Docker and (optionally) a manifest and the models
    its roles use: credentials, Ollama reachability and pulled models."""
    probe = probe_docker()
    results = [check_python(), check_git(), check_docker(probe)]
    mounts = check_bind_mounts(probe)
    if mounts is not None:
        results.append(mounts)
    results.append(check_home())
    if config is not None:
        results.append(check_config(config))
        results += check_providers(config)

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
