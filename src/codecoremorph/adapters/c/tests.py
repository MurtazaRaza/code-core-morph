"""What a C test checks and what disables one (I-3, I-4), for tests that print TAP or run under
automake's harness (D-73).

A C test's cases exist only when it runs, so their IDs come from the last test run (the
index's test entries): ``tap:<program>/<description>`` for a TAP point, ``make:<program>`` for a
program automake's harness ran whole. The checks are the calls of the assertion functions and
macros of the usual C test libraries: libtap and its kin (``ok``, ``is``, ``cmp_ok``, ...),
``assert``, Check (``ck_assert*``), CUnit (``CU_ASSERT*``), Unity (``TEST_ASSERT*``), cmocka
(``assert_*``), and the ``CHECK``, ``ASSERT``, ``EXPECT``, ``REQUIRE`` and ``VERIFY`` macro
families projects define for themselves. A TAP point's checks are the call whose description
is the point's (one that formats its description at run time can't be told apart, so it has
none); a whole program's are every call in its files.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from codecoremorph.core.models import SymbolKind, TestAssertions
from codecoremorph.patching.textfile import BinaryFileError, TextFile
from codecoremorph.rules.detectors import mask_c_family
from codecoremorph.store.index import IndexStore

_CALL = re.compile(
    r"\b(ok|is|isnt|like|unlike|cmp_ok|cmp_mem|pass|fail|assert|"
    r"ck_assert\w*|fail_unless|fail_if|CU_ASSERT\w*|TEST_ASSERT\w*|assert_\w+|CCM_CHECK\w*|"
    r"(?:CHECK|ASSERT|EXPECT|REQUIRE|VERIFY)(?:_\w+)?)\s*\("
)
_DEFINE = re.compile(r"^\s*#\s*define\b")
_DECLARES = re.compile(
    r"\b(?:void|int|static|char|bool|unsigned|long|double|size_t|const)\s*\*?\s*$"
)
_STRING = re.compile(r'"((?:[^"\\\n]|\\.)*)"')
_ESCAPES = {"n": "\n", "t": "\t", '"': '"', "\\": "\\", "'": "'", "0": "\0"}
# Markers that disable a test or skip it (I-3).
_IF_ZERO = re.compile(r"^\s*#\s*if\s+0\b")
# A free function's call: `.skip(1)` on an iterator, or `p->skip(...)`, is none of libtap's.
_SKIP_CALL = re.compile(r"(?<![\w.>])(skip|skip_start|todo|todo_start|plan_skip_all)\s*\(")
_SKIP_TEXT = re.compile(r"#\s*(SKIP|TODO)\b", re.IGNORECASE)
_SKIP_STATUS = re.compile(r"\b(?:return|exit)\s*\(?\s*77\s*\)?\s*;")


@dataclass(frozen=True)
class Check:
    """One assertion call: where it is, its text with spacing normalized, and the TAP
    description it prints, when it is a literal."""

    line: int
    text: str
    description: str | None


def checks(text: str) -> list[Check]:
    """The assertion calls in a C test file, in order, outside comments, string literals and
    ``#define``s (a test helper's ``#define ok(cond, what) ...`` defines one, it doesn't
    check), and not the declarations of such functions."""
    code = mask_c_family(text)  # comments and literals blanked: names and directives only
    literal = mask_c_family(text, strings=False)  # comments blanked: the arguments
    found: list[Check] = []
    for match in _CALL.finditer(code):
        start = code.rfind("\n", 0, match.start()) + 1
        before = code[start : match.start()]
        if _DEFINE.match(before):
            continue
        if _DECLARES.search(before):
            continue  # a definition or declaration of the function, not a call
        opening = match.end() - 1
        closing = _matching(literal, opening)
        if closing is None:
            continue
        call = " ".join(literal[match.start() : closing + 1].split())
        args = _arguments(literal[opening + 1 : closing])
        found.append(
            Check(
                line=text.count("\n", 0, match.start()) + 1,
                text=call,
                description=_description(match.group(1), args),
            )
        )
    return found


def _matching(text: str, opening: int) -> int | None:
    depth = 0
    i = opening
    quote: str | None = None
    while i < len(text):
        c = text[i]
        if quote is not None:
            if c == "\\":
                i += 1
            elif c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _arguments(inner: str) -> list[str]:
    """A call's arguments, split at its top-level commas."""
    args: list[str] = []
    depth = 0
    quote: str | None = None
    current = ""
    i = 0
    while i < len(inner):
        c = inner[i]
        if quote is not None:
            current += c
            if c == "\\" and i + 1 < len(inner):
                current += inner[i + 1]
                i += 1
            elif c == quote:
                quote = None
        elif c in "\"'":
            quote = c
            current += c
        elif c in "([{":
            depth += 1
            current += c
        elif c in ")]}":
            depth -= 1
            current += c
        elif c == "," and depth == 0:
            args.append(current.strip())
            current = ""
        else:
            current += c
        i += 1
    if current.strip():
        args.append(current.strip())
    return args


