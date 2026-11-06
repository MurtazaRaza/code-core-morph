"""The ``dotnet-format`` codemod's pieces (SPEC §11.3, D-65): the style options it writes into
the mirror's ``.editorconfig``, its command line, and keeping a rewritten file's encoding and
line endings.

``dotnet format style --diagnostics <ids>`` fixes only those diagnostics, and only where the
options that make them fire are set (``csharp_style_namespace_declarations = file_scoped``
for IDE0161), so the rule's options go into the mirror's top ``.editorconfig``, after the
project's own (a later section wins), with the diagnostics at warning.
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path

from codecoremorph.patching.textfile import BinaryFileError, TextFile

EDITORCONFIG = ".editorconfig"
_ROOT = re.compile(r"^\s*root\s*=\s*true\s*$", re.IGNORECASE | re.MULTILINE)
_DIAGNOSTIC = re.compile(r"^[A-Z]{2,}\d{4}$")


def check_ids(checks: list[str]) -> list[str]:
    """The diagnostic IDs of a rule's ``checks`` (``IDE0161``); anything else is refused."""
    bad = [c for c in checks if not _DIAGNOSTIC.match(c)]
    if bad:
        raise ValueError(f"not diagnostic IDs: {', '.join(bad)}")
    return list(checks)


def editorconfig(existing: str | None, options: dict[str, str], checks: list[str]) -> str:
    """The mirror's top ``.editorconfig``: the project's own (``root = true`` added, since the
    mirror's parents aren't the worktree's), then a ``[*.cs]`` section with the rule's options
    and its diagnostics at warning."""
    text = existing or ""
    newline = "\r\n" if "\r\n" in text else "\n"
    if not _ROOT.search(text):
        text = "root = true" + newline + newline + text
    if text and not text.endswith(("\n", "\r")):
        text += newline
    lines = ["", "# Written by CodeCoreMorph for a codemod, in its mirror only.", "[*.cs]"]
    lines += [f"{key} = {value}" for key, value in sorted(options.items())]
    lines += [f"dotnet_diagnostic.{check}.severity = warning" for check in checks]
    return text + newline.join(lines) + newline


def write_editorconfig(mirror: Path, options: dict[str, str], checks: list[str]) -> None:
    path = mirror / EDITORCONFIG
    existing: str | None = None
    if path.is_file():
        try:
            existing = TextFile.decode(path.read_bytes()).text
        except BinaryFileError:
            existing = None
    path.write_text(editorconfig(existing, options, checks), encoding="utf-8", newline="")


def format_arguments(entry: str, files: list[str], checks: list[str]) -> list[str]:
    """``dotnet format style``'s arguments for the unit's ``files`` (paths from the mirror)."""
    return [
        "format",
        "style",
        entry,
        "--no-restore",
        "--severity",
        "info",
        "--verbosity",
        "normal",
        "--diagnostics",
        *checks,
        "--include",
        *files,
    ]


def keep_style(old: bytes, new: bytes) -> bytes:
    """``new`` in ``old``'s encoding (a BOM kept) and line endings."""
    try:
        before = TextFile.decode(old)
        after = TextFile.decode(new)
    except BinaryFileError:
        return new
    text = after.text
    if "\r\n" in before.text and "\r\n" not in text:
        text = text.replace("\n", "\r\n")
    elif "\r\n" not in before.text and "\r\n" in text:
        text = text.replace("\r\n", "\n")
    return before.encode(text) if before.can_encode(text) else new


def changed_ranges(before: bytes, after: bytes) -> list[tuple[int, int]]:
    try:
        old = TextFile.decode(before).text.splitlines()
        new = TextFile.decode(after).text.splitlines()
    except BinaryFileError:
        return []
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    return [(j1 + 1, j2) for tag, _, _, j1, j2 in matcher.get_opcodes() if tag != "equal"]
