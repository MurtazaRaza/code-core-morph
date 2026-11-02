"""clang-tidy fix parsing and application."""

from __future__ import annotations

from codecoremorph.adapters.cpp.fixes import (
    Fix,
    Replacement,
    apply_fixes,
    fixed_sites,
    parse_fixes,
)

EXPORT = """---
MainSourceFile:  '/workspace/src/a.cpp'
Diagnostics:
  - DiagnosticName:  modernize-use-using
    DiagnosticMessage:
      Message:         'use ''using'' instead of ''typedef'''
      FilePath:        '/workspace/include/a.h'
      FileOffset:      0
      Replacements:
        - FilePath:        '/workspace/include/a.h'
          Offset:          0
          Length:          15
          ReplacementText: 'using X = int'
    Level:           Warning
  - DiagnosticName:  modernize-use-using
    DiagnosticMessage:
      Message:         'use ''using'' instead of ''typedef'''
      FilePath:        '/workspace/include/a.h'
      FileOffset:      17
      Replacements:    []
    Level:           Warning
...
"""


def _fix(path: str, offset: int, length: int, text: str, check: str = "c") -> Fix:
    return Fix(check, "m", path, offset, (Replacement(path, offset, length, text),))


def test_parse_export() -> None:
    fixes = parse_fixes(EXPORT + EXPORT)
    assert len(fixes) == 4
    first = fixes[0]
    assert first.check == "modernize-use-using"
    assert first.message == "use 'using' instead of 'typedef'"
    assert first.offset == 0
    assert first.replacements == (Replacement("/workspace/include/a.h", 0, 15, "using X = int"),)
    assert fixes[1].replacements == ()


def test_apply_dedupes_and_preserves_other_bytes() -> None:
    files = {"a.h": b"typedef int X;\ntypedef long Y;\n"}
    fixes = [
        _fix("/w/a.h", 0, 13, "using X = int"),
        _fix("/w/a.h", 0, 13, "using X = int"),  # the same fix from another translation unit
        _fix("/w/a.h", 15, 14, "using Y = long"),
    ]
    outcome = apply_fixes(fixes, key_of=lambda p: p.removeprefix("/w/"), read=files.__getitem__)
    assert outcome.new_contents["a.h"] == b"using X = int;\nusing Y = long;\n"
    assert outcome.applied == {"a.h": 2}
    assert outcome.skipped == []


def test_conflicts_and_files_outside_the_unit_are_skipped_whole() -> None:
    files = {"a.h": b"abcdef", "b.h": b"xyz"}
    outside = Fix(
        "c",
        "m",
        "/w/a.h",
        0,
        (Replacement("/w/a.h", 0, 1, "A"), Replacement("/elsewhere/c.h", 0, 1, "C")),
    )
    fixes = [
        _fix("/w/a.h", 1, 3, "BCD", check="first"),
        _fix("/w/a.h", 2, 3, "zzz", check="second"),  # overlaps the first
        outside,
        _fix("/w/b.h", 3, 0, "!"),  # an insertion at the end
    ]

    def key_of(path: str) -> str | None:
        return path.removeprefix("/w/") if path.startswith("/w/") else None

    outcome = apply_fixes(fixes, key_of=key_of, read=files.__getitem__)
    assert outcome.new_contents == {"a.h": b"aBCDef", "b.h": b"xyz!"}
    assert outcome.skipped == [
        ("a.h", "second", "conflicts with another fix"),
        ("/elsewhere/c.h", "c", "the fix changes a file outside the unit"),
    ]


def test_two_insertions_at_one_point_conflict() -> None:
    files = {"a": b"xy"}
    fixes = [_fix("a", 1, 0, "1"), _fix("a", 1, 0, "2")]
    outcome = apply_fixes(fixes, key_of=lambda p: p, read=files.__getitem__)
    assert outcome.new_contents == {"a": b"x1y"}
    assert len(outcome.skipped) == 1


def test_crlf_files_get_crlf_replacements_and_utf8_text() -> None:
    files = {"a.h": b"typedef int X;\r\nint y;\r\n"}
    fixes = [_fix("a.h", 0, 13, "// café\nusing X = int")]
    outcome = apply_fixes(fixes, key_of=lambda p: p, read=files.__getitem__)
    assert outcome.new_contents["a.h"] == "// café\r\nusing X = int;\r\nint y;\r\n".encode()


def test_fixed_sites_use_original_offsets() -> None:
    files = {"a.h": b"one\ntwo\nthree\n"}
    fixes = [_fix("a.h", 8, 5, "THREE", check="k"), Fix("k", "m", "a.h", 4, ())]
    sites = fixed_sites(fixes, key_of=lambda p: p, read=files.__getitem__)
    assert sites == {("a.h", 3, "k")}
