"""The Executor interface (SPEC §7.8) and the output handling both executors share.

Every toolchain command goes through an ``Executor``. Adapters never start processes
themselves. Output is captured to files, so a chatty build can't exhaust memory; the
:class:`~codecoremorph.core.models.ExecResult` carries a capped copy (head and tail) and
``log_path`` points at the full log.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO, Literal, Protocol

from codecoremorph.core.errors import SandboxError
from codecoremorph.core.models import ExecResult
from codecoremorph.core.secrets import is_secret_env_name, looks_like_secret

DEFAULT_OUTPUT_CAP = 64 * 1024  # bytes kept per stream in ExecResult; the log has everything
COMMAND_NOT_FOUND = 127


class Executor(Protocol):
    """Runs commands in the sandbox (Docker) or on the host (native).

    ``cwd`` and paths inside ``argv`` are sandbox paths; convert host paths with
    :meth:`to_sandbox_path`. ``log_path`` (an extension of SPEC §7.8) chooses where the full
    log goes; by default the executor names it in its log directory.
    """

    @property
    def kind(self) -> Literal["docker", "native"]: ...

    def start(self) -> None: ...

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: str,
        env: Mapping[str, str] | None = None,
        timeout_s: float,
        stdin: bytes | None = None,
        log_path: Path | None = None,
    ) -> ExecResult: ...

    def set_network(self, enabled: bool) -> None: ...

    def to_sandbox_path(self, host_path: Path) -> str: ...

    def stop(self) -> None: ...


def check_command_env(env: Mapping[str, str] | None) -> None:
    """Refuse to pass credentials to a command (NFR-5.3)."""
    for key, value in (env or {}).items():
        if is_secret_env_name(key) or looks_like_secret(value):
            raise SandboxError(
                f"refusing to pass the credential-like variable {key} to a sandboxed command",
                reason="Secrets never enter the sandbox (NFR-5.3).",
            )


class LogNamer:
    """Numbered log file names: ``0003-cmake.log``."""

    def __init__(self, directory: Path):
        self.directory = directory
        self._count = 0

    def next(self, argv0: str) -> Path:
        self._count += 1
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(argv0).name)[:40] or "command"
        return self.directory / f"{self._count:04d}-{name}.log"


_STDOUT_MARK = b"\n\n--- stdout ---\n"
_STDERR_MARK = b"\n--- stderr ---\n"


def full_output(result: ExecResult) -> tuple[str, str]:
    """The complete stdout and stderr of a command.

    :class:`ExecResult` keeps a capped copy; when it was capped, this reads the full output
    back from the log that :func:`finish_result` wrote.
    """
    if not (result.stdout_truncated or result.stderr_truncated):
        return result.stdout, result.stderr
    data = result.log_path.read_bytes()
    start = data.find(_STDOUT_MARK)
    split = data.find(_STDERR_MARK, start + len(_STDOUT_MARK)) if start >= 0 else -1
    if start < 0 or split < 0:
        return result.stdout, result.stderr
    stdout = data[start + len(_STDOUT_MARK) : split]
    stderr = data[split + len(_STDERR_MARK) :]
    return stdout.decode("utf-8", "replace"), stderr.decode("utf-8", "replace")


def _read_capped(path: Path, cap: int) -> tuple[str, bool]:
    size = path.stat().st_size if path.exists() else 0
    if size <= cap:
        return (path.read_bytes() if size else b"").decode("utf-8", errors="replace"), False
    half = cap // 2
    with path.open("rb") as handle:
        head = handle.read(half)
        handle.seek(size - half)
        tail = handle.read(half)
    omitted = size - 2 * half
    marker = f"\n[... {omitted} bytes omitted; the log has the full output ...]\n"
    return head.decode("utf-8", "replace") + marker + tail.decode("utf-8", "replace"), True


def _copy_into(log: BinaryIO, path: Path) -> None:
    if path.exists():
        with path.open("rb") as source:
            shutil.copyfileobj(source, log)


def finish_result(
    *,
    argv: Sequence[str],
    cwd: str,
    stdout_file: Path,
    stderr_file: Path,
    log_path: Path,
    exit_code: int,
    duration_s: float,
    timed_out: bool,
    output_cap: int,
    note: str | None = None,
) -> ExecResult:
    """Write the full log, remove the capture files and build the capped result."""
    stdout, stdout_truncated = _read_capped(stdout_file, output_cap)
    stderr, stderr_truncated = _read_capped(stderr_file, output_cap)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    header = [
        f"$ {' '.join(argv)}",
        f"cwd: {cwd}",
        f"finished: {datetime.now(UTC).isoformat()}",
        f"exit code: {exit_code}" + (" (timed out)" if timed_out else ""),
        f"duration: {duration_s:.3f} s",
    ]
    if note:
        header.append(f"note: {note}")
    with log_path.open("wb") as log:
        log.write(("\n".join(header) + "\n\n--- stdout ---\n").encode())
        _copy_into(log, stdout_file)
        log.write(b"\n--- stderr ---\n")
        _copy_into(log, stderr_file)
    stdout_file.unlink(missing_ok=True)
    stderr_file.unlink(missing_ok=True)
    return ExecResult(
        argv=list(argv),
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
        duration_s=max(0.0, duration_s),
        timed_out=timed_out,
        log_path=log_path,
    )
