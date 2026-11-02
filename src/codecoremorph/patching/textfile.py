"""Text files as PatchEngine sees them: encoding, BOM and line endings preserved (§7.4 rule 3).

Edits run against a *normalized view* of the text, where every CRLF is LF, and offsets map
back to the raw text. Replacements are spliced into the raw text, so bytes outside an edit
never change, even in files with mixed line endings.
"""

from __future__ import annotations

import codecs
from bisect import bisect_left
from dataclasses import dataclass

_BOMS: tuple[tuple[bytes, str], ...] = (
    (codecs.BOM_UTF32_LE, "utf-32-le"),  # before UTF-16 LE: it starts with the same bytes
    (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF8, "utf-8"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)
# Without a BOM: UTF-8 if it decodes, else the common legacy Windows code page, else
# Latin-1, which decodes any byte and round-trips exactly.
_FALLBACK_ENCODINGS = ("utf-8", "cp1252", "latin-1")
_BINARY_SNIFF = 8192


class BinaryFileError(ValueError):
    pass


@dataclass(frozen=True)
class TextFile:
    encoding: str
    bom: bytes
    text: str  # decoded, BOM removed, raw line endings

    @classmethod
    def decode(cls, data: bytes) -> TextFile:
        for bom, encoding in _BOMS:
            if data.startswith(bom):
                return cls(encoding, bom, data[len(bom) :].decode(encoding))
        if b"\0" in data[:_BINARY_SNIFF]:
            raise BinaryFileError("the file looks binary (it contains NUL bytes)")
        for encoding in _FALLBACK_ENCODINGS:
            try:
                return cls(encoding, b"", data.decode(encoding))
            except UnicodeDecodeError:
                continue
        raise AssertionError("latin-1 decodes every byte")

    def encode(self, text: str) -> bytes:
        """Encode with this file's encoding and BOM. Raises ``UnicodeEncodeError``."""
        return self.bom + text.encode(self.encoding)

    def can_encode(self, text: str) -> bool:
        try:
            text.encode(self.encoding)
        except UnicodeEncodeError:
            return False
        return True


def majority_newline(raw: str) -> str:
    crlf = raw.count("\r\n")
    return "\r\n" if crlf > raw.count("\n") - crlf else "\n"


class NormalizedView:
    """``raw`` with CRLF turned into LF, plus a map from normalized offsets to raw ones."""

    def __init__(self, raw: str):
        self.raw = raw
        self.text = raw.replace("\r\n", "\n")
        # Normalized offsets of the "\n" characters that were "\r\n" in the raw text.
        self._crlf: list[int] = []
        start = removed = 0
        while (k := raw.find("\r\n", start)) != -1:
            self._crlf.append(k - removed)
            removed += 1
            start = k + 2

    def to_raw(self, offset: int) -> int:
        return offset + bisect_left(self._crlf, offset)

    def line_of(self, offset: int) -> int:
        """1-based line number of a normalized offset."""
        return self.text.count("\n", 0, offset) + 1

    def splice(self, start: int, end: int, replacement: str) -> str:
        """Raw text with normalized ``[start, end)`` replaced. ``replacement`` uses LF and is
        converted to the line ending of the region it replaces (or the file's majority)."""
        raw_start, raw_end = self.to_raw(start), self.to_raw(end)
        region = self.raw[raw_start:raw_end]
        if "\r\n" in region:
            newline = "\r\n"
        elif "\n" in region:
            newline = "\n"
        else:
            newline = majority_newline(self.raw)
        converted = replacement.replace("\r\n", "\n").replace("\n", newline)
        return self.raw[:raw_start] + converted + self.raw[raw_end:]
