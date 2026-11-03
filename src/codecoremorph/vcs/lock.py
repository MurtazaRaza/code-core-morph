"""One active run per repository (FR-6.6): ``.codecoremorph/lock``.

The lock file is created with ``O_EXCL``, so two processes can't both take it. A lock left by
a process that no longer runs on this machine is stale and is replaced. A lock taken on
another machine (a shared network drive) can't be checked, so it is never broken
automatically.

A run that continues in a later command (a host agent's unit, D-41) *parks* its lock: the
lock then belongs to the run, not to a process, and is never stale. The run's next command
takes it back with :meth:`RepoLock.resume`; finishing or aborting the run releases it.
"""

from __future__ import annotations

import logging
import os
import socket
import time
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError

from codecoremorph import __version__
from codecoremorph.core.errors import RepoLockedError, RunNotActiveError
from codecoremorph.core.processes import pid_is_running

log = logging.getLogger(__name__)

# A lock file that can't be parsed is probably being written; after this long it is junk.
_UNREADABLE_LOCK_GRACE_S = 60.0


class LockInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    pid: int
    host: str
    created_at: AwareDatetime
    version: str
    parked: bool = False  # the run waits for its next command; no process holds the lock


def read_lock(path: Path) -> LockInfo | None:
    """The lock's contents, or None if it is missing or unreadable."""
    try:
        return LockInfo.model_validate_json(path.read_bytes())
    except (OSError, ValidationError):
        return None


def _is_stale(path: Path, holder: LockInfo | None) -> bool:
    if holder is None:
        try:
            return time.time() - path.stat().st_mtime > _UNREADABLE_LOCK_GRACE_S
        except FileNotFoundError:
            return True
    if holder.parked or holder.host != socket.gethostname():
        return False
    return holder.pid != os.getpid() and not pid_is_running(holder.pid)


def held_by_running_command(path: Path, run_id: str) -> bool:
    """Whether a live process of ``run_id`` holds the lock now: not parked and not stale.
    Reads only; the lock file is not touched."""
    holder = read_lock(path)
    if holder is None or holder.run_id != run_id or holder.parked:
        return False
    return not _is_stale(path, holder)


def _info(run_id: str) -> LockInfo:
    return LockInfo(
        run_id=run_id,
        pid=os.getpid(),
        host=socket.gethostname(),
        created_at=datetime.now(UTC),
        version=__version__,
    )


def _rewrite(path: Path, info: LockInfo) -> None:
    """Replace the lock file's contents in one step (write a sibling, then rename)."""
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(info.model_dump_json(), encoding="utf-8")
    temporary.replace(path)


class RepoLock:
    """The repository lock. Use :meth:`acquire`; release it when the run ends."""

    def __init__(self, path: Path, info: LockInfo):
        self.path = path
        self.info = info
        self._held = True

    @classmethod
    def acquire(cls, path: Path, run_id: str) -> RepoLock:
        info = _info(run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(2):
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                holder = read_lock(path)
                if attempt == 0 and _is_stale(path, holder):
                    who = f"run {holder.run_id} (pid {holder.pid})" if holder else "an unknown run"
                    log.warning("removing a stale repository lock left by %s", who)
                    path.unlink(missing_ok=True)
                    continue
                raise _locked_error(path, holder) from None
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(info.model_dump_json())
            return cls(path, info)
        raise _locked_error(path, read_lock(path))

    @classmethod
    def resume(cls, path: Path, run_id: str) -> RepoLock:
        """Take back the lock that ``run_id`` parked when its last command ended."""
        holder = read_lock(path)
        if holder is None or holder.run_id != run_id:
            raise RunNotActiveError(
                f"run {run_id} is not active in this repository",
                reason="its lock is gone: the run finished or was aborted"
                if holder is None
                else f"the lock belongs to run {holder.run_id}",
                hint="Start a new unit, or check the run with `codecoremorph unit status`.",
            )
        if not holder.parked and not _is_stale(path, holder):
            raise _locked_error(path, holder, busy=True)
        info = _info(run_id)
        _rewrite(path, info)
        return cls(path, info)

    @property
    def held(self) -> bool:
        return self._held

    def park(self) -> None:
        """Hand the lock to the run: it outlives this process until the run ends."""
        if not self._held:
            return
        self.info = self.info.model_copy(update={"parked": True})
        _rewrite(self.path, self.info)
        self._held = False

    def release(self) -> None:
        """Remove the lock if it is still ours. Safe to call more than once."""
        if not self._held:
            return
        holder = read_lock(self.path)
        if holder is not None and (holder.run_id, holder.pid) == (self.info.run_id, self.info.pid):
            self.path.unlink(missing_ok=True)
        self._held = False

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()


def _locked_error(path: Path, holder: LockInfo | None, *, busy: bool = False) -> RepoLockedError:
    if busy and holder is not None:
        return RepoLockedError(
            f"another command of run {holder.run_id} is still running (pid {holder.pid})",
            reason="A run's commands run one at a time.",
            hint="Wait for it to finish, then try again.",
        )
    if holder is not None and holder.parked:
        return RepoLockedError(
            f"run {holder.run_id} is active in this repository, waiting for its agent",
            reason="Only one run per repository can be active at a time (FR-6.6).",
            hint="Finish its unit with `codecoremorph unit submit`, or stop it with "
            "`codecoremorph unit abort`.",
        )
    who = (
        f"run {holder.run_id} (pid {holder.pid} on {holder.host}, since {holder.created_at:%c})"
        if holder
        else "another run"
    )
    return RepoLockedError(
        f"another CodeCoreMorph run is active in this repository: {who}",
        reason="Only one run per repository can be active at a time (FR-6.6).",
        hint=f"Wait for it to finish or abort it. If no run is active, delete {path}.",
    )
