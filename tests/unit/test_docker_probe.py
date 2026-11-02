"""probe_docker() without a real daemon (tests against a live daemon are marked `docker`)."""

from __future__ import annotations

import docker
import pytest
from docker.errors import DockerException

from codecoremorph.sandbox.docker.probe import probe_docker


class _FakeClient:
    def __init__(self, version: dict[str, str]):
        self._version = version
        self.closed = False

    def version(self) -> dict[str, str]:
        return self._version

    def info(self) -> dict[str, object]:
        return {
            "OperatingSystem": "Docker Desktop",
            "NCPU": 8,
            "MemTotal": 2**34,
            "Architecture": "x86_64",
        }

    def close(self) -> None:
        self.closed = True


def test_unreachable_daemon_is_reported_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    def from_env(**kwargs: object) -> _FakeClient:
        raise DockerException("Error while fetching server API version")

    monkeypatch.setattr(docker, "from_env", from_env)
    probe = probe_docker()
    assert not probe.reachable
    assert probe.detail is not None
    assert "server API version" in probe.detail


def test_reachable_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient({"Version": "27.3.1", "Os": "linux"})
    monkeypatch.setattr(docker, "from_env", lambda **kwargs: client)
    probe = probe_docker()
    assert (probe.reachable, probe.server_version, probe.os_type) == (True, "27.3.1", "linux")
    assert (probe.operating_system, probe.cpus, probe.memory_bytes) == ("Docker Desktop", 8, 2**34)
    assert client.closed


@pytest.mark.docker
def test_live_daemon() -> None:
    assert probe_docker().reachable
