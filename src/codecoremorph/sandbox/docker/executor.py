"""DockerExecutor: one long-lived, hardened container per run (SPEC §7.8).

- **Mounts:** the worktree at ``/workspace`` (bind, read-write), the run's build volume at
  ``/build``, a cache directory at ``/cache``, plus named tool-cache volumes.
- **Hardening:** non-root user, ``--cap-drop=ALL``, ``no-new-privileges``, CPU, memory and
  PID limits, an init process for signal handling and zombie reaping.
- **Network:** the container sits on its own bridge network. ``set_network`` connects and
  disconnects it, as ``sandbox.network`` allows (``off`` never connects; ``on`` stays on).
- **Timeouts:** exec'd processes can't be killed through the Docker API, so a timeout
  restarts the container, which kills every process in it. The worktree, the volumes and
  the network setup survive; the engine runs one command at a time, so nothing else is lost.
- **Cleanup:** :meth:`stop` (also registered with ``atexit``) removes the container, network
  and build volume. :func:`remove_run_resources` removes leftovers by label after a crash.
"""

from __future__ import annotations

import atexit
import io
import logging
import os
import secrets
import sys
import tarfile
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from codecoremorph.config.models import NetworkPolicy
from codecoremorph.core.errors import SandboxError
from codecoremorph.core.ids import validate_run_id
from codecoremorph.core.models import ExecResult
from codecoremorph.sandbox.executor import (
    DEFAULT_OUTPUT_CAP,
    LogNamer,
    check_command_env,
    finish_result,
)
from codecoremorph.sandbox.native.env import PATH_PREPEND, parse_env_file

log = logging.getLogger(__name__)

RUN_LABEL = "codecoremorph.run"
MANAGED_LABEL = "codecoremorph.managed"
WORKSPACE_MOUNT = "/workspace"
BUILD_MOUNT = "/build"
CACHE_MOUNT = "/cache"
CONTAINER_TMP = "/tmp"  # noqa: S108 - inside the container, private to the run
# The container's main process: idle until stopped (the init process forwards signals).
_IDLE_COMMAND = ["sh", "-c", "trap 'exit 0' TERM INT; while :; do sleep 3600 & wait $!; done"]
_STREAM_JOIN_S = 30.0
_DEFAULT_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def default_user() -> str:
    """The host user's uid:gid, so files written in the bind-mounted worktree belong to them.

    Docker Desktop on Windows ignores ownership on bind mounts, so any non-root id works.
    """
    if sys.platform == "win32":
        return "1000:1000"
    uid, gid = os.getuid(), os.getgid()
    if uid == 0:
        log.warning("running as root; the sandbox container will run as root too")
    return f"{uid}:{gid}"


@dataclass(frozen=True)
class DockerSettings:
    image: str
    run_id: str
    worktree: Path
    build_dir: Path  # host-side name of the build volume's contents (what adapters see)
    cache_dir: Path  # bind-mounted at /cache
    log_dir: Path
    network: NetworkPolicy = NetworkPolicy.FETCH_ONLY
    cpus: float = 4.0
    memory: str = "8g"
    pids_limit: int = 4096
    tool_cache_volumes: Mapping[str, str] = field(default_factory=dict[str, str])
    user: str | None = None
    # The toolchain environment file inside the image (/opt/codecoremorph/toolchains/<lang>.env),
    # applied to every command like the native executor applies the host's (SPEC §7.15).
    toolchain_env: str | None = None

    @property
    def container_name(self) -> str:
        return f"codecoremorph-{validate_run_id(self.run_id)}"

    @property
    def network_name(self) -> str:
        return f"codecoremorph-{self.run_id}"

    @property
    def build_volume(self) -> str:
        return f"codecoremorph-build-{self.run_id}"

    @property
    def labels(self) -> dict[str, str]:
        return {RUN_LABEL: self.run_id, MANAGED_LABEL: "true"}


def _docker_client() -> Any:
    import docker  # slow import; only Docker mode needs it

    return docker.from_env(timeout=120)


