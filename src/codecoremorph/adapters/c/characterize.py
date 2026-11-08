"""Characterization tests for a C project built with a Makefile (SPEC §7.11, D-74).

A characterization test is a C program in the characterization directory that prints TAP,
over a small helper CodeCoreMorph writes beside it, ``codecoremorph_tap.h``. Each test case is
a ``static void name(void)`` function of ``CCM_CHECK*`` checks, which ``main`` runs with
``CCM_RUN(name)``; a case prints one TAP point named after its function, ``not ok`` with each
failed check (the values it compared) after it when one fails. A program is named after its
file, so a case's test ID is known before it runs: ``tap:<dir>/<stem>_test/<function>``. A
``main`` that starts with ``CCM_SELECT(argc, argv)`` runs only the case its argument names: a
Rust → C port's translated tests make each case a CTest test that way (D-75).

The top-level Makefile gets a marked block that builds each program from its file, linked with
the archives of the libraries that compile the code it tests (and Rust's native libraries,
once a port folds a crate into them), and runs it before the test target's own recipe, its
exit status ignored so the project's tests always run: the TAP points judge it. The tests are C
tests like the project's own, so a port to Rust translates them with the rest (D-60).
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Sequence
from dataclasses import dataclass

from codecoremorph.adapters.c.tests import Check, checks
from codecoremorph.rules.detectors import mask_c_family

HELPER = "codecoremorph_tap.h"
BEGIN = "# >>> codecoremorph: characterization tests (written by CodeCoreMorph; do not edit) >>>"
END = "# <<< codecoremorph: characterization tests <<<"
TARGET = "codecoremorph-characterization"

HELPER_TEXT = """\
/*
 * codecoremorph_tap.h - the characterization tests' checks (written by CodeCoreMorph).
 *
 * A test case is a static void function of CCM_CHECK* checks; main runs each with CCM_RUN
 * and returns ccm_done(). A case prints one TAP point, named after its function: "ok" when
 * every check held, else "not ok" and a "#" line per failed check, with the values compared.
 * A main that starts with CCM_SELECT(argc, argv) runs only the case its argument names, and
 * fails when no case has that name: each case can be a test of its own (a CTest test).
 */
#ifndef CODECOREMORPH_TAP_H
#define CODECOREMORPH_TAP_H

#include <stdio.h>
#include <string.h>

static int ccm_cases = 0;
static int ccm_failed_cases = 0;
static int ccm_failed_checks = 0;
static char ccm_notes[4096];
static const char *ccm_only = NULL; /* CCM_SELECT: only the case of this name */

static void ccm_note(const char *file, int line, const char *what)
{
    size_t used = strlen(ccm_notes);
    char text[600];
    ccm_failed_checks++;
    sprintf(text, "#   %.200s line %d: %.360s\\n", file, line, what);
    if (used + strlen(text) < sizeof ccm_notes) {
        strcpy(ccm_notes + used, text);
    }
}

static void ccm_check_long(long got, long expected, const char *what, const char *file,
                           int line)
{
    char text[400];
    if (got != expected) {
        sprintf(text, "%.300s: got %ld, expected %ld", what, got, expected);
        ccm_note(file, line, text);
    }
}

static void ccm_check_ulong(unsigned long got, unsigned long expected, const char *what,
                            const char *file, int line)
{
    char text[400];
    if (got != expected) {
        sprintf(text, "%.300s: got %lu, expected %lu", what, got, expected);
        ccm_note(file, line, text);
    }
}

static void ccm_check_str(const char *got, const char *expected, const char *what,
                          const char *file, int line)
{
    char text[600];
    if (got == NULL || expected == NULL ? got != expected : strcmp(got, expected) != 0) {
        sprintf(text, "%.200s: got \\"%.150s\\", expected \\"%.150s\\"", what,
                got ? got : "(null)", expected ? expected : "(null)");
        ccm_note(file, line, text);
    }
}

static void ccm_check_double(double got, double expected, double within, const char *what,
                             const char *file, int line)
{
    char text[400];
    double off = got > expected ? got - expected : expected - got;
    if (!(off <= within)) {
        sprintf(text, "%.300s: got %.17g, expected %.17g", what, got, expected);
        ccm_note(file, line, text);
    }
}