def _description(name: str, args: Sequence[str]) -> str | None:
    """The TAP description a libtap-style call prints: the literal in its description's place
    (``ok(test, description)``, ``cmp_ok(got, op, expected, description)``), or None for
    another kind of check or a description formatted at run time (a ``%`` in it)."""
    at = _DESCRIBED_AT.get(name)
    if at is None or at >= len(args):
        return None
    parts = _STRING.findall(args[at])
    if not parts or _STRING.sub("", args[at]).strip():
        return None
    text = "".join(_unescape(p) for p in parts)  # adjacent literals join
    return None if "%" in text else text


_DESCRIBED_AT = {
    "ok": 1,
    "is": 2,
    "isnt": 2,
    "like": 2,
    "unlike": 2,
    "cmp_ok": 3,
    "cmp_mem": 3,
    "pass": 0,
    "fail": 0,
}


def _unescape(literal: str) -> str:
    return re.sub(r"\\(.)", lambda m: _ESCAPES.get(m.group(1), m.group(1)), literal)


def tap_checks(point: str, files: Mapping[str, str]) -> tuple[str, int, list[str]] | None:
    """``(file, line, [check])`` of the call that prints the TAP point with description
    ``point`` in ``files`` (path → text), or None."""
    for path, text in sorted(files.items()):
        for check in checks(text):
            if check.description == point:
                return path, check.line, [check.text]
    return None


def program_checks(files: Mapping[str, str]) -> list[str]:
    """Every check in a test program's files, in file order."""
    return [c.text for _, text in sorted(files.items()) for c in checks(text)]


def test_assertions(index_path: Path, worktree: Path, files: Sequence[str]) -> list[TestAssertions]:
    """The C tests of the programs whose files are among ``files``, with their checks, by the
    IDs of the last test run (the index's test entries): each TAP point the program printed
    whose call names it, and each program automake's harness ran whole. A coarse entry (a
    make target's exit status) runs no one program and has none."""
    if not index_path.is_file():
        return []
    wanted = set(files)
    with IndexStore.open(index_path) as store:
        targets = {t.name: t for t in store.build_targets()}
        entries = store.test_entries()
        mains = {
            s.file: s.range.start_line
            for s in store.symbols(kind=SymbolKind.FUNCTION, name="main")
            if s.is_definition
        }
    found: list[TestAssertions] = []
    texts: dict[str, dict[str, str]] = {}
    for entry in entries:
        target = targets.get(entry.runs or "")
        if target is None or not target.files:
            continue
        home = next((f for f in target.files if f in mains), target.files[0])
        if home not in wanted:
            continue
        if target.name not in texts:
            texts[target.name] = {
                f: t for f in target.files if (t := _read(worktree / f)) is not None
            }
        sources = texts[target.name]
        if entry.test_id.startswith("tap:"):
            for point in _points(entry.test_id, target.artifacts):
                hit = tap_checks(point, sources)
                if hit is not None:
                    path, line, calls = hit
                    found.append(
                        TestAssertions(
                            file=path,
                            test=point,
                            test_id=entry.test_id,
                            line=line,
                            assertions=calls,
                        )
                    )
                    break
        elif entry.test_id.startswith("make:"):
            found.append(
                TestAssertions(
                    file=home,
                    test=target.name,
                    test_id=entry.test_id,
                    line=mains.get(home, 1),
                    assertions=program_checks(sources),
                )
            )
    return found


def _points(test_id: str, artifacts: Sequence[str]) -> list[str]:
    """The description a ``tap:<program>/<description>`` ID may hold: after the program's
    path when the build names it, else after each ``/`` in turn, the longest first (both the
    program and the description may hold one)."""
    rest = test_id.removeprefix("tap:")
    for artifact in artifacts:
        if rest.startswith(f"{artifact}/"):
            return [rest[len(artifact) + 1 :]]
    return [rest[i + 1 :] for i, c in enumerate(rest) if c == "/"]


def _read(path: Path) -> str | None:
    try:
        return TextFile.decode(path.read_bytes()).text
    except (OSError, BinaryFileError):
        return None


def disabled_markers(path: str, text: str) -> list[str]:
    """``path:line: marker`` for what disables or skips a C test: an ``#if 0`` block, a TAP
    skip or to-do (``skip(...)``, a ``# SKIP`` or ``# TODO`` directive in a literal), and
    automake's skipped status, 77. Comments don't count."""
    code = mask_c_family(text).splitlines()
    literal = mask_c_family(text, strings=False).splitlines()
    found: list[str] = []
    for number, (line, kept) in enumerate(zip(code, literal, strict=False), start=1):
        if _IF_ZERO.match(line):
            found.append(f"{path}:{number}: #if 0")
        elif _DEFINE.match(line):
            continue
        elif m := _SKIP_CALL.search(line):
            found.append(f"{path}:{number}: {m.group(1)}(...)")
        elif directive := next(
            (d for s in _STRING.findall(kept) if (d := _SKIP_TEXT.search(s))), None
        ):
            found.append(f"{path}:{number}: a TAP directive ({directive.group(0)})")
        elif _SKIP_STATUS.search(line):
            found.append(f"{path}:{number}: the skipped status 77")
    return found
