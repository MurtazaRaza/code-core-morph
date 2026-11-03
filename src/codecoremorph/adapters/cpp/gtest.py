"""GoogleTest source: test cases, their assertions, and removing cases (SPEC §7.11, I-4).

Test cases are ``TEST``, ``TEST_F``, ``TEST_P``, ``TYPED_TEST`` and ``TYPED_TEST_P`` blocks,
named ``Suite.Case`` as GoogleTest names them in its reports (a value- or type-parameterized
case's report name adds an instantiation prefix and an index; the name here is its pattern).

Assertions are the ``EXPECT_*``/``ASSERT_*`` macro calls and ``FAIL()``, ``ADD_FAILURE()`` and
``SUCCEED()`` in a case's body, with their arguments, comments removed and whitespace
normalized, so reformatting a test doesn't count as changing what it checks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from codecoremorph.rules.detectors import mask_c_family

_TEST_MACRO = re.compile(
    r"\b(TEST|TEST_F|TEST_P|TYPED_TEST|TYPED_TEST_P)\s*\(\s*(\w+)\s*,\s*(\w+)\s*\)"
)
_ASSERTION = re.compile(r"\b((?:EXPECT|ASSERT)_[A-Z0-9_]+|FAIL|ADD_FAILURE|SUCCEED)\s*\(")
_SPACE_AROUND = re.compile(r"\s*([(),\[\]{}])\s*")
_SPACES = re.compile(r"\s+")


@dataclass(frozen=True)
class CaseBlock:
    __test__ = False  # not a pytest test class

    name: str  # "Suite.Case"
    macro: str
    start: int  # offset of the macro
    end: int  # offset just past the closing brace
    line: int  # 1-based line of the macro
    body_start: int  # offset of the opening brace
    body_end: int  # offset of the closing brace


def _matching(masked: str, opening: int) -> int:
    """The offset of the bracket closing the one at ``opening`` (in masked text), or -1."""
    pairs = {"(": ")", "{": "}", "[": "]"}
    close = pairs[masked[opening]]
    depth = 0
    for i in range(opening, len(masked)):
        c = masked[i]
        if c == masked[opening]:
            depth += 1
        elif c == close:
            depth -= 1
            if depth == 0:
                return i
    return -1


def find_cases(text: str) -> list[CaseBlock]:
    """Every test case in ``text``, in file order. A case whose braces don't close is left
    out: the file doesn't compile, and the build says so."""
    masked = mask_c_family(text)
    blocks: list[CaseBlock] = []
    position = 0
    while (match := _TEST_MACRO.search(masked, position)) is not None:
        position = match.end()
        brace = position
        while brace < len(masked) and masked[brace] in " \t\r\n":
            brace += 1
        if brace >= len(masked) or masked[brace] != "{":
            continue  # a declaration or a macro use that isn't a test definition
        closing = _matching(masked, brace)
        if closing < 0:
            continue
        blocks.append(
            CaseBlock(
                name=f"{match.group(2)}.{match.group(3)}",
                macro=match.group(1),
                start=match.start(),
                end=closing + 1,
                line=text.count("\n", 0, match.start()) + 1,
                body_start=brace,
                body_end=closing,
            )
        )
        position = closing + 1
    return blocks


def _normalize(call: str) -> str:
    return _SPACE_AROUND.sub(r"\1", _SPACES.sub(" ", call)).strip()


def assertions(text: str, block: CaseBlock) -> list[str]:
    """The assertion macro calls in ``block``'s body, normalized, in order."""
    masked = mask_c_family(text)  # finds the calls; their text comes from...
    no_comments = mask_c_family(text, strings=False)  # ...here, which keeps the literals
    found: list[str] = []
    position = block.body_start
    while (match := _ASSERTION.search(masked, position, block.body_end)) is not None:
        opening = match.end() - 1
        closing = _matching(masked, opening)
        if closing < 0 or closing > block.body_end:
            break
        found.append(_normalize(no_comments[match.start() : closing + 1]))
        position = closing + 1
    return found


def drop_tests(text: str, names: set[str]) -> str:
    """``text`` without the test cases named in ``names`` (and the line breaks after them)."""
    out: list[str] = []
    position = 0
    for block in find_cases(text):
        if block.name not in names:
            continue
        out.append(text[position : block.start])
        end = block.end
        while end < len(text) and text[end] in " \t":
            end += 1
        if text.startswith("\r\n", end):
            end += 2
        elif text.startswith("\n", end):
            end += 1
        position = end
    out.append(text[position:])
    return "".join(out)
