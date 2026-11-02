"""clang-tidy fixes: parse ``--export-fixes`` YAML and apply the replacements.

CodeCoreMorph applies the replacements itself instead of running ``clang-tidy -fix`` or
``clang-apply-replacements``. Fixes in a header are exported once per translation unit that
includes it, so identical replacements are de-duplicated; overlapping ones are conflicts, and
the whole diagnostic they belong to is skipped, as clang-apply-replacements does. Offsets are
byte offsets, and bytes outside a replacement never change. A replacement inserted into a CRLF
file gets CRLF line breaks.
"""

from __future__ import annotations

import fnmatch
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, cast

import yaml

_CSafeLoader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


@dataclass(frozen=True, order=True)
class Replacement:
    path: str  # as clang-tidy printed it (a sandbox path)
    offset: int
    length: int
    text: str


@dataclass(frozen=True)
class Fix:
    check: str
    message: str
    path: str | None
    offset: int | None
    replacements: tuple[Replacement, ...]


@dataclass
class ApplyOutcome:
    """What applying a set of fixes did, per file (keyed by the caller's path key)."""

    new_contents: dict[str, bytes] = field(default_factory=dict[str, bytes])
    applied: dict[str, int] = field(default_factory=dict[str, int])
    skipped: list[tuple[str, str, str]] = field(
        default_factory=list[tuple[str, str, str]]
    )  # (path, check, reason)


def parse_fixes(text: str) -> list[Fix]:
    """The diagnostics in one or more ``--export-fixes`` YAML documents."""
    fixes: list[Fix] = []
    for document in yaml.load_all(text, Loader=_CSafeLoader):
        if not isinstance(document, dict):
            continue
        doc = cast(dict[str, Any], document)
        for raw in doc.get("Diagnostics") or []:
            if not isinstance(raw, dict):
                continue
            diag = cast(dict[str, Any], raw)
            message = cast(dict[str, Any], diag.get("DiagnosticMessage") or {})
            replacements = tuple(
                Replacement(
                    path=str(r["FilePath"]),
                    offset=int(r["Offset"]),
                    length=int(r["Length"]),
                    text=str(r.get("ReplacementText") or ""),
                )
                for r in cast(list[dict[str, Any]], message.get("Replacements") or [])
            )
            fixes.append(
                Fix(
                    check=str(diag.get("DiagnosticName") or ""),
                    message=str(message.get("Message") or ""),
                    path=str(message["FilePath"]) if message.get("FilePath") else None,
                    offset=int(message["FileOffset"]) if "FileOffset" in message else None,
                    replacements=replacements,
                )
            )
    return fixes


def _crlf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\n", "\r\n")


def apply_fixes(
    fixes: Iterable[Fix],
    *,
    key_of: Callable[[str], str | None],
    read: Callable[[str], bytes],
) -> ApplyOutcome:
    """Apply ``fixes`` in memory.

    ``key_of`` maps a clang-tidy path to the caller's key for the file, or None when the file
    may not be changed (outside the unit); a fix touching such a file is skipped whole.
    ``read`` returns a file's current bytes, by key.
    """
    outcome = ApplyOutcome()
    accepted: dict[str, set[tuple[int, int, str]]] = defaultdict(set)
    for fix in fixes:
        if not fix.replacements:
            continue
        keyed: list[tuple[str, Replacement]] = []
        blocked: str | None = None
        for replacement in fix.replacements:
            key = key_of(replacement.path)
            if key is None:
                blocked = replacement.path
                break
            keyed.append((key, replacement))
        if blocked is not None:
            outcome.skipped.append((blocked, fix.check, "the fix changes a file outside the unit"))
            continue
        spans = {(k, (r.offset, r.length, r.text)) for k, r in keyed}
        if all(span in accepted[k] for k, span in spans):
            continue  # the same fix, exported again from another translation unit
        if any(_overlaps(accepted[k], span) for k, span in spans):
            path = keyed[0][0]
            outcome.skipped.append((path, fix.check, "conflicts with another fix"))
            continue
        for key, span in spans:
            accepted[key].add(span)

    for key, spans in accepted.items():
        data = read(key)
        crlf = data.count(b"\r\n") * 2 > data.count(b"\n")
        result = bytearray(data)
        for offset, length, text in sorted(spans, reverse=True):
            new_text = _crlf(text) if crlf else text
            result[offset : offset + length] = new_text.encode("utf-8")
        outcome.new_contents[key] = bytes(result)
        outcome.applied[key] = len(spans)
    return outcome


def _overlaps(existing: set[tuple[int, int, str]], span: tuple[int, int, str]) -> bool:
    offset, length, _text = span
    end = offset + length
    for other in existing:
        if other == span:
            continue
        other_offset, other_length, _other_text = other
        other_end = other_offset + other_length
        # Two insertions at the same point conflict; touching ranges don't.
        if offset < other_end and other_offset < end:
            return True
        if length == 0 and other_length == 0 and offset == other_offset:
            return True
    return False


def fixed_sites(
    fixes: Iterable[Fix], *, key_of: Callable[[str], str | None], read: Callable[[str], bytes]
) -> set[tuple[str, int, str]]:
    """``(file key, line, check)`` of every diagnostic that came with a fix. Call it before
    applying the fixes: offsets refer to the original bytes."""
    sites: set[tuple[str, int, str]] = set()
    contents: dict[str, bytes] = {}
    for fix in fixes:
        if not fix.replacements or fix.path is None or fix.offset is None:
            continue
        key = key_of(fix.path)
        if key is None:
            continue
        if key not in contents:
            contents[key] = read(key)
        sites.add((key, contents[key].count(b"\n", 0, fix.offset) + 1, fix.check))
    return sites


def requested_check(check: str, checks: Iterable[str]) -> bool:
    """Whether ``check`` is one of ``checks`` (clang-tidy globs such as ``modernize-*``;
    ``-name`` entries exclude)."""
    selected = False
    for pattern in checks:
        negative = pattern.startswith("-")
        if fnmatch.fnmatchcase(check, pattern.removeprefix("-")):
            selected = not negative
    return selected
