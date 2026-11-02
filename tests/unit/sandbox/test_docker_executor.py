"""DockerExecutor logic against a fake Docker client (the real daemon is tested under the
``docker`` marker in tests/integration/sandbox/test_docker.py)."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from docker.errors import ImageNotFound, NotFound

from codecoremorph.config.models import NetworkPolicy
from codecoremorph.core.errors import SandboxError
from codecoremorph.sandbox.docker.executor import (
    MANAGED_LABEL,
    RUN_LABEL,
    DockerExecutor,
    DockerSettings,
    remove_run_resources,
)

RUN = "20261102-141500-a3f9"

# An exec script returns (stdout chunks, stderr chunks, exit code), or None to hang.
ExecScript = Callable[[list[str]], tuple[list[bytes], list[bytes], int] | None]


@dataclass
class FakeResource:
    name: str
    labels: dict[str, str]
    removed: bool = False

    def remove(self, force: bool = False) -> None:
        self.removed = True


@dataclass
class FakeContainer(FakeResource):
    id: str = "c0ffee"
    kwargs: dict[str, Any] = field(default_factory=dict[str, Any])
    archives: list[tuple[str, bytes]] = field(default_factory=list[tuple[str, bytes]])
    restarts: int = 0
    killed: threading.Event = field(default_factory=threading.Event)
    env_file: bytes | None = None  # what `cat` of the toolchain env file prints
    attrs: dict[str, Any] = field(
        default_factory=lambda: {"Config": {"Env": ["PATH=/opt/venv/bin:/usr/bin:/bin"]}}
    )

    def exec_run(self, cmd: list[str], user: str | None = None) -> tuple[int, bytes]:
        if self.env_file is None:
            return 1, b"cat: no such file"
        return 0, self.env_file

    def reload(self) -> None:
        pass

    def restart(self, timeout: int = 10) -> None:
        self.restarts += 1
        self.killed.set()

    def put_archive(self, path: str, data: bytes) -> bool:
        self.archives.append((path, data))
        return True


@dataclass
class FakeNetwork(FakeResource):
    events: list[str] = field(default_factory=list[str])

    def connect(self, container: Any) -> None:
        self.events.append("connect")

    def disconnect(self, container: Any, force: bool = False) -> None:
        self.events.append("disconnect")


class FakeAPI:
    def __init__(self, client: FakeClient):
        self.client = client
        self.execs: dict[str, dict[str, Any]] = {}

    def exec_create(self, container_id: str, cmd: list[str], **kwargs: Any) -> dict[str, str]:
        exec_id = f"exec{len(self.execs)}"
        self.execs[exec_id] = {"cmd": cmd, **kwargs, "exit": None}
        return {"Id": exec_id}

    def exec_start(
        self, exec_id: str, stream: bool, demux: bool
    ) -> Iterator[tuple[bytes | None, bytes | None]]:
        record = self.execs[exec_id]
        outcome = self.client.script(record["cmd"])

        def generate() -> Iterator[tuple[bytes | None, bytes | None]]:
            if outcome is None:  # hang until the container is restarted
                container = self.client.container
                assert container is not None
                container.killed.wait(10)
                return
            out, err, code = outcome
            for chunk in out:
                yield chunk, None
            for chunk in err:
                yield None, chunk
            record["exit"] = code

        return generate()

    def exec_inspect(self, exec_id: str) -> dict[str, Any]:
        return {"ExitCode": self.execs[exec_id]["exit"]}


class _Collection:
    def __init__(self, items: list[Any]):
        self.items = items

    def list(self, all: bool = False, filters: dict[str, str] | None = None) -> list[Any]:  # noqa: A002
        key, _, value = (filters or {}).get("label", "").partition("=")
        return [
            i
            for i in self.items
            if not i.removed and key in i.labels and (not value or i.labels[key] == value)
        ]

    def get(self, name: str) -> Any:
        for item in self.items:
            if item.name == name and not item.removed:
                return item
        raise NotFound(f"{name} not found")


class FakeClient:
    def __init__(self, script: ExecScript, *, image_present: bool = True):
        self.script = script
        self.api = FakeAPI(self)
        self.container: FakeContainer | None = None
        self.pulled: list[str] = []
        self.image_present = image_present
        self._containers: list[FakeContainer] = []
        self._networks: list[FakeNetwork] = []
        self._volumes: list[FakeResource] = []
        self.closed = False
        self.env_file: bytes | None = None
        self.one_shots: list[dict[str, Any]] = []
        client = self

        class Images:
            def get(self, name: str) -> object:
                if not client.image_present:
                    raise ImageNotFound(name)
                return object()

            def pull(self, name: str) -> object:
                client.pulled.append(name)
                return object()

        class Containers(_Collection):
            def run(self, image: str, command: list[str], **kwargs: Any) -> Any:
                if kwargs.get("remove") and not kwargs.get("detach"):
                    client.one_shots.append({"image": image, "command": command, **kwargs})
                    return b""  # a finished one-shot container returns its output
                container = FakeContainer(
                    kwargs["name"],
                    kwargs["labels"],
                    kwargs={"image": image, "command": command, **kwargs},
                )
                container.env_file = client.env_file
                client.container = container
                self.items.append(container)
                return container

        class Networks(_Collection):
            def create(self, name: str, driver: str, labels: dict[str, str]) -> FakeNetwork:
                network = FakeNetwork(name, labels)
                self.items.append(network)
                return network

        class Volumes(_Collection):
            def create(self, name: str, labels: dict[str, str]) -> FakeResource:
                volume = FakeResource(name, labels)
                self.items.append(volume)
                return volume

        self.images = Images()
        self.containers = Containers(self._containers)
        self.networks = Networks(self._networks)
        self.volumes = Volumes(self._volumes)

    def close(self) -> None:
        self.closed = True

    @property
    def network(self) -> FakeNetwork:
        return self._networks[-1]


def _ok(cmd: list[str]) -> tuple[list[bytes], list[bytes], int]:
    return [b"hello ", b"world\n"], [b"warning\n"], 0


def make(
    tmp_path: Path, script: ExecScript = _ok, **overrides: Any
) -> tuple[DockerExecutor, FakeClient]:
    client = FakeClient(script, image_present=overrides.pop("image_present", True))
    settings = DockerSettings(
        image="busybox@sha256:" + "a" * 64,
        run_id=RUN,
        worktree=tmp_path / "worktree",
        build_dir=tmp_path / "build",
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
        user="1000:1000",
        **overrides,
    )
    settings.worktree.mkdir(parents=True, exist_ok=True)
    return DockerExecutor(settings, client_factory=lambda: client), client


def test_container_is_hardened_and_mounted(tmp_path: Path) -> None:
    executor, client = make(tmp_path, tool_cache_volumes={"codecoremorph-ccache": "/ccache"})
    executor.start()
    kwargs = client.container.kwargs if client.container else {}
    assert kwargs["cap_drop"] == ["ALL"]
    assert kwargs["security_opt"] == ["no-new-privileges"]
    assert kwargs["user"] == "1000:1000"
    assert kwargs["init"] is True
    assert kwargs["pids_limit"] == 4096
    assert kwargs["nano_cpus"] == 4_000_000_000
    assert kwargs["mem_limit"] == "8g"
    assert kwargs["labels"] == {RUN_LABEL: RUN, MANAGED_LABEL: "true"}
    assert kwargs["volumes"] == {
        str(tmp_path / "worktree"): {"bind": "/workspace", "mode": "rw"},
        f"codecoremorph-build-{RUN}": {"bind": "/build", "mode": "rw"},
        str(tmp_path / "cache"): {"bind": "/cache", "mode": "rw"},
        "codecoremorph-ccache": {"bind": "/ccache", "mode": "rw"},
    }
    assert kwargs["network"] == f"codecoremorph-{RUN}"
    assert client.network.events == ["disconnect"]  # fetch_only starts offline
    executor.stop()


def test_named_volumes_are_handed_to_the_user_before_the_container_starts(tmp_path: Path) -> None:
    """A new volume belongs to root; the sandbox user (no capabilities) couldn't write it."""
    executor, client = make(tmp_path, tool_cache_volumes={"codecoremorph-ccache": "/ccache"})
    executor.start()
    [init] = client.one_shots
    assert init["entrypoint"] == ["chown"]
    assert init["command"] == ["1000:1000", "/build", "/ccache"]
    assert init["user"] == "0:0"
    assert init["cap_drop"] == ["ALL"]
    assert init["cap_add"] == ["CHOWN"]
    assert init["network_mode"] == "none"
    assert init["labels"] == {RUN_LABEL: RUN, MANAGED_LABEL: "true"}
    assert init["volumes"] == {
        f"codecoremorph-build-{RUN}": {"bind": "/build", "mode": "rw"},
        "codecoremorph-ccache": {"bind": "/ccache", "mode": "rw"},
    }
    executor.stop()


