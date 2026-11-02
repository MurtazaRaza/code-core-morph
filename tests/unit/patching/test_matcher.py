from __future__ import annotations

from codecoremorph.core.models import RejectionReason
from codecoremorph.patching.matcher import Match, MatchFailure, find_match

SOURCE = """\
namespace geo {
    struct Vector {
        float x;
        float y;
    };

    float dot(const Vector& a, const Vector& b) {
        return a.x * b.x + a.y * b.y;
    }
}
"""


def test_exact_unique_match() -> None:
    found = find_match(SOURCE, "float dot(", "double dot(")
    assert isinstance(found, Match)
    assert not found.tolerant
    assert SOURCE[found.start : found.end] == "float dot("


def test_exact_ambiguous_match_lists_lines() -> None:
    found = find_match(SOURCE, "float ", "double ")
    assert isinstance(found, MatchFailure)
    assert found.reason is RejectionReason.AMBIGUOUS
    assert found.lines == [3, 4, 7]
    assert "3 places" in found.message


def test_overlapping_occurrences_count_as_ambiguous() -> None:
    found = find_match("aaa", "aa", "b")
    assert isinstance(found, MatchFailure)
    assert found.reason is RejectionReason.AMBIGUOUS


def test_whitespace_tolerant_match_reindents_the_replacement() -> None:
    search = "float x;\nfloat y;\n"  # the model dropped the indentation
    replace = "float x = 0;\nfloat y = 0;\n"
    found = find_match(SOURCE, search, replace)
    assert isinstance(found, Match)
    assert found.tolerant
    assert SOURCE[found.start : found.end] == "        float x;\n        float y;"
    assert found.replacement == "        float x = 0;\n        float y = 0;"


def test_whitespace_tolerant_ignores_trailing_spaces() -> None:
    found = find_match("int a;   \nint b;\n", "int a;\nint b;", "int c;")
    assert isinstance(found, Match)
    assert found.tolerant
    assert (found.start, found.end) == (0, len("int a;   \nint b;"))


def test_whitespace_tolerant_must_still_be_unique() -> None:
    found = find_match("  x;\n\n    x;\n", "x;\n", "y;\n")
    assert isinstance(found, MatchFailure)
    # "x;\n" matches exactly in two places already
    assert found.reason is RejectionReason.AMBIGUOUS
    found = find_match("  int x;\n    int x;\n", "int  x;", "int y;")
    assert isinstance(found, MatchFailure)


def test_not_found_reports_the_closest_region() -> None:
    search = "float dot(const Vector& a, const Vector& c) {\n    return a.x * c.x;\n"
    found = find_match(SOURCE, search, "x")
    assert isinstance(found, MatchFailure)
    assert found.reason is RejectionReason.NOT_FOUND
    assert found.candidate is not None
    assert (found.candidate.start_line, found.candidate.end_line) == (7, 8)
    assert "float dot(const Vector& a, const Vector& b)" in found.candidate.text
    assert found.candidate.similarity >= 0.5


def test_not_found_without_a_similar_region() -> None:
    found = find_match(SOURCE, "completely unrelated text\nthat appears nowhere", "x")
    assert isinstance(found, MatchFailure)
    assert found.candidate is None


def test_whitespace_only_search_is_never_loosely_matched() -> None:
    found = find_match("a\n\nb\n", "   \n", "x")
    assert isinstance(found, MatchFailure)
    assert found.reason is RejectionReason.NOT_FOUND
