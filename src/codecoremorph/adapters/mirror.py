"""A copy of the run worktree that a build tool may write into (D-68).

Old Makefiles build inside the source tree: objects beside the sources, ``./configure``'s
``Makefile`` and ``config.h``, libraries and programs. The run worktree's ``git add --all``
would commit all of it, so an adapter keeps a mirror of the worktree in its cache directory
and builds there.

:meth:`Mirror.sync` copies a worktree file only when the worktree's version changed since the
last sync, judged by a manifest of what each copy was made from. So a file the build rewrote,
such as the placeholder ``Makefile`` zlib commits and ``./configure`` replaces, keeps the
build's version, and ``make`` sees unchanged files as unchanged. A file the worktree no longer
has is deleted; everything else in the mirror is the build's and stays.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Callable
from pathlib import Path

SKIPPED_NAMES = frozenset({".git", ".codecoremorph"})
MANIFEST_NAME = ".codecoremorph-mirror.json"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _never(directory: Path, name: str) -> bool:
    return False


class Mirror:
    """The mirror at ``root`` of ``worktree``. ``skip_dir(parent, name)`` leaves a worktree
    directory out (build outputs a tool writes there)."""

    def __init__(
        self, worktree: Path, root: Path, *, skip_dir: Callable[[Path, str], bool] = _never
    ):
        self.worktree = worktree
        self.root = root
        self.skip_dir = skip_dir

    def worktree_files(self) -> dict[str, Path]:
        """The worktree's files, by POSIX path: everything but ``.git``, CodeCoreMorph's
        store and what ``skip_dir`` leaves out."""
        found: dict[str, Path] = {}
        for current, dirs, files in os.walk(self.worktree):
            here = Path(current)
            dirs[:] = sorted(
                d for d in dirs if d not in SKIPPED_NAMES and not self.skip_dir(here, d)
            )
            for name in files:
                if name in SKIPPED_NAMES:
                    continue  # a worktree's .git is a file
                path = here / name
                found[path.relative_to(self.worktree).as_posix()] = path
        return found

    def _manifest(self) -> dict[str, str]:
        path = self.root / MANIFEST_NAME
        if not path.is_file():
            return {}
        data: object = json.loads(path.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}

    def _save(self, manifest: dict[str, str]) -> None:
        path = self.root / MANIFEST_NAME
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(manifest, indent=0, sort_keys=True), encoding="utf-8")
        tmp.replace(path)

    def sync(self) -> list[str]:
        """Bring the mirror up to the worktree; returns the paths copied or deleted."""
        self.root.mkdir(parents=True, exist_ok=True)
        before = self._manifest()
        after: dict[str, str] = {}
        changed: list[str] = []
        for rel, source in sorted(self.worktree_files().items()):
            digest = _digest(source)
            after[rel] = digest
            copy = self.root / rel
            if before.get(rel) == digest and copy.exists():
                continue
            copy.parent.mkdir(parents=True, exist_ok=True)
            if copy.is_dir():
                shutil.rmtree(copy)
            # A fresh modification time: make rebuilds what depends on a changed file.
            shutil.copyfile(source, copy)
            shutil.copymode(source, copy)
            changed.append(rel)
        for rel in sorted(set(before) - set(after)):
            (self.root / rel).unlink(missing_ok=True)
            changed.append(rel)
        self._save(after)
        return sorted(changed)

    def wipe(self) -> None:
        """Remove the mirror and everything the build wrote in it."""
        shutil.rmtree(self.root, ignore_errors=True)

    def path(self, rel: str) -> Path:
        return self.root / rel