def test_missing_image_is_pulled(tmp_path: Path) -> None:
    executor, client = make(tmp_path, image_present=False)
    executor.start()
    assert client.pulled == ["busybox@sha256:" + "a" * 64]
    executor.stop()


def test_run_collects_output_exit_code_and_log(tmp_path: Path) -> None:
    executor, client = make(tmp_path)
    executor.start()
    result = executor.run(["make", "all"], cwd="/workspace/src", env={"CC": "clang"}, timeout_s=30)
    assert (result.exit_code, result.stdout, result.stderr) == (0, "hello world\n", "warning\n")
    record = client.api.execs["exec0"]
    assert record["workdir"] == "/workspace/src"
    assert record["environment"] == {"CC": "clang"}
    assert record["user"] == "1000:1000"
    assert "hello world" in result.log_path.read_text(encoding="utf-8")
    executor.stop()


def test_stdin_is_delivered_through_a_file(tmp_path: Path) -> None:
    executor, client = make(tmp_path)
    executor.start()
    executor.run(["cat"], cwd="/workspace", timeout_s=30, stdin=b"input")
    cmd = client.api.execs["exec0"]["cmd"]
    assert cmd[:3] == ["sh", "-c", 'exec "$@" < "$0"']
    assert cmd[3].startswith("/tmp/codecoremorph-stdin-")
    assert cmd[4:] == ["cat"]
    assert client.container is not None
    assert client.container.archives[0][0] == "/tmp"
    executor.stop()


