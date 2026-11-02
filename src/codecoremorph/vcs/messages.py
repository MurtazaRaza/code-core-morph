"""Commit messages for migration units (SPEC §7.9).

```
codecoremorph(replace_typedefs_with_using): src/geometry/vector.{h,cpp}

Replaced 12 typedef declarations with alias declarations.

CodeCoreMorph-Run: 20261102-141500-a3f9
CodeCoreMorph-Unit: U-0042
CodeCoreMorph-Rules: replace_typedefs_with_using
CodeCoreMorph-Attempts: 2
CodeCoreMorph-Models: anthropic/claude-opus-5-5
```
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Mapping, Sequence

TRAILER_PREFIX = "CodeCoreMorph-"
_TRAILER_RE = re.compile(r"^([A-Za-z][A-Za-z0-9-]*): (.*)$")


def summarize_paths(paths: Sequence[str], limit: int = 3) -> str:
    """A short, deterministic description of a set of paths for a commit subject."""
    unique = sorted(set(paths))
    if not unique:
        return "no files"
    if len(unique) == 1:
        return unique[0]
    stems = {posixpath.splitext(p)[0] for p in unique}
    extensions = [posixpath.splitext(p)[1].lstrip(".") for p in unique]
    if len(stems) == 1 and all(extensions):
        return f"{stems.pop()}.{{{','.join(extensions)}}}"
    if len(unique) <= limit:
        return ", ".join(unique)
    common = posixpath.commonpath(unique)
    return f"{len(unique)} files in {common}/" if common else f"{len(unique)} files"


def with_trailers(message: str, trailers: Mapping[str, str]) -> str:
    """``message`` followed by a blank line and ``Key: value`` trailer lines."""
    body = message.rstrip("\n")
    lines = [f"{key}: {value}" for key, value in trailers.items()]
    for line in lines:
        if "\n" in line or not _TRAILER_RE.match(line):
            raise ValueError(f"invalid commit trailer {line!r}")
    return f"{body}\n\n" + "\n".join(lines) + "\n" if lines else body + "\n"


def unit_commit_message(
    *,
    run_id: str,
    unit_id: str,
    rules: Sequence[str],
    files: Sequence[str],
    summary: str,
    attempts: int,
    models: Sequence[str],
) -> str:
    subject = f"codecoremorph({','.join(rules)}): {summarize_paths(files)}"
    trailers = {
        f"{TRAILER_PREFIX}Run": run_id,
        f"{TRAILER_PREFIX}Unit": unit_id,
        f"{TRAILER_PREFIX}Rules": ", ".join(rules),
        f"{TRAILER_PREFIX}Attempts": str(attempts),
        f"{TRAILER_PREFIX}Models": ", ".join(models) if models else "none",
    }
    text = subject if not summary.strip() else f"{subject}\n\n{summary.strip()}"
    return with_trailers(text, trailers)


def parse_trailers(message: str) -> dict[str, str]:
    """The trailers in the last paragraph of a commit message."""
    paragraphs = message.strip().split("\n\n")
    trailers: dict[str, str] = {}
    for line in paragraphs[-1].splitlines() if len(paragraphs) > 1 else []:
        match = _TRAILER_RE.match(line)
        if match is None:
            return {}
        trailers[match.group(1)] = match.group(2)
    return trailers
