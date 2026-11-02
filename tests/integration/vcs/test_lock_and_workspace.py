"""The repository lock (FR-6.6) and the run workspace lifecycle (FR-6.1, 6.2, 6.7)."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from codecoremorph.core.errors import RepoLockedError, VcsError
from codecoremorph.core.paths import HomeLayout
from codecoremorph.vcs import LockInfo, RepoLock, RunWorkspace, read_lock

RUN = "20261102-141500-a3f9"
OTHER = "20261102-141501-b4e0"


def _write_lock(path: Path, *, pid: int, host: str | None = None, run_id: str = OTHER) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    info = LockInfo(
        run_id=run_id,
        pid=pid,
        host=host or socket.gethostname(),
        created_at=datetime.now(UTC),
        version="test",
    )
    path.write_text(info.model_dump_json(), encoding="utf-8")


def _dead_pid() -> int:
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


# --------------------------------------------------------------------------- lock


def test_lock_contention(tmp_path: Path) -> None:
    path = tmp_path / ".codecoremorph" / "lock"
    with RepoLock.acquire(path, RUN) as lock:
        assert read_lock(path) == lock.info
        with pytest.raises(RepoLockedError, match=RUN) as info:
            RepoLock.acquire(path, OTHER)
        assert str(path) in (info.value.hint or "")
    assert not path.exists()
    RepoLock.acquire(path, OTHER).release()


def test_stale_lock_from_a_dead_process_is_replaced(tmp_path: Path) -> None:
    path = tmp_path / "lock"
    _write_lock(path, pid=_dead_pid())
    lock = RepoLock.acquire(path, RUN)
    assert read_lock(path) == lock.info
    lock.release()


def test_lock_from_a_live_process_is_respected(tmp_path: Path) -> None:
    path = tmp_path / "lock"
    holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        _write_lock(path, pid=holder.pid)
        with pytest.raises(RepoLockedError):
            RepoLock.acquire(path, RUN)
    finally:
        holder.kill()
        holder.wait()


def test_lock_from_another_host_is_never_broken(tmp_path: Path) -> None:
    path = tmp_path / "lock"
    _write_lock(path, pid=_dead_pid(), host="some-other-machine")
    with pytest.raises(RepoLockedError, match="some-other-machine"):
        RepoLock.acquire(path, RUN)


def test_unreadable_lock_is_held_until_it_is_old(tmp_path: Path) -> None:
    path = tmp_path / "lock"
    path.write_bytes(b"")  # a writer crashed mid-write, or is writing right now
    with pytest.raises(RepoLockedError):
        RepoLock.acquire(path, RUN)
    old = time.time() - 3600
    os.utime(path, (old, old))
    RepoLock.acquire(path, RUN).release()


def test_release_leaves_someone_elses_lock_alone(tmp_path: Path) -> None:
    path = tmp_path / "lock"
    lock = RepoLock.acquire(path, RUN)
    _write_lock(path, pid=os.getpid() + 1, run_id=OTHER)  # replaced behind our back
    lock.release()
    assert path.exists()
    lock.release()  # idempotent


# --------------------------------------------------------------------------- workspace


def test_workspace_create_and_cleanup(repo: Path, isolated_home: Path, git) -> None:
    home = HomeLayout(isolated_home)
    workspace = RunWorkspace.create(repo / "src", RUN)
    try:
        assert workspace.repository.root.resolve() == repo.resolve()
        assert workspace.base_commit == git(repo, "rev-parse", "HEAD").strip()
        assert workspace.worktree.path == home.worktree(RUN)
        assert workspace.worktree.head() == workspace.base_commit
        assert workspace.build_dir == home.build_dir(RUN)
        assert workspace.build_dir.is_dir()
        assert workspace.cache_dir == home.run_cache(RUN)
        assert workspace.cache_dir.is_dir()
        assert (repo / ".codecoremorph" / "lock").exists()
        assert "/.codecoremorph/" in (repo / ".git" / "info" / "exclude").read_text()
        assert not workspace.dirty.is_dirty
        with pytest.raises(RepoLockedError):
            RunWorkspace.create(repo, OTHER)
    finally:
        workspace.cleanup()
    assert not home.worktree(RUN).exists()
    assert not home.build_dir(RUN).exists()
    assert not (repo / ".codecoremorph" / "lock").exists()
    assert git(repo, "branch", "--list", f"codecoremorph/{RUN}").strip()  # the result stays


def test_workspace_cleanup_can_delete_the_branch(repo: Path, git) -> None:
    workspace = RunWorkspace.create(repo, RUN)
    workspace.cleanup(delete_branch=True)
    assert not git(repo, "branch", "--list", "codecoremorph/*").strip()


def test_workspace_from_another_base_with_a_dirty_tree(
    repo: Path, git, caplog: pytest.LogCaptureFixture
) -> None:
    (repo / "README.md").write_bytes(b"# uncommitted\n")
    with RunWorkspace.create(repo, RUN, base="HEAD~1") as workspace:
        assert workspace.base_commit == git(repo, "rev-parse", "HEAD~1").strip()
        assert workspace.dirty.modified == ["README.md"]
        assert not (workspace.worktree.path / "src" / "util.h").exists()
        assert (workspace.worktree.path / "README.md").read_bytes() == b"# demo\n"
    assert "not part of this run" in caplog.text


def test_failed_create_releases_the_lock(repo: Path) -> None:
    with pytest.raises(VcsError, match="doesn't name a commit"):
        RunWorkspace.create(repo, RUN, base="no-such-ref")
    assert not (repo / ".codecoremorph" / "lock").exists()
    RunWorkspace.create(repo, RUN).cleanup(delete_branch=True)


def test_home_inside_the_repository_is_refused(repo: Path) -> None:
    with pytest.raises(VcsError, match="inside the repository"):
        RunWorkspace.create(repo, RUN, home=HomeLayout(repo / "ccm-home"))
