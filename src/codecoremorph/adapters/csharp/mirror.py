"""The mirror ``dotnet`` builds in (D-65).

``dotnet`` writes ``bin/`` and ``obj/`` beside every project, and the run worktree's
``git add --all`` would commit them, so the C# adapter keeps a copy of the worktree in its cache
directory and builds there, as the Java adapter does for Maven (D-63). :func:`sync` makes the
copy's files equal the worktree's, copying only what changed (MSBuild's up-to-date checks see
unchanged files as unchanged) and deleting what the worktree no longer has, but keeps the build
outputs: ``bin/`` and ``obj/`` beside a project file, which also hold the restore's records.

The copy differs from the worktree in one way: ``global.json`` loses its SDK pin, since the
toolchain's SDK builds every project (a pin to an older SDK can't be met).
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, cast

from codecoremorph.adapters.csharp.msbuild import OUTPUT_DIRS, is_project

SKIPPED_NAMES = frozenset({".git", ".codecoremorph", ".vs"})


def _is_output(directory: Path, name: str, names: list[str]) -> bool:
    return name.lower() in OUTPUT_DIRS and any(is_project(n) for n in names)


def project_files(root: Path) -> dict[str, Path]:
    """The worktree's files ``dotnet`` may read, by POSIX path from ``root``: everything but
    ``.git``, CodeCoreMorph's store and the outputs beside a project."""
    found: dict[str, Path] = {}
    for current, dirs, files in os.walk(root):
        here = Path(current)
        dirs[:] = sorted(
            d for d in dirs if d not in SKIPPED_NAMES and not _is_output(here, d, files)
        )
        for name in files:
            if name in SKIPPED_NAMES:
                continue  # a worktree's .git is a file
            path = here / name
            found[path.relative_to(root).as_posix()] = path
    return found


def mirrored(rel: str, data: bytes) -> bytes:
    """What the mirror holds for the worktree's ``rel``: the same bytes, but ``global.json``
    without its ``sdk`` section."""
    if rel.rsplit("/", 1)[-1].lower() != "global.json":
        return data
    try:
        document = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        return data
    if not isinstance(document, dict) or "sdk" not in document:
        return data
    kept = {k: v for k, v in cast(dict[str, Any], document).items() if k != "sdk"}
    return (json.dumps(kept, indent=2) + "\n").encode("utf-8")


def sync(worktree: Path, mirror: Path) -> list[str]:
    """Make ``mirror`` hold the worktree's files; returns the paths copied or deleted."""
    mirror.mkdir(parents=True, exist_ok=True)
    wanted = project_files(worktree)
    present = project_files(mirror)
    changed: list[str] = []
    for rel, source in wanted.items():
        copy = mirror / rel
        data = mirrored(rel, source.read_bytes())
        if rel in present:
            try:
                if copy.stat().st_size == len(data) and copy.read_bytes() == data:
                    continue
            except OSError:
                pass
        copy.parent.mkdir(parents=True, exist_ok=True)
        if copy.is_dir():
            shutil.rmtree(copy)
        copy.write_bytes(data)
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
    """Every ``bin/`` and ``obj/`` beside a project in the mirror."""
    found: list[Path] = []
    for current, dirs, files in os.walk(mirror):
        here = Path(current)
        if any(is_project(n) for n in files):
            found += [here / d for d in dirs if d.lower() in OUTPUT_DIRS]
            dirs[:] = [d for d in dirs if d.lower() not in OUTPUT_DIRS]
        dirs[:] = [d for d in dirs if d not in SKIPPED_NAMES]
    return found
