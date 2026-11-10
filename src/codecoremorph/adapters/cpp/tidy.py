"""Which of a port's source files clang-tidy has to look at for diagnostics in some of its
files (a unit's new code), so a unit's ``lint_clean`` doesn't lint every file earlier units
wrote: at libsodium-core's last unit that was 165 s of 98 files, against 2.5 s for its own
(M11)."""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from pathlib import Path, PurePosixPath

from codecoremorph.patching.textfile import BinaryFileError, TextFile

HEADER_EXTENSIONS = (".h", ".hpp", ".hh", ".hxx", ".inl")
_INCLUDE = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]', re.MULTILINE)


def lint_scope(
    worktree: Path, compiled: Sequence[str], wanted: Collection[str] | None
) -> list[str]:
    """The files of ``compiled`` (worktree-relative, what clang-tidy can check) that cover
    ``wanted``: those of them it compiles, and for a header, its own source (the same name
    beside it) or else a compiled file that includes it; in ``compiled``'s order. All of
    ``compiled`` when ``wanted`` is None, or when nothing compiled includes a wanted header, as
    far as its ``#include`` lines tell."""
    if wanted is None:
        return list(compiled)
    chosen = [f for f in compiled if f in wanted]
    for header in sorted(w for w in wanted if w.endswith(HEADER_EXTENSIONS)):
        stem = PurePosixPath(header).with_suffix("").as_posix()
        name = PurePosixPath(header).name
        own = [f for f in compiled if PurePosixPath(f).with_suffix("").as_posix() == stem]
        if any(f in chosen for f in own) or any(_includes(worktree, f, name) for f in chosen):
            continue
        if own:
            chosen.append(own[0])
            continue
        includer = next((f for f in compiled if _includes(worktree, f, name)), None)
        if includer is None:
            return list(compiled)
        chosen.append(includer)
    return [f for f in compiled if f in chosen]


def _includes(worktree: Path, rel: str, name: str) -> bool:
    path = worktree / rel
    try:
        text = TextFile.decode(path.read_bytes()).text
    except (OSError, BinaryFileError):
        return False
    return any(PurePosixPath(m).name == name for m in _INCLUDE.findall(text))