def test_timeout_restarts_the_container(tmp_path: Path) -> None:
    executor, client = make(tmp_path, script=lambda cmd: None)
    executor.start()
    result = executor.run(["sleep", "999"], cwd="/workspace", timeout_s=0.5)
    assert result.timed_out
    assert result.exit_code == -1
    assert client.container is not None
    assert client.container.restarts == 1
    executor.stop()


def test_network_policies(tmp_path: Path) -> None:
    executor, client = make(tmp_path)
    executor.start()
    executor.set_network(True)
    executor.set_network(True)  # idempotent
    executor.set_network(False)
    assert client.network.events == ["disconnect", "connect", "disconnect"]
    executor.stop()

    off, _ = make(tmp_path, network=NetworkPolicy.OFF)
    off.start()
    with pytest.raises(SandboxError, match=r"sandbox\.network is off"):
        off.set_network(True)
    off.stop()

    on, on_client = make(tmp_path, network=NetworkPolicy.ON)
    on.start()
    on.set_network(False)
    assert on_client.network.events == []
    on.stop()


def test_path_translation_including_windows_paths(tmp_path: Path) -> None:
    executor, _ = make(tmp_path)
    assert (
        executor.to_sandbox_path(tmp_path / "worktree" / "src" / "a.cpp") == "/workspace/src/a.cpp"
    )
    assert executor.to_sandbox_path(tmp_path / "worktree") == "/workspace"
    assert (
        executor.to_sandbox_path(tmp_path / "build" / "CMakeCache.txt") == "/build/CMakeCache.txt"
    )
    assert executor.to_sandbox_path(tmp_path / "cache" / "index.sqlite") == "/cache/index.sqlite"
    with pytest.raises(SandboxError, match="isn't visible"):
        executor.to_sandbox_path(tmp_path / "elsewhere")


def test_commands_need_a_started_container_and_safe_input(tmp_path: Path) -> None:
    executor, _ = make(tmp_path)
    with pytest.raises(SandboxError, match="start"):
        executor.run(["true"], cwd="/workspace", timeout_s=1)
    executor.start()
    with pytest.raises(SandboxError, match="sandbox path"):
        executor.run(["true"], cwd="relative", timeout_s=1)
    with pytest.raises(SandboxError, match="credential"):
        executor.run(["true"], cwd="/workspace", timeout_s=1, env={"ANTHROPIC_API_KEY": "x"})
    executor.stop()


def test_stop_removes_labeled_resources_and_is_idempotent(tmp_path: Path) -> None:
    executor, client = make(tmp_path)
    executor.start()
    executor.stop()
    executor.stop()
    assert client.closed
    assert client.container is not None
    assert client.container.removed
    assert client.network.removed
    assert all(v.removed for v in client.volumes.items)


def test_remove_run_resources_only_touches_labeled_resources() -> None:
    client = FakeClient(_ok)
    mine = FakeContainer("a", {RUN_LABEL: RUN, MANAGED_LABEL: "true"})
    other_run = FakeContainer("b", {RUN_LABEL: "20261102-000000-0000", MANAGED_LABEL: "true"})
    foreign = FakeContainer("c", {"com.example": "x"})
    client.containers.items.extend([mine, other_run, foreign])
    assert remove_run_resources(client, RUN) == 1
    assert mine.removed
    assert not other_run.removed
    assert not foreign.removed
    assert remove_run_resources(client) == 1  # every CodeCoreMorph run
    assert other_run.removed
    assert not foreign.removed


def test_toolchain_env_of_the_image_reaches_every_command(tmp_path: Path) -> None:
    executor, client = make(tmp_path, toolchain_env="/opt/codecoremorph/toolchains/cpp.env")
    client.env_file = (
        b"# comment\nPATH_PREPEND=/opt/llvm/bin:/opt/cmake/bin\nCC=/opt/llvm/bin/clang\n"
    )
    executor.start()
    assert executor.command_env == {
        "PATH": "/opt/llvm/bin:/opt/cmake/bin:/opt/venv/bin:/usr/bin:/bin",
        "CC": "/opt/llvm/bin/clang",
    }
    executor.run(["cmake", "--version"], cwd="/workspace", env={"CC": "gcc"}, timeout_s=30)
    environment = client.api.execs["exec0"]["environment"]
    assert environment["CC"] == "gcc"  # the command's own variables win
    assert environment["PATH"].startswith("/opt/llvm/bin:")
    executor.stop()


def test_missing_toolchain_env_file_is_tolerated(tmp_path: Path) -> None:
    executor, _ = make(tmp_path, toolchain_env="/opt/none.env")
    executor.start()
    assert executor.command_env == {}
    executor.stop()
