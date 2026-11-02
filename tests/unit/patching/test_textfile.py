from __future__ import annotations

import codecs

import pytest
from hypothesis import given
from hypothesis import strategies as st

from codecoremorph.patching.textfile import (
    BinaryFileError,
    NormalizedView,
    TextFile,
    majority_newline,
)


@pytest.mark.parametrize(
    ("data", "encoding", "bom", "text"),
    [
        (b"plain\n", "utf-8", b"", "plain\n"),
        (codecs.BOM_UTF8 + b"x\r\n", "utf-8", codecs.BOM_UTF8, "x\r\n"),
        (
            codecs.BOM_UTF16_LE + "hé\n".encode("utf-16-le"),
            "utf-16-le",
            codecs.BOM_UTF16_LE,
            "hé\n",
        ),
        (codecs.BOM_UTF16_BE + "a".encode("utf-16-be"), "utf-16-be", codecs.BOM_UTF16_BE, "a"),
        (codecs.BOM_UTF32_LE + "a".encode("utf-32-le"), "utf-32-le", codecs.BOM_UTF32_LE, "a"),
        ("café ✓".encode(), "utf-8", b"", "café ✓"),
        (b"caf\xe9 \x80", "cp1252", b"", "café €"),
        (b"\x81\x8d", "latin-1", b"", "\x81\x8d"),  # undefined in cp1252
    ],
)
def test_decode_and_round_trip(data: bytes, encoding: str, bom: bytes, text: str) -> None:
    decoded = TextFile.decode(data)
    assert (decoded.encoding, decoded.bom, decoded.text) == (encoding, bom, text)
    assert decoded.encode(decoded.text) == data


def test_binary_files_are_refused() -> None:
    with pytest.raises(BinaryFileError):
        TextFile.decode(b"\x7fELF\x00\x00\x01")


def test_can_encode() -> None:
    assert TextFile.decode(b"caf\xe9").can_encode("€")
    assert not TextFile.decode(b"caf\xe9").can_encode("✓")
    assert TextFile.decode(b"x").can_encode("✓")


def test_majority_newline() -> None:
    assert majority_newline("a\r\nb\r\nc\n") == "\r\n"
    assert majority_newline("a\nb\nc\r\n") == "\n"
    assert majority_newline("no newline") == "\n"


def test_view_maps_offsets_back_to_raw() -> None:
    view = NormalizedView("ab\r\ncd\nef\r\n")
    assert view.text == "ab\ncd\nef\n"
    assert [view.to_raw(i) for i in range(len(view.text) + 1)] == [0, 1, 2, 4, 5, 6, 7, 8, 9, 11]
    assert view.line_of(view.text.index("e")) == 3


@pytest.mark.parametrize(
    ("raw", "search", "replacement", "expected"),
    [
        ("a\r\nb\r\nc\r\n", "b\nc", "x\ny", "a\r\nx\r\ny\r\n"),  # region uses CRLF
        ("a\nb\r\nc\n", "a\nb", "1\n2\n3", "1\n2\n3\r\nc\n"),  # region uses LF
        ("a\r\nb\r\nc\n", "b", "x\ny", "a\r\nx\r\ny\r\nc\n"),  # one-line region: majority (CRLF)
        ("x\n", "x", "y", "y\n"),
    ],
)
def test_splice_uses_the_regions_line_ending(
    raw: str, search: str, replacement: str, expected: str
) -> None:
    view = NormalizedView(raw)
    start = view.text.index(search)
    assert view.splice(start, start + len(search), replacement) == expected


_line = st.text(alphabet=st.characters(blacklist_characters="\r\n"), max_size=8)


@given(
    st.lists(st.tuples(_line, st.sampled_from(["\n", "\r\n"])), min_size=1, max_size=12),
    st.data(),
)
def test_splice_never_changes_bytes_outside_the_region(
    lines: list[tuple[str, str]], data: st.DataObject
) -> None:
    raw = "".join(text + newline for text, newline in lines)
    view = NormalizedView(raw)
    start = data.draw(st.integers(0, len(view.text)))
    end = data.draw(st.integers(start, len(view.text)))
    result = view.splice(start, end, "<NEW>")
    raw_start, raw_end = view.to_raw(start), view.to_raw(end)
    assert result.startswith(raw[:raw_start])
    assert result.endswith(raw[raw_end:])
    assert result[raw_start : len(result) - (len(raw) - raw_end)] == "<NEW>"
