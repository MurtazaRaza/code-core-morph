"""Finding an edit's ``search`` text (SPEC §7.4 rule 1).

1. Exact: ``search`` must occur exactly once (overlapping occurrences count).
2. Whitespace-tolerant: if there is no exact occurrence, compare whole lines with
   indentation and trailing whitespace ignored; still exactly one place must match. The
   replacement is re-indented by the difference in indentation.
3. Otherwise the edit is rejected, with the most similar region of the file (when there is
   a reasonably similar one) or the line numbers of the ambiguous matches.

All text here is normalized (LF line endings).
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

from codecoremorph.core.models.edits import CandidateRegion, RejectionReason

_MAX_REPORTED_MATCHES = 10
_MIN_CANDIDATE_SIMILARITY = 0.5
_MAX_CANDIDATE_LINES = 40
_CANDIDATE_SEARCH_BUDGET = 2_000_000  # window count x window size, to bound the cost


@dataclass(frozen=True)
class Match:
    start: int
    end: int
    replacement: str
    tolerant: bool


@dataclass(frozen=True)
class MatchFailure:
    reason: RejectionReason
    message: str
    lines: list[int]
    candidate: CandidateRegion | None


def _line_starts(text: str) -> list[int]:
    starts = [0]
    starts.extend(i + 1 for i, c in enumerate(text) if c == "\n")
    return starts


def _find_all(text: str, needle: str) -> list[int]:
    found: list[int] = []
    start = text.find(needle)
    while start != -1 and len(found) <= _MAX_REPORTED_MATCHES:
        found.append(start)
        start = text.find(needle, start + 1)
    return found


def _indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


def find_match(text: str, search: str, replace: str) -> Match | MatchFailure:
    exact = _find_all(text, search)
    if len(exact) == 1:
        return Match(exact[0], exact[0] + len(search), replace, tolerant=False)
    if len(exact) > 1:
        return _ambiguous(text, exact)
    tolerant = _tolerant_match(text, search, replace)
    if tolerant is not None:
        return tolerant
    return MatchFailure(
        reason=RejectionReason.NOT_FOUND,
        message="the search text was not found (not even with indentation ignored); copy it "
        "exactly from the current file contents",
        lines=[],
        candidate=_closest_region(text, search),
    )


def _ambiguous(text: str, offsets: list[int]) -> MatchFailure:
    lines = sorted({text.count("\n", 0, o) + 1 for o in offsets[:_MAX_REPORTED_MATCHES]})
    shown = ", ".join(map(str, lines))
    more = "+" if len(offsets) > _MAX_REPORTED_MATCHES else ""
    return MatchFailure(
        reason=RejectionReason.AMBIGUOUS,
        message=f"the search text matches {len(offsets)}{more} places (lines {shown}); "
        "include more surrounding lines so it matches exactly once",
        lines=lines,
        candidate=None,
    )


def _split_search(search: str) -> tuple[list[str], bool]:
    trailing_newline = search.endswith("\n")
    lines = (search[:-1] if trailing_newline else search).split("\n")
    return lines, trailing_newline


def _tolerant_match(text: str, search: str, replace: str) -> Match | MatchFailure | None:
    search_lines, trailing_newline = _split_search(search)
    keys = [line.strip() for line in search_lines]
    if not any(keys):
        return None  # whitespace-only search texts can't be matched loosely
    file_lines = text.split("\n")
    starts = _line_starts(text)
    n = len(keys)
    hits = [
        i
        for i in range(len(file_lines) - n + 1)
        if file_lines[i].strip() == keys[0]
        and all(file_lines[i + j].strip() == keys[j] for j in range(1, n))
    ]
    if not hits:
        return None
    if len(hits) > 1:
        return _ambiguous(text, [starts[i] for i in hits])
    first = hits[0]
    start = starts[first]
    end = starts[first + n - 1] + len(file_lines[first + n - 1])

    replacement = replace[:-1] if trailing_newline and replace.endswith("\n") else replace
    anchor = next(j for j, key in enumerate(keys) if key)
    old_indent, new_indent = _indent(search_lines[anchor]), _indent(file_lines[first + anchor])
    if old_indent != new_indent:
        replacement = "\n".join(
            new_indent + line[len(old_indent) :] if line.startswith(old_indent) and line else line
            for line in replacement.split("\n")
        )
    return Match(start, end, replacement, tolerant=True)


def _closest_region(text: str, search: str) -> CandidateRegion | None:
    search_lines, _ = _split_search(search)
    file_lines = text.split("\n")
    n = max(1, len(search_lines))
    windows = max(1, len(file_lines) - n + 1)
    if windows * n > _CANDIDATE_SEARCH_BUDGET:
        return None
    target = "\n".join(line.strip() for line in search_lines)
    stripped = [line.strip() for line in file_lines]
    best_ratio, best_start = 0.0, 0
    for i in range(windows):
        matcher = SequenceMatcher(None, target, "\n".join(stripped[i : i + n]), autojunk=False)
        if matcher.real_quick_ratio() <= best_ratio or matcher.quick_ratio() <= best_ratio:
            continue
        ratio = matcher.ratio()
        if ratio > best_ratio:
            best_ratio, best_start = ratio, i
    if best_ratio < _MIN_CANDIDATE_SIMILARITY:
        return None
    region = file_lines[best_start : best_start + n][:_MAX_CANDIDATE_LINES]
    return CandidateRegion(
        start_line=best_start + 1,
        end_line=best_start + len(region),
        text="\n".join(region),
        similarity=round(best_ratio, 3),
    )
