"""The mirror Maven builds in (D-63).

Maven writes ``target/`` beside every POM, and the run worktree's ``git add --all`` would
commit it, so the Java adapter keeps a copy of the worktree in its cache directory and runs
Maven there. :func:`sync` makes the copy's files equal the worktree's, copying only what
changed (Maven's up-to-date checks see unchanged files as unchanged) and deleting what the
worktree no longer has, but keeps the build outputs: ``target/`` beside a POM.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

SKIPPED_NAMES = frozenset({".git", ".codecoremorph"})
OUTPUT_DIR = "target"


def _is_output(directory: Path, name: str) -> bool:
    return name == OUTPUT_DIR and (directory / "pom.xml").is_file()


def project_files(root: Path) -> dict[str, Path]:
    """The worktree's files Maven may read, by POSIX path from ``root``: everything but
    ``.git``, CodeCoreMorph's store and ``target/`` beside a POM."""
    found: dict[str, Path] = {}
    for current, dirs, files in os.walk(root):
        here = Path(current)
        dirs[:] = sorted(d for d in dirs if d not in SKIPPED_NAMES and not _is_output(here, d))
        for name in files:
            if name in SKIPPED_NAMES:
                continue  # a worktree's .git is a file
            path = here / name
            found[path.relative_to(root).as_posix()] = path
    return found


def _same(source: Path, copy: Path) -> bool:
    try:
        if source.stat().st_size != copy.stat().st_size:
            return False
        return source.read_bytes() == copy.read_bytes()
    except OSError:
        return False


def sync(worktree: Path, mirror: Path) -> list[str]:
    """Make ``mirror`` hold the worktree's files; returns the paths copied or deleted."""
    mirror.mkdir(parents=True, exist_ok=True)
    wanted = project_files(worktree)
    present = project_files(mirror)
    changed: list[str] = []
    for rel, source in wanted.items():
        copy = mirror / rel
        if rel in present and _same(source, copy):
            continue
        copy.parent.mkdir(parents=True, exist_ok=True)
        if copy.is_dir():
            shutil.rmtree(copy)
        shutil.copyfile(source, copy)
        changed.append(rel)
    for rel in sorted(set(present) - set(wanted)):
        (mirror / rel).unlink(missing_ok=True)
        changed.append(rel)
    _prune_empty(mirror)
    return sorted(changed)


def _prune_empty(root: Path) -> None:
    for current, _dirs, _files in os.walk(root, topdown=False):
        here = Path(current)
        try:
            if here != root and not any(here.iterdir()):
                here.rmdir()
        except OSError:
            continue


def output_dirs(mirror: Path) -> list[Path]:
    """Every ``target/`` beside a POM in the mirror."""
    found: list[Path] = []
    for current, dirs, files in os.walk(mirror):
        here = Path(current)
        if "pom.xml" in files and OUTPUT_DIR in dirs:
            found.append(here / OUTPUT_DIR)
        dirs[:] = [d for d in dirs if d not in SKIPPED_NAMES and d != OUTPUT_DIR]
    return found


def clean(mirror: Path) -> None:
    """Remove the build outputs, so the next build compiles everything."""
    for directory in output_dirs(mirror):
        shutil.rmtree(directory, ignore_errors=True)
