"""What a C test checks and what disables one (I-3, I-4, D-73)."""

from __future__ import annotations

from pathlib import Path

from codecoremorph.adapters.c.tests import checks, disabled_markers, program_checks, tap_checks

FIXTURES = Path(__file__).resolve().parents[4] / "fixtures" / "c"

LIBTAP = """\
#include "tap.h"
/* ok(0, "a comment is no check") */
static void helper(void) { }

int main(void)
{
    plan(4);
    ok(htab_count(t) == 0, "a new table is empty");
    is(sbuf_len(&b), 3, "the buffer holds" " three bytes");
    cmp_ok(n, "<", 10,
           "the count stays small");
    ok(i == 2, "item %d", i);
    pass("reached the end");
    printf("ok(not a check)\\n");
    return exit_status();
}
"""


def test_libtap_checks_and_their_descriptions() -> None:
    found = checks(LIBTAP)
    assert [(c.line, c.description) for c in found] == [
        (8, "a new table is empty"),
        (9, "the buffer holds three bytes"),  # adjacent literals join
        (10, "the count stays small"),
        (12, None),  # formatted at run time
        (13, "reached the end"),
    ]
    assert found[2].text == 'cmp_ok(n, "<", 10, "the count stays small")'  # spacing normalized


def test_a_tap_point_finds_its_call() -> None:
    files = {"tests/test_htab.c": LIBTAP}
    assert tap_checks("the count stays small", files) == (
        "tests/test_htab.c",
        10,
        ['cmp_ok(n, "<", 10, "the count stays small")'],
    )
    assert tap_checks("item 2", files) is None


def test_the_fixtures_checks() -> None:
    """c-legacy's own TAP macro, ``ok(cond, what)``, and c-autotools's ``CHECK`` macros: the
    helpers that define them check nothing themselves."""
    legacy = FIXTURES / "c-legacy" / "tests"
    assert checks((legacy / "tap.h").read_text(encoding="utf-8")) == []
    htab = checks((legacy / "test_htab.c").read_text(encoding="utf-8"))
    assert htab
    assert all(c.text.startswith("ok(") and c.description for c in htab)
    autotools = FIXTURES / "c-autotools" / "tests"
    assert checks((autotools / "check.h").read_text(encoding="utf-8")) == []
    value = {"tests/test_value.c": (autotools / "test_value.c").read_text(encoding="utf-8")}
    found = program_checks(value)
    assert found
    assert all(c.startswith(("CHECK(", "CHECK_STR(")) for c in found)


def test_declarations_and_definitions_are_no_checks() -> None:
    text = "void fail(const char *why);\nstatic int CHECK_init(void) { return 0; }\n"
    assert checks(text) == []


def test_what_disables_a_c_test() -> None:
    text = """\
#if 0
    ok(broken(), "not yet");
#endif
    skip(1, "needs a network");
    ok(1, "later # TODO make it work");
    /* skip(1, "a comment") */
    if (getenv("NO_TESTS")) return 77;
    printf("# SKIP is printed, not a directive here\\n");
    reader.skip(2); p->skip(1);
"""
    assert disabled_markers("tests/t.c", text) == [
        "tests/t.c:1: #if 0",
        "tests/t.c:4: skip(...)",
        "tests/t.c:5: a TAP directive (# TODO)",
        "tests/t.c:7: the skipped status 77",
        "tests/t.c:8: a TAP directive (# SKIP)",
    ]
