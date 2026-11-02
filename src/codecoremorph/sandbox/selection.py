"""Choosing the executor for ``sandbox.mode`` (FR-1.3, D-07)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict

from codecoremorph.config.models import SandboxMode
from codecoremorph.core.errors import SandboxError
from codecoremorph.sandbox.docker.probe import DockerProbe, probe_docker
from codecoremorph.sandbox.native import REDUCED_ISOLATION_WARNING


class ExecutorChoice(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["docker", "native"]
    reason: str
    warning: str | None = None


def choose_executor(
    mode: SandboxMode, probe: Callable[[], DockerProbe] = probe_docker
) -> ExecutorChoice:
    """``docker`` and ``native`` are honored or fail; ``auto`` prefers Docker."""
    if mode is SandboxMode.NATIVE:
        return ExecutorChoice(
            kind="native", reason="sandbox.mode is native", warning=REDUCED_ISOLATION_WARNING
        )
    status = probe()
    usable = status.reachable and status.os_type in (None, "linux")
    if usable:
        return ExecutorChoice(kind="docker", reason=f"Docker {status.server_version} is available")
    if not status.reachable:
        problem = "Docker isn't reachable"
        fix = "Install or start Docker (Docker Desktop on Windows and macOS)"
    else:
        problem = f"Docker runs {status.os_type} containers"
        fix = "Switch Docker Desktop to Linux containers"
    if mode is SandboxMode.DOCKER:
        raise SandboxError(
            f"sandbox.mode is docker, but {problem}",
            reason=status.detail,
            hint=f"{fix}, or set sandbox.mode to native (reduced isolation).",
        )
    return ExecutorChoice(
        kind="native",
        reason=f"sandbox.mode is auto and {problem}",
        warning=f"{problem}; falling back to native mode. {REDUCED_ISOLATION_WARNING}",
    )
