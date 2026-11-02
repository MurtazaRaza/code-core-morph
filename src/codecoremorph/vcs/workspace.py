"""A run's isolated workspace: lock, branch, worktree, build and cache directories (FR-6).

```
<repo>/.codecoremorph/lock                      one active run per repository
<CODECOREMORPH_HOME>/worktrees/<run-id>/        branch codecoremorph/<run-id>, from the base
<CODECOREMORPH_HOME>/build/<run-id>/            build output, outside the worktree (FR-6.7)
<CODECOREMORPH_HOME>/cache/<run-id>/            adapter caches
```
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Self

from codecoremorph.core.errors import VcsError
from codecoremorph.core.ids import run_branch, validate_run_id
from codecoremorph.core.models.vcs import DirtyState
from codecoremorph.core.paths import STATE_DIR_NAME, HomeLayout
from codecoremorph.vcs.lock import RepoLock
from codecoremorph.vcs.repository import Repository
from codecoremorph.vcs.worktree import Worktree

log = logging.getLogger(__name__)


def _inside(child: Path, parent: Path) -> bool:
    return child.resolve().is_relative_to(parent.resolve())


@dataclass
class RunWorkspace:
    run_id: str
    repository: Repository
    base_commit: str
    worktree: Worktree
    build_dir: Path
    cache_dir: Path
    dirty: DirtyState
    lock: RepoLock = field(repr=False)

    @classmethod
    def create(
        cls,
        repo_path: Path,
        run_id: str,
        *,
        base: str = "HEAD",
        home: HomeLayout | None = None,
    ) -> RunWorkspace:
        """Take the lock and create the run branch and worktree from ``base`` (FR-6.1, 6.2)."""
        validate_run_id(run_id)
        home = home or HomeLayout.resolve()
        repository = Repository.discover(repo_path)
        if _inside(home.root, repository.root):
            raise VcsError(
                f"CODECOREMORPH_HOME ({home.root}) is inside the repository {repository.root}",
                reason="Run worktrees and build output must live outside the repository.",
                hint="Set CODECOREMORPH_HOME to a directory outside the repository.",
            )

        repository.ensure_excluded()  # before the state directory exists, so git ignores it
        lock = RepoLock.acquire(repository.root / STATE_DIR_NAME / "lock", run_id)
        worktree: Worktree | None = None
        try:
            base_commit = repository.resolve_commit(base)
            dirty = repository.dirty_state()
            if dirty.is_dirty:
                log.warning(
                    "%d uncommitted and %d untracked files in %s are not part of this run; "
                    "it starts from %s (%s)",
                    len(dirty.modified),
                    len(dirty.untracked),
                    repository.root,
                    base,
                    base_commit[:12],
                )
            worktree = repository.add_worktree(
                home.worktree(run_id), run_branch(run_id), base_commit
            )
            missing = repository.populate_submodules(worktree, base_commit)
            if missing:
                log.warning(
                    "the run worktree lacks submodules that %s doesn't have checked out: %s; "
                    "run `git submodule update --init --recursive` there first",
                    repository.root,
                    ", ".join(missing),
                )
            build_dir = home.build_dir(run_id)
            cache_dir = home.run_cache(run_id)
            for directory in (build_dir, cache_dir):
                directory.mkdir(parents=True, exist_ok=True)
        except BaseException:
            if worktree is not None:
                repository.remove_worktree(worktree.path)
                repository.delete_run_branch(worktree.branch)
            lock.release()
            raise
        log.info("run %s: worktree %s on %s", run_id, worktree.path, worktree.branch)
        return cls(
            run_id=run_id,
            repository=repository,
            base_commit=base_commit,
            worktree=worktree,
            build_dir=build_dir,
            cache_dir=cache_dir,
            dirty=dirty,
            lock=lock,
        )

    def cleanup(self, *, delete_branch: bool = False, keep_build: bool = False) -> None:
        """Remove the worktree (and, only if asked, the run branch), then release the lock.

        The branch is the run's result, so it stays by default (FR-6.5).
        """
        try:
            self.repository.remove_worktree(self.worktree.path)
            if delete_branch:
                self.repository.delete_run_branch(self.worktree.branch)
            if not keep_build:
                for directory in (self.build_dir, self.cache_dir):
                    shutil.rmtree(directory, ignore_errors=True)
        finally:
            self.lock.release()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.cleanup()
