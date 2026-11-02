"""NativeExecutor: commands run directly on the host (SPEC §7.8). Reduced isolation."""

from __future__ import annotations

import logging
import secrets
import shutil
import subprocess
import time
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Literal

from codecoremorph.core.errors import SandboxError
from codecoremorph.core.models import ExecResult
from codecoremorph.sandbox.executor import (
    COMMAND_NOT_FOUND,
    DEFAULT_OUTPUT_CAP,
    LogNamer,
    check_command_env,
    finish_result,
)
from codecoremorph.sandbox.native.env import (
    apply_toolchain_env,
    load_toolchain_env,
    merge_environment,
    scrubbed_environment,
)
from codecoremorph.sandbox.native.proctree import spawn

log = logging.getLogger(__name__)

REDUCED_ISOLATION_WARNING = (
    "native mode: commands, including builds and tests of LLM-written code, run directly on "
    "this machine with your user's permissions and network access. Use Docker for isolation."
)
_KILL_WAIT_S = 30.0


class NativeExecutor:
    kind: Literal["native"] = "native"

    def __init__(
        self,
        *,
        log_dir: Path,
        toolchain_env: Path | None = None,
        env_allowlist: Iterable[str] = (),
        output_cap: int = DEFAULT_OUTPUT_CAP,
        host_env: Mapping[str, str] | None = None,
    ):
        self._logs = LogNamer(log_dir)
        self._toolchain_env_file = toolchain_env
        self._allowlist = tuple(env_allowlist)
        self._output_cap = output_cap
        self._host_env = host_env
        self._base_env: dict[str, str] | None = None

    def start(self) -> None:
        log.warning(REDUCED_ISOLATION_WARNING)
        self._base_env = self._compute_base_env()

    def _compute_base_env(self) -> dict[str, str]:
        base = scrubbed_environment(self._host_env, self._allowlist)
        return apply_toolchain_env(base, load_toolchain_env(self._toolchain_env_file))

    @property
    def base_env(self) -> dict[str, str]:
        if self._base_env is None:
            self._base_env = self._compute_base_env()
        return dict(self._base_env)

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
        if not argv:
            raise ValueError("argv is empty")
        check_command_env(env)
        workdir = Path(cwd)
        if not workdir.is_dir():
            raise SandboxError(f"working directory {cwd} doesn't exist")
        child_env = merge_environment(self.base_env, env or {})
        log_path = log_path or self._logs.next(argv[0])
        log_path.parent.mkdir(parents=True, exist_ok=True)
        stem = f".{log_path.name}.{secrets.token_hex(3)}"
        out_file = log_path.with_name(stem + ".stdout")
        err_file = log_path.with_name(stem + ".stderr")

        # Resolve the program with the command's own PATH: on Windows, CreateProcess would
        # search the parent's PATH and miss toolchain directories.
        path_value = next((v for k, v in child_env.items() if k.upper() == "PATH"), "")
        executable = shutil.which(argv[0], path=path_value)
        if executable is None:
            err_file.write_bytes(f"command not found: {argv[0]}\n".encode())
            return finish_result(
                argv=argv,
                cwd=cwd,
                stdout_file=out_file,
                stderr_file=err_file,
                log_path=log_path,
                exit_code=COMMAND_NOT_FOUND,
                duration_s=0.0,
                timed_out=False,
                output_cap=self._output_cap,
            )

        log.debug("native run: %s (cwd %s, timeout %.0f s)", " ".join(argv), cwd, timeout_s)
        started = time.monotonic()
        timed_out = False
        with out_file.open("wb") as out, err_file.open("wb") as err:
            process = spawn(
                [executable, *argv[1:]],
                cwd=workdir,
                env=child_env,
                stdout=out,
                stderr=err,
                stdin_pipe=stdin is not None,
            )
            try:
                process.popen.communicate(input=stdin, timeout=timeout_s)
            except subprocess.TimeoutExpired:
                timed_out = True
                process.kill_tree()
                try:
                    process.popen.wait(timeout=_KILL_WAIT_S)
                except subprocess.TimeoutExpired:
                    log.warning("pid %d survived the timeout kill", process.popen.pid)
            finally:
                process.close()
        returncode = process.popen.returncode
        return finish_result(
            argv=argv,
            cwd=cwd,
            stdout_file=out_file,
            stderr_file=err_file,
            log_path=log_path,
            exit_code=returncode if returncode is not None else -1,
            duration_s=time.monotonic() - started,
            timed_out=timed_out,
            output_cap=self._output_cap,
            note=f"killed after the {timeout_s:g} s timeout" if timed_out else None,
        )

    def set_network(self, enabled: bool) -> None:
        log.debug("native mode can't restrict network access (asked: %s)", enabled)

    def to_sandbox_path(self, host_path: Path) -> str:
        return str(host_path)

    def stop(self) -> None:
        """Nothing to tear down: every command's process tree ends with the command."""
