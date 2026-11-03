"""Pattern detectors: regular expressions over source text with comments and string literals
blanked out, so a ``typedef`` in a comment is not a site (SPEC §7.3).

Masking keeps every offset and line break, so match positions map straight back to the file.
The C-family scanner covers C, C++, Java and C#; raw strings (``R"x(...)x"``) are C++.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from codecoremorph.core.models import RuleSite

_RAW_STRING = re.compile(r'R"([^ ()\\\t\n]{0,16})\(')


def _blank(text: str) -> str:
    return "".join(c if c in "\r\n" else " " for c in text)


def mask_c_family(text: str, *, strings: bool = True) -> str:
    """``text`` with comments, string and character literals replaced by spaces; with
    ``strings=False``, only the comments (literals are kept, so a ``//`` in one stays)."""
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if c == "/" and nxt == "/":
            end = text.find("\n", i)
            end = n if end < 0 else end
            out.append(_blank(text[i:end]))
            i = end
        elif c == "/" and nxt == "*":
            end = text.find("*/", i + 2)
            end = n if end < 0 else end + 2
            out.append(_blank(text[i:end]))
            i = end
        elif (
            c == "R"
            and nxt == '"'
            and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] == "_"))
        ):
            match = _RAW_STRING.match(text, i)
            if match is None:
                out.append(c)
                i += 1
                continue
            closing = ")" + match.group(1) + '"'
            end = text.find(closing, match.end())
            end = n if end < 0 else end + len(closing)
            out.append(_blank(text[i:end]) if strings else text[i:end])
            i = end
        elif c in "\"'":
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            end = min(j + 1, n)
            if strings:
                inner = _blank(text[i + 1 : end - 1])
                out.append(c + inner + (text[end - 1] if end - 1 > i else ""))
            else:
                out.append(text[i:end])
            i = end
        else:
            out.append(c)
            i += 1
    return "".join(out)


def find_pattern_sites(path: str, text: str, patterns: Iterable[str]) -> list[RuleSite]:
    """Every match of ``patterns`` in the code of ``text`` (comments and strings ignored)."""
    masked = mask_c_family(text)
    line_starts = [0] + [m.end() for m in re.finditer("\n", text)]
    sites: dict[tuple[int, int], RuleSite] = {}
    for pattern in patterns:
        for match in re.finditer(pattern, masked, flags=re.MULTILINE):
            offset = match.start()
            line = _line_of(line_starts, offset)
            column = offset - line_starts[line - 1] + 1
            snippet = text[line_starts[line - 1] :].split("\n", 1)[0].strip()
            sites.setdefault(
                (line, column), RuleSite(file=path, line=line, column=column, text=snippet)
            )
    return [sites[key] for key in sorted(sites)]


def _line_of(line_starts: list[int], offset: int) -> int:
    lo, hi = 0, len(line_starts)
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if line_starts[mid] <= offset:
            lo = mid
        else:
            hi = mid
    return lo + 1
