"""Mapping sandbox paths in tool output back to worktree-relative paths."""

from __future__ import annotations

import posixpath
import re

_DRIVE = re.compile(r"^[A-Za-z]:/")


def _norm(path: str) -> str:
    return path.replace("\\", "/")


def _is_absolute(path: str) -> bool:
    return path.startswith("/") or bool(_DRIVE.match(path))


class PathMapper:
    """Converts paths as tools print them (``/workspace/src/a.cpp``, ``E:\\w\\src\\a.cpp``,
    ``../src/a.cpp`` relative to the build directory) into worktree-relative POSIX paths."""

    def __init__(self, worktree: str, build_dir: str, *, case_insensitive: bool):
        self.case_insensitive = case_insensitive
        self.worktree = posixpath.normpath(_norm(worktree))
        self.build_dir = posixpath.normpath(_norm(build_dir))
        self._root_key = self._key(self.worktree.rstrip("/") + "/")

    def _key(self, path: str) -> str:
        return path.casefold() if self.case_insensitive else path

    def absolute(self, path: str, base: str | None = None) -> str:
        p = _norm(path)
        if not _is_absolute(p):
            p = f"{_norm(base) if base else self.build_dir}/{p}"
        return posixpath.normpath(p)

    def relative(self, path: str, base: str | None = None) -> str | None:
        """The worktree-relative path, or None when ``path`` is outside the worktree."""
        p = self.absolute(path, base)
        key = self._key(p)
        if not key.startswith(self._root_key):
            return None
        rel = p[len(self._root_key) :]
        return rel or None

    def sandbox(self, rel: str) -> str:
        """The sandbox path of a worktree-relative path."""
        return f"{self.worktree}/{rel}"