class DockerExecutor:
    kind: Literal["docker"] = "docker"

    def __init__(
        self,
        settings: DockerSettings,
        *,
        client_factory: Callable[[], Any] = _docker_client,
        output_cap: int = DEFAULT_OUTPUT_CAP,
    ):
        self.settings = settings
        self._client_factory = client_factory
        self._output_cap = output_cap
        self._logs = LogNamer(settings.log_dir)
        self._client: Any = None
        self._container: Any = None
        self._network: Any = None
        self._connected = False
        self._user = settings.user or default_user()
        self._command_env: dict[str, str] = {}

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        from docker.errors import DockerException, ImageNotFound, NotFound

        s = self.settings
        try:
            client = self._client = self._client_factory()
            try:
                client.images.get(s.image)
            except ImageNotFound:
                log.info("pulling %s", s.image)
                client.images.pull(s.image)
            remove_run_resources(client, s.run_id, keep_volumes=True)  # a crashed earlier start
            try:
                self._network = client.networks.get(s.network_name)
            except NotFound:
                self._network = client.networks.create(
                    s.network_name, driver="bridge", labels=s.labels
                )
            try:
                client.volumes.get(s.build_volume)
            except NotFound:
                client.volumes.create(name=s.build_volume, labels=s.labels)
            s.cache_dir.mkdir(parents=True, exist_ok=True)
            volumes: dict[str, dict[str, str]] = {
                str(s.worktree): {"bind": WORKSPACE_MOUNT, "mode": "rw"},
                s.build_volume: {"bind": BUILD_MOUNT, "mode": "rw"},
                str(s.cache_dir): {"bind": CACHE_MOUNT, "mode": "rw"},
            }
            for volume, mount in s.tool_cache_volumes.items():
                volumes[volume] = {"bind": mount, "mode": "rw"}
            self._hand_volumes_to_user(client)
            self._container = client.containers.run(
                s.image,
                _IDLE_COMMAND,
                detach=True,
                name=s.container_name,
                labels=s.labels,
                user=self._user,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges"],
                nano_cpus=int(s.cpus * 1e9),
                mem_limit=s.memory,
                pids_limit=s.pids_limit,
                volumes=volumes,
                working_dir=WORKSPACE_MOUNT,
                network=s.network_name,
                init=True,
                environment={"HOME": CONTAINER_TMP},
            )
            self._connected = True
            if s.network is not NetworkPolicy.ON:
                self._disconnect()
            self._command_env = self._load_toolchain_env()
        except DockerException as exc:
            self.stop()
            raise SandboxError(
                f"couldn't start the sandbox container from {s.image}",
                reason=str(exc),
                hint="Check that Docker is running (`codecoremorph doctor`), or use "
                "sandbox.mode: native.",
            ) from exc
        atexit.register(self.stop)
        log.info("sandbox container %s started", s.container_name)

    def _hand_volumes_to_user(self, client: Any) -> None:
        """Give the named volumes' roots to the sandbox user.

        Docker creates a volume owned by root, and the sandbox container can't change that: it
        runs as the user with every capability dropped. A one-shot container, root with only
        CAP_CHOWN and no network, chowns the mount points (not their contents) and exits.
        """
        s = self.settings
        mounts = {s.build_volume: BUILD_MOUNT, **s.tool_cache_volumes}
        client.containers.run(
            s.image,
            [self._user, *mounts.values()],
            entrypoint=["chown"],
            name=f"{s.container_name}-init",
            labels=s.labels,
            user="0:0",
            cap_drop=["ALL"],
            cap_add=["CHOWN"],
            security_opt=["no-new-privileges"],
            network_mode="none",
            volumes={volume: {"bind": mount, "mode": "rw"} for volume, mount in mounts.items()},
            remove=True,
        )

    def _load_toolchain_env(self) -> dict[str, str]:
        path = self.settings.toolchain_env
        if path is None:
            return {}
        exit_code, output = self._container.exec_run(["cat", path], user=self._user)
        if exit_code != 0:
            log.warning("the image has no toolchain environment file %s", path)
            return {}
        values = parse_env_file(bytes(output).decode("utf-8", "replace"))
        self._container.reload()
        image_env: list[str] = list(self._container.attrs.get("Config", {}).get("Env") or [])
        base_path = next(
            (entry.split("=", 1)[1] for entry in image_env if entry.startswith("PATH=")),
            _DEFAULT_PATH,
        )
        env: dict[str, str] = {}
        for key, value in values.items():
            if key == PATH_PREPEND:
                env["PATH"] = f"{value}:{base_path}"
            else:
                env[key] = value
        return env

    @property
    def command_env(self) -> dict[str, str]:
        """Variables every command gets: the toolchain environment of the image."""
        return dict(self._command_env)

    def stop(self, *, remove_volumes: bool = True) -> None:
        """Remove the container, network and (by default) the build volume. Idempotent."""
        client = self._client
        if client is None:
            return
        try:
            remove_run_resources(client, self.settings.run_id, keep_volumes=not remove_volumes)
        finally:
            self._container = self._network = None
            self._client = None
            atexit.unregister(self.stop)
            close = getattr(client, "close", None)
            if callable(close):
                close()

    # ------------------------------------------------------------------ commands

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: str,
        env: Mapping[str, str] | None = None,
        timeout_s: float,
        stdin: bytes | None = None,
        log_path: Path | None = None,
    ) -> ExecResult:
        if self._container is None:
            raise SandboxError("the sandbox container isn't running; call start() first")
        if not argv:
            raise ValueError("argv is empty")
        check_command_env(env)
        if not PurePosixPath(cwd).is_absolute():
            raise SandboxError(f"cwd must be a sandbox path such as /workspace/src, got {cwd!r}")
        command = list(argv)
        if stdin is not None:
            command = self._with_stdin(command, stdin)

        log_path = log_path or self._logs.next(argv[0])
        log_path.parent.mkdir(parents=True, exist_ok=True)
        stem = f".{log_path.name}.{secrets.token_hex(3)}"
        out_file = log_path.with_name(stem + ".stdout")
        err_file = log_path.with_name(stem + ".stderr")

        api = self._client.api
        exec_id = api.exec_create(
            self._container.id,
            command,
            stdout=True,
            stderr=True,
            workdir=cwd,
            environment={**self._command_env, **(env or {})},
            user=self._user,
        )["Id"]
        started = time.monotonic()
        stream: Iterator[tuple[bytes | None, bytes | None]] = api.exec_start(
            exec_id, stream=True, demux=True
        )
        failure: list[BaseException] = []

        def pump() -> None:
            try:
                with out_file.open("wb") as out, err_file.open("wb") as err:
                    for chunk_out, chunk_err in stream:
                        if chunk_out:
                            out.write(chunk_out)
                        if chunk_err:
                            err.write(chunk_err)
            except Exception as exc:  # noqa: BLE001 - the connection drops on a timeout restart
                failure.append(exc)

        reader = threading.Thread(target=pump, name="codecoremorph-docker-exec", daemon=True)
        reader.start()
        reader.join(timeout_s)
        timed_out = reader.is_alive()
        if timed_out:
            log.warning("command timed out after %g s; restarting the sandbox container", timeout_s)
            self._container.restart(timeout=0)
            reader.join(_STREAM_JOIN_S)
        elif failure:
            raise SandboxError(
                "lost the connection to the sandbox container", reason=str(failure[0])
            )
        exit_code = api.exec_inspect(exec_id).get("ExitCode")
        return finish_result(
            argv=argv,
            cwd=cwd,
            stdout_file=out_file,
            stderr_file=err_file,
            log_path=log_path,
            exit_code=-1 if timed_out or exit_code is None else int(exit_code),
            duration_s=time.monotonic() - started,
            timed_out=timed_out,
            output_cap=self._output_cap,
            note=f"container restarted after the {timeout_s:g} s timeout" if timed_out else None,
        )

    def _with_stdin(self, command: list[str], data: bytes) -> list[str]:
        name = f"codecoremorph-stdin-{secrets.token_hex(6)}"
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))
        self._container.put_archive(CONTAINER_TMP, buffer.getvalue())
        return ["sh", "-c", 'exec "$@" < "$0"', f"{CONTAINER_TMP}/{name}", *command]

    # ------------------------------------------------------------------ network and paths

    def set_network(self, enabled: bool) -> None:
        policy = self.settings.network
        if policy is NetworkPolicy.ON:
            return
        if policy is NetworkPolicy.OFF:
            if enabled:
                raise SandboxError(
                    "network access was requested, but sandbox.network is off",
                    hint="Set sandbox.network to fetch_only if the build must download "
                    "dependencies.",
                )
            return
        if enabled and not self._connected:
            self._network.connect(self._container)
            self._connected = True
        elif not enabled and self._connected:
            self._disconnect()

    def _disconnect(self) -> None:
        self._network.disconnect(self._container, force=True)
        self._connected = False

    @property
    def network_connected(self) -> bool:
        return self._connected

    def to_sandbox_path(self, host_path: Path) -> str:
        """Translate a host path (Windows paths included) into the container."""
        s = self.settings
        for host_root, mount in (
            (s.worktree, WORKSPACE_MOUNT),
            (s.build_dir, BUILD_MOUNT),
            (s.cache_dir, CACHE_MOUNT),
        ):
            try:
                relative = host_path.resolve().relative_to(host_root.resolve())
            except ValueError:
                continue
            return str(PurePosixPath(mount, *relative.parts))
        raise SandboxError(
            f"{host_path} isn't visible inside the sandbox",
            reason="Only the worktree, the build directory and the cache directory are mounted.",
        )


