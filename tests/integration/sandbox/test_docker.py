"""DockerExecutor against a real daemon, with a tiny public image. Run with ``-m docker``."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from codecoremorph.config.models import NetworkPolicy
from codecoremorph.core.ids import new_run_id
from codecoremorph.sandbox.docker.executor import DockerExecutor, DockerSettings

pytestmark = pytest.mark.docker

IMAGE = "busybox:1.36"


@pytest.fixture
def executor(tmp_path: Path) -> Iterator[DockerExecutor]:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / "hello.txt").write_text("hello from the host\n", encoding="utf-8")
    ex = DockerExecutor(
        DockerSettings(
            image=IMAGE,
            run_id=new_run_id(),
            worktree=worktree,
            build_dir=tmp_path / "build",
            cache_dir=tmp_path / "cache",
            log_dir=tmp_path / "logs",
            network=NetworkPolicy.FETCH_ONLY,
            cpus=1,
            memory="256m",
            pids_limit=64,
        )
    )
    ex.start()
    yield ex
    ex.stop()


def test_reads_and_writes_the_worktree_as_non_root(executor: DockerExecutor) -> None:
    result = executor.run(
        ["sh", "-c", "cat hello.txt; id -u; echo out > written.txt"], cwd="/workspace", timeout_s=60
    )
    assert result.exit_code == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == "hello from the host"
    assert lines[1] != "0"
    assert (executor.settings.worktree / "written.txt").read_text().strip() == "out"


def test_build_volume_persists_between_commands(executor: DockerExecutor) -> None:
    executor.run(["sh", "-c", "echo cached > /build/state"], cwd="/build", timeout_s=60)
    result = executor.run(["cat", "/build/state"], cwd="/build", timeout_s=60)
    assert result.stdout.strip() == "cached"


def test_timeout_kills_every_process(executor: DockerExecutor) -> None:
    result = executor.run(["sh", "-c", "sleep 300 & sleep 300"], cwd="/workspace", timeout_s=2)
    assert result.timed_out
    # "[s]leep" matches the sleeps but neither the grep nor this shell's own command line.
    after = executor.run(
        ["sh", "-c", "ps | grep -c '[s]leep 300' || true"], cwd="/workspace", timeout_s=30
    )
    assert after.stdout.strip() == "0"


def test_network_toggles(executor: DockerExecutor) -> None:
    offline = executor.run(
        ["sh", "-c", "ip route | grep -c default || true"], cwd="/", timeout_s=30
    )
    assert offline.stdout.strip() == "0"
    executor.set_network(True)
    online = executor.run(["sh", "-c", "ip route | grep -c default || true"], cwd="/", timeout_s=30)
    assert online.stdout.strip() == "1"
    executor.set_network(False)


def test_the_container_is_hardened(executor: DockerExecutor) -> None:
    """Security review (Phase 8, SPEC §10): the settings hold in a real container."""
    status = executor.run(["cat", "/proc/self/status"], cwd="/", timeout_s=30).stdout
    fields = dict(line.split(":", 1) for line in status.splitlines() if ":" in line)
    assert fields["CapEff"].strip() == "0000000000000000"  # every capability dropped
    assert fields["CapBnd"].strip() == "0000000000000000"
    assert fields["NoNewPrivs"].strip() == "1"
    assert fields["Uid"].split()[0] != "0"

    container: Any = executor._container  # pyright: ignore[reportPrivateUsage]
    container.reload()
    host = container.attrs["HostConfig"]
    assert host["Privileged"] is False
    assert host["CapDrop"] == ["ALL"]
    assert host["CapAdd"] in (None, [])
    assert host["SecurityOpt"] == ["no-new-privileges"]
    assert host["Memory"] == 256 * 1024 * 1024
    assert host["PidsLimit"] == 64
    assert host["NanoCpus"] == 1_000_000_000
    assert host["PidMode"] in ("", None)  # not the host's PID namespace
    assert host["IpcMode"] != "host"
    assert host["NetworkMode"] != "host"
    sources = [m["Source"].replace("\\", "/") for m in container.attrs["Mounts"]]
    assert not any(s.endswith("docker.sock") for s in sources)  # no Docker inside
    assert {m["Destination"] for m in container.attrs["Mounts"]} == {
        "/workspace",
        "/build",
        "/cache",
    }


def test_host_credentials_never_reach_the_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "sk-ant-api03-" + "Z" * 24
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
    monkeypatch.setenv("CODECOREMORPH_TEST_TOKEN", secret)
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    ex = DockerExecutor(
        DockerSettings(
            image=IMAGE,
            run_id=new_run_id(),
            worktree=worktree,
            build_dir=tmp_path / "build",
            cache_dir=tmp_path / "cache",
            log_dir=tmp_path / "logs",
            network=NetworkPolicy.OFF,
            cpus=1,
            memory="256m",
            pids_limit=64,
        )
    )
    ex.start()
    try:
        env = ex.run(["env"], cwd="/", timeout_s=30).stdout
    finally:
        ex.stop()
    assert secret not in env
    assert "ANTHROPIC_API_KEY" not in env


def test_stdin(executor: DockerExecutor) -> None:
    result = executor.run(["wc", "-c"], cwd="/workspace", timeout_s=30, stdin=b"12345")
    assert result.stdout.strip() == "5"
