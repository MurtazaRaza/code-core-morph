from __future__ import annotations

import pytest

from codecoremorph.config.models import SandboxMode
from codecoremorph.core.errors import SandboxError
from codecoremorph.sandbox import choose_executor
from codecoremorph.sandbox.docker.probe import DockerProbe

UP = DockerProbe(reachable=True, server_version="27.3.1", os_type="linux")
DOWN = DockerProbe(reachable=False, detail="pipe not found")
WINDOWS_CONTAINERS = DockerProbe(reachable=True, server_version="27.3.1", os_type="windows")


def _never() -> DockerProbe:
    raise AssertionError("native mode must not probe Docker")


def test_native_is_honored_without_probing() -> None:
    choice = choose_executor(SandboxMode.NATIVE, _never)
    assert choice.kind == "native"
    assert choice.warning is not None


@pytest.mark.parametrize("mode", [SandboxMode.AUTO, SandboxMode.DOCKER])
def test_docker_when_available(mode: SandboxMode) -> None:
    choice = choose_executor(mode, lambda: UP)
    assert (choice.kind, choice.warning) == ("docker", None)
    assert "27.3.1" in choice.reason


@pytest.mark.parametrize(
    ("probe", "problem"), [(DOWN, "isn't reachable"), (WINDOWS_CONTAINERS, "windows containers")]
)
def test_auto_falls_back_to_native_with_a_warning(probe: DockerProbe, problem: str) -> None:
    choice = choose_executor(SandboxMode.AUTO, lambda: probe)
    assert choice.kind == "native"
    assert choice.warning is not None
    assert problem in choice.warning
    assert "falling back to native mode" in choice.warning


@pytest.mark.parametrize(
    ("probe", "fix"), [(DOWN, "start Docker"), (WINDOWS_CONTAINERS, "Linux containers")]
)
def test_docker_mode_fails_clearly(probe: DockerProbe, fix: str) -> None:
    with pytest.raises(SandboxError, match=r"sandbox\.mode is docker") as info:
        choose_executor(SandboxMode.DOCKER, lambda: probe)
    assert fix.lower() in (info.value.hint or "").lower()
    assert "native" in (info.value.hint or "")
