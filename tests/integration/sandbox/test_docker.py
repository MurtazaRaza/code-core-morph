"""DockerExecutor against a real daemon, with a tiny public image. Run with ``-m docker``."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

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
    after = executor.run(
        ["sh", "-c", "ps | grep -c 'sleep 300' || true"], cwd="/workspace", timeout_s=30
    )
    assert after.stdout.strip() in ("0", "1")  # only the grep itself may match


def test_network_toggles(executor: DockerExecutor) -> None:
    offline = executor.run(
        ["sh", "-c", "ip route | grep -c default || true"], cwd="/", timeout_s=30
    )
    assert offline.stdout.strip() == "0"
    executor.set_network(True)
    online = executor.run(["sh", "-c", "ip route | grep -c default || true"], cwd="/", timeout_s=30)
    assert online.stdout.strip() == "1"
    executor.set_network(False)


def test_stdin(executor: DockerExecutor) -> None:
    result = executor.run(["wc", "-c"], cwd="/workspace", timeout_s=30, stdin=b"12345")
    assert result.stdout.strip() == "5"
