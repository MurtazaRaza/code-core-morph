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

from codecoremorph.core.errors import RepoLockedError, RunNotActiveError, VcsError
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


# --------------------------------------------------------------------------- parked runs (D-41)


def test_a_parked_lock_outlives_its_process_and_blocks_other_runs(tmp_path: Path) -> None:
    path = tmp_path / ".codecoremorph" / "lock"
    lock = RepoLock.acquire(path, RUN)
    lock.park()
    assert not lock.held
    holder = read_lock(path)
    assert holder is not None
    assert holder.parked
    # Even when the process that parked it has ended, a parked lock is not stale.
    path.write_text(
        holder.model_copy(update={"pid": _dead_pid()}).model_dump_json(), encoding="utf-8"
    )
    with pytest.raises(RepoLockedError, match="waiting for its agent") as info:
        RepoLock.acquire(path, OTHER)
    assert "codecoremorph unit abort" in (info.value.hint or "")


def test_the_run_resumes_its_parked_lock(tmp_path: Path) -> None:
    path = tmp_path / "lock"
    RepoLock.acquire(path, RUN).park()
    lock = RepoLock.resume(path, RUN)
    holder = read_lock(path)
    assert holder is not None
    assert not holder.parked
    assert holder.pid == os.getpid()
    lock.release()
    assert not path.exists()


def test_resume_refuses_another_runs_lock_and_a_missing_one(tmp_path: Path) -> None:
    path = tmp_path / "lock"
    with pytest.raises(RunNotActiveError, match="not active"):
        RepoLock.resume(path, RUN)
    RepoLock.acquire(path, OTHER).park()
    with pytest.raises(RunNotActiveError, match=f"belongs to run {OTHER}"):
        RepoLock.resume(path, RUN)


def test_resume_waits_for_a_running_command_of_the_run(tmp_path: Path) -> None:
    path = tmp_path / "lock"
    holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        _write_lock(path, pid=holder.pid, run_id=RUN)
        with pytest.raises(RepoLockedError, match="still running"):
            RepoLock.resume(path, RUN)
    finally:
        holder.kill()
        holder.wait()


def test_workspace_parks_and_resumes(repo: Path, isolated_home: Path, git) -> None:
    workspace = RunWorkspace.create(repo, RUN)
    (workspace.worktree.path / "README.md").write_bytes(b"# edited in the run\n")
    workspace.park()
    assert workspace.worktree.path.is_dir()
    with pytest.raises(RepoLockedError):
        RunWorkspace.create(repo, OTHER)
    again = RunWorkspace.resume(repo, RUN, base_commit=workspace.base_commit)
    assert again.worktree.path == workspace.worktree.path
    assert (again.worktree.path / "README.md").read_bytes() == b"# edited in the run\n"
    assert again.build_dir.is_dir()
    again.cleanup(delete_branch=True)
    assert not (repo / ".codecoremorph" / "lock").exists()
    assert not git(repo, "branch", "--list", "codecoremorph/*").strip()
    assert (repo / "README.md").read_bytes() == b"# demo\n"


def test_resuming_without_a_worktree_keeps_the_run_parked(repo: Path, isolated_home: Path) -> None:
    workspace = RunWorkspace.create(repo, RUN)
    workspace.park()
    workspace.repository.remove_worktree(workspace.worktree.path)
    with pytest.raises(VcsError, match=r"worktree of run .* is gone"):
        RunWorkspace.resume(repo, RUN, base_commit=workspace.base_commit)
    holder = read_lock(repo / ".codecoremorph" / "lock")
    assert holder is not None
    assert holder.parked
