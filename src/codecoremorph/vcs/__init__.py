"""Git operations through the git CLI, on the host. The only place git is invoked."""

from codecoremorph.vcs.git import MIN_GIT_VERSION, Git, git_version, parse_git_version
from codecoremorph.vcs.lock import LockInfo, RepoLock, read_lock
from codecoremorph.vcs.messages import parse_trailers, summarize_paths, unit_commit_message
from codecoremorph.vcs.repository import Repository, is_run_branch
from codecoremorph.vcs.workspace import RunWorkspace
from codecoremorph.vcs.worktree import LineEndings, Worktree

__all__ = [
    "MIN_GIT_VERSION",
    "Git",
    "LineEndings",
    "LockInfo",
    "RepoLock",
    "Repository",
    "RunWorkspace",
    "Worktree",
    "git_version",
    "is_run_branch",
    "parse_git_version",
    "parse_trailers",
    "read_lock",
    "summarize_paths",
    "unit_commit_message",
]
