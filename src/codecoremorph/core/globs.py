"""Gitignore-style path patterns, as used by ``project.exclude`` and friends.

- ``*`` and ``?`` never cross a ``/``; ``**`` does; ``[abc]`` and ``[!abc]`` are classes.
- A pattern without a ``/`` (other than a trailing one) matches a name at any depth:
  ``*.pb.cc`` matches ``src/a/b.pb.cc``.
- A pattern containing a ``/`` is anchored at the root: ``third_party/**``.
- A directory match covers everything below it: ``third_party`` matches ``third_party/x/y.h``.

Matching is case-sensitive on every platform, like git, so results don't depend on the host.
"""

from __future__ import annotations

import re
from collections.abc import Iterable


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    text = pattern.strip().replace("\\", "/")
    anchored = "/" in text.rstrip("/")
    text = text.strip("/")
    if not anchored:
        text = "**/" + text
    out: list[str] = []
    i = 0
    while i < len(text):
        if text.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif text.startswith("**", i):
            out.append(".*")
            i += 2
        elif text[i] == "*":
            out.append("[^/]*")
            i += 1
        elif text[i] == "?":
            out.append("[^/]")
            i += 1
        elif text[i] == "[" and (end := text.find("]", i + 2)) != -1:
            body = text[i + 1 : end]
            if body.startswith("!"):
                body = "^" + body[1:]
            out.append("[" + body.replace("\\", "\\\\") + "]")
            i = end + 1
        else:
            out.append(re.escape(text[i]))
            i += 1
    return re.compile("".join(out))


class PathMatcher:
    """Matches worktree-relative POSIX paths against a set of patterns."""

    def __init__(self, patterns: Iterable[str] = ()):
        self.patterns = tuple(p for p in patterns if p.strip())
        self._regexes = tuple(glob_to_regex(p) for p in self.patterns)

    def __bool__(self) -> bool:
        return bool(self._regexes)

    def matches(self, path: str) -> bool:
        parts = path.strip("/").split("/")
        for depth in range(1, len(parts) + 1):
            prefix = "/".join(parts[:depth])
            if any(rx.fullmatch(prefix) for rx in self._regexes):
                return True
        return False

    def first_match(self, path: str) -> str | None:
        """The first pattern that matches ``path``, for messages."""
        for pattern, rx in zip(self.patterns, self._regexes, strict=True):
            parts = path.strip("/").split("/")
            if any(rx.fullmatch("/".join(parts[:d])) for d in range(1, len(parts) + 1)):
                return pattern
        return None