def remove_run_resources(
    client: Any, run_id: str | None = None, *, keep_volumes: bool = False
) -> int:
    """Remove CodeCoreMorph containers, networks and volumes by label (one run, or all).

    Returns how many resources were removed. Only labeled resources are touched.
    """
    from docker.errors import APIError, NotFound

    label = f"{RUN_LABEL}={run_id}" if run_id else MANAGED_LABEL
    removed = 0
    for container in client.containers.list(all=True, filters={"label": label}):
        try:
            container.remove(force=True)
            removed += 1
        except NotFound:
            pass
    for network in client.networks.list(filters={"label": label}):
        try:
            network.remove()
            removed += 1
        except (NotFound, APIError) as exc:
            log.debug("couldn't remove network %s: %s", getattr(network, "name", "?"), exc)
    if not keep_volumes:
        for volume in client.volumes.list(filters={"label": label}):
            try:
                volume.remove(force=True)
                removed += 1
            except (NotFound, APIError) as exc:
                log.debug("couldn't remove volume %s: %s", getattr(volume, "name", "?"), exc)
    return removed


def release_run(run_id: str) -> bool:
    """Best effort: remove a finished run's container, network and build volume, for a command
    that ends a parked run without starting its sandbox. False if Docker can't be reached."""
    try:
        client = _docker_client()
    except Exception as exc:  # noqa: BLE001 - Docker being down must not block an abort
        log.warning("couldn't reach Docker to remove run %s's volume: %s", run_id, exc)
        return False
    try:
        remove_run_resources(client, run_id)
        return True
    except Exception as exc:  # noqa: BLE001 - see above
        log.warning("couldn't remove run %s's Docker resources: %s", run_id, exc)
        return False
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()