#define CCM_CHECK(cond) \\
    do { if (!(cond)) ccm_note(__FILE__, __LINE__, #cond); } while (0)
#define CCM_CHECK_INT(got, expected) \\
    ccm_check_long((long)(got), (long)(expected), #got, __FILE__, __LINE__)
#define CCM_CHECK_UINT(got, expected) \\
    ccm_check_ulong((unsigned long)(got), (unsigned long)(expected), #got, __FILE__, __LINE__)
#define CCM_CHECK_STR(got, expected) \\
    ccm_check_str((got), (expected), #got, __FILE__, __LINE__)
#define CCM_CHECK_NEAR(got, expected, within) \\
    ccm_check_double((got), (expected), (within), #got, __FILE__, __LINE__)
#define CCM_RUN(test) ccm_run((test), #test)
#define CCM_SELECT(argc, argv) (ccm_only = (argc) > 1 ? (argv)[1] : NULL)

static void ccm_run(void (*test)(void), const char *name)
{
    int before = ccm_failed_checks;
    if (ccm_only != NULL && strcmp(ccm_only, name) != 0) {
        return;
    }
    ccm_notes[0] = '\\0';
    test();
    ccm_cases++;
    if (ccm_failed_checks == before) {
        printf("ok %d - %s\\n", ccm_cases, name);
    } else {
        ccm_failed_cases++;
        printf("not ok %d - %s\\n%s", ccm_cases, name, ccm_notes);
    }
    fflush(stdout);
}

static int ccm_done(void)
{
    /* Each check is a macro a test may not use: this keeps the compiler quiet about them. */
    (void)&ccm_check_long;
    (void)&ccm_check_ulong;
    (void)&ccm_check_str;
    (void)&ccm_check_double;
    printf("1..%d\\n", ccm_cases);
    if (ccm_only != NULL && ccm_cases == 0) {
        printf("# no case is named %s\\n", ccm_only);
        return 1;
    }
    return ccm_failed_cases != 0;
}

#endif
"""

_RUN = re.compile(r"\bCCM_RUN\s*\(\s*([A-Za-z_]\w*)\s*\)")
_FUNCTION = r"\bstatic\s+void\s+{name}\s*\(\s*(?:void)?\s*\)\s*\{{"


def test_file(test_dir: str, stem: str) -> str:
    return f"{test_dir.rstrip('/')}/{stem}_test.c"


def program(path: str) -> str:
    """The program a characterization test file builds: the file without ``.c``."""
    return path.removesuffix(".c")


@dataclass(frozen=True)
class Case:
    """A characterization test case: its function, where it starts, and its checks."""

    name: str
    line: int
    checks: list[Check]
    start: int  # offsets of the function's text, its comment included
    end: int


def cases(text: str) -> list[Case]:
    """The test cases ``main`` runs (``CCM_RUN(name)``), in that order, with each function's
    checks. A name run with no function of that shape is a case without checks."""
    code = mask_c_family(text)
    found: list[Case] = []
    seen: set[str] = set()
    for match in _RUN.finditer(code):
        name = match.group(1)
        if name in seen:
            continue
        seen.add(name)
        function = re.search(_FUNCTION.format(name=re.escape(name)), code)
        if function is None:
            line = text.count("\n", 0, match.start()) + 1
            found.append(Case(name=name, line=line, checks=[], start=0, end=0))
            continue
        opening = function.end() - 1
        closing = _closing_brace(code, opening)
        body = text[: closing + 1] if closing is not None else text
        offset = function.start()
        start = _comment_start(text, code, offset)
        inside = [c for c in checks(body) if c.line > text.count("\n", 0, offset)]
        found.append(
            Case(
                name=name,
                line=text.count("\n", 0, offset) + 1,
                checks=inside,
                start=start,
                end=(closing + 1) if closing is not None else len(text),
            )
        )
    return found


def _closing_brace(code: str, opening: int) -> int | None:
    depth = 0
    for i in range(opening, len(code)):
        if code[i] == "{":
            depth += 1
        elif code[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    return None


def _comment_start(text: str, code: str, offset: int) -> int:
    """Where the function's text starts: its line, or the comment right above it."""
    start = text.rfind("\n", 0, offset) + 1
    while start > 0:
        previous = text.rfind("\n", 0, start - 1) + 1
        line = text[previous : start - 1]
        if line.strip() and not code[previous : start - 1].strip():
            start = previous  # a comment line: blank in the masked code
        else:
            break
    return start


def drop_cases(text: str, names: Sequence[str]) -> str:
    """``text`` without the cases ``names``: each function and its ``CCM_RUN`` line."""
    gone = set(names)
    spans = sorted(
        ((c.start, c.end) for c in cases(text) if c.name in gone and c.end > c.start),
        reverse=True,
    )
    for start, end in spans:
        stop = end
        while stop < len(text) and text[stop] in " \t":
            stop += 1
        if text.startswith("\r\n", stop):
            stop += 2
        elif text.startswith("\n", stop):
            stop += 1
        if text.startswith("\n", stop) or text.startswith("\r\n", stop):
            stop = text.index("\n", stop) + 1  # and one blank line after it
        text = text[:start] + text[stop:]
    lines = text.splitlines(keepends=True)
    kept = [
        line
        for line in lines
        if not ((m := _RUN.search(mask_c_family(line))) and m.group(1) in gone)
    ]
    return "".join(kept)


def test_targets(makefile: str, configured: Sequence[str]) -> list[str]:
    """The targets that run the tests: the manifest's, else ``check`` or ``test``, whichever
    the Makefile has outside the block (as the Make build finds them)."""
    if configured:
        return list(configured)
    text = with_block(makefile, None)
    for target in ("check", "test"):
        if re.search(rf"^{target}\s*:", text, re.MULTILINE):
            return [target]
    return []


def make_block(
    tests: Sequence[str],
    archives: Sequence[str],
    include_dirs: Sequence[str],
    test_targets: Sequence[str],
) -> str:
    """The marked block for ``tests`` (paths from the Makefile's directory), each built from
    its file and linked with ``archives``, run before each of ``test_targets``."""
    names = [program(t) for t in tests]
    helper_dirs = sorted({posixpath.dirname(t) or "." for t in tests})
    includes = " ".join(f"-I{d}" for d in [*helper_dirs, *include_dirs])
    lines = [BEGIN, f"CODECOREMORPH_CHARACTERIZATION = {' '.join(names)}"]
    for test, name in zip(tests, names, strict=True):
        helper = posixpath.join(posixpath.dirname(test), HELPER)
        lines += [
            f"{name}: {test} {helper} {' '.join(archives)}".rstrip(),
            f"\t$(CC) $(CPPFLAGS) $(CFLAGS) {includes} -o $@ {test} {' '.join(archives)} "
            "$(LDFLAGS) $(CODECOREMORPH_RUST_LIBS) $(LDLIBS) $(LIBS)",
        ]
    lines += [f"{target}: {TARGET}" for target in test_targets]
    lines += [f".PHONY: {TARGET}", f"{TARGET}: $(CODECOREMORPH_CHARACTERIZATION)"]
    # Run as ./path, which the test results know the program by; failures don't stop make.
    lines += [f"\t-./{name}" for name in names]
    lines.append(END)
    return "\n".join(lines) + "\n"


def with_block(text: str, block: str | None) -> str:
    """``text`` with ``block`` in place of its marked block, at the end when it has none;
    without the block when ``block`` is None."""
    start, end = text.find(BEGIN), text.find(END)
    if start != -1 and end > start:
        stop = text.find("\n", end)
        stop = len(text) if stop == -1 else stop + 1
        head, tail = text[:start], text[stop:]
        if block is None:
            return head.rstrip("\n") + "\n" + tail if head.strip() else tail
        return head + block + tail
    if block is None:
        return text
    body = text if text.endswith("\n") or not text else text + "\n"
    return body + ("\n" if body else "") + block
