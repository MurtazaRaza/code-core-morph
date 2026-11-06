"""C# test sources: their test methods, assertions and disabled tests (SPEC §11.3; I-3, I-4).

A test method is one with a test attribute of NUnit (``[Test]``, ``[TestCase]``,
``[TestCaseSource]``, ``[Theory]``), xUnit (``[Fact]``, ``[Theory]``) or MSTest
(``[TestMethod]``, ``[DataTestMethod]``). Its test ID is the one the TRX report gives,
``dotnet:<namespace>.<Class>#<method>``, with ``+`` between nested classes; a parameterized
method's cases add their arguments in the report, which the sources can't know.

Assertions are the calls of ``Assert.…``, ``ClassicAssert.…``, ``CollectionAssert.…``,
``StringAssert.…`` and the like, and FluentAssertions' and Shouldly's ``Should…`` chains, in a
method's body, and an ``[ExpectedException]`` on it, comments removed and whitespace normalized.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_TEST_ATTRIBUTES = frozenset(
    {"Test", "TestCase", "TestCaseSource", "Theory", "Fact", "TestMethod", "DataTestMethod"}
)
_TYPE = re.compile(
    r"(?<![\w.@])(?:record\s+(?:class|struct)|class|struct|interface|record)\s+([A-Za-z_]\w*)"
)
_NAMESPACE = re.compile(r"(?<![\w.@])namespace\s+([\w.]+)\s*([;{])")
_ATTRIBUTE_NAME = re.compile(r"(?:[\w]+\.)*(\w+?)(?:Attribute)?\s*(?:\(|,|\]|$)")
_IDENT_BEFORE = re.compile(r"([A-Za-z_]\w*)\s*(?:<[^<>]*>)?\s*$")
_ASSERTION = re.compile(
    r"(?<![\w.])(?:[\w]+\.)*(?:Assert|ClassicAssert|CollectionAssert|StringAssert|FileAssert|"
    r"DirectoryAssert|Verify)\.\w+\s*(?:<[^<>()]*>)?\s*\("
    r"|\.\s*Should(?:[A-Z]\w*)?\s*\("
)
_DISABLED = re.compile(
    # [Ignore], [Explicit], and either one in a list: [Test, Ignore("reason")]
    r"(?<=[\[,])\s*(?:[\w.]*\.)?(?:Ignore|Explicit)(?:Attribute)?(?=\s*[(\],])"
    r"|\bSkip\s*=\s*(?=\")"
    r"|(?<![\w.])(?:[\w]+\.)*(?:Assert\.(?:Ignore|Inconclusive|Skip\w*)|Assume\.\w+|Skip\.\w+)\s*\("
)
_SPACE_AROUND = re.compile(r"\s*([(),\[\]{}.])\s*")
_SPACES = re.compile(r"\s+")


def _blank(text: str) -> str:
    return re.sub(r"[^\n]", " ", text)


def _string_end(text: str, start: int, quote_at: int) -> tuple[int, int, int]:
    """``(end, head, tail)`` of the string literal whose prefix starts at ``start`` and whose
    first quote is at ``quote_at``: its end offset, and the lengths of its opening (prefix and
    quotes) and closing quotes."""
    n = len(text)
    verbatim = "@" in text[start:quote_at]
    quotes = 0
    while quote_at + quotes < n and text[quote_at + quotes] == '"':
        quotes += 1
    if quotes >= 3 and not verbatim:  # a raw string literal, closed by as many quotes
        fence = '"' * quotes
        close = text.find(fence, quote_at + quotes)
        if close < 0:
            return n, quote_at + quotes - start, 0
        return close + quotes, quote_at + quotes - start, quotes
    head = quote_at + 1 - start
    if quotes == 2 and not verbatim:  # ""
        return quote_at + 2, head, 1
    j = quote_at + 1
    while j < n:
        if verbatim:
            if text[j] == '"':
                if j + 1 < n and text[j + 1] == '"':
                    j += 2
                    continue
                return j + 1, head, 1
            j += 1
        else:
            if text[j] == "\\":
                j += 2
                continue
            if text[j] == '"':
                return j + 1, head, 1
            if text[j] == "\n":
                return j, head, 0
            j += 1
    return n, head, 0


def mask_csharp(text: str, *, strings: bool = True) -> str:
    """``text`` with comments, and with ``strings`` also string and character literals
    (verbatim ``@"…"``, interpolated ``$"…"``, raw ``\"\"\"…\"\"\"``) replaced by spaces (same
    length, line breaks kept)."""
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
            continue
        if c == "/" and nxt == "*":
            end = text.find("*/", i + 2)
            end = n if end < 0 else end + 2
            out.append(_blank(text[i:end]))
            i = end
            continue
        quote_at = i
        while quote_at < n and quote_at - i < 3 and text[quote_at] in "$@":
            quote_at += 1  # $"…", @"…", $@"…", $$"""…"""
        if quote_at < n and text[quote_at] == '"':
            end, head, tail = _string_end(text, i, quote_at)
            literal = text[i:end]
            if strings:
                inner = literal[head : len(literal) - tail]
                out.append(literal[:head] + _blank(inner) + literal[len(literal) - tail :])
            else:
                out.append(literal)
            i = end
            continue
        if c == "'":
            j = i + 1
            while j < n and text[j] != "'" and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            end = min(j + 1, n)
            out.append(
                c + _blank(text[i + 1 : end - 1]) + text[end - 1] if strings else text[i:end]
            )
            i = end
            continue
        out.append(c)
        i += 1
    return "".join(out)


def matching(masked: str, opening: int) -> int:
    """The offset of the bracket closing the one at ``opening``, or -1."""
    pairs = {"(": ")", "{": "}", "[": "]", "<": ">"}
    close = pairs[masked[opening]]
    depth = 0
    for i in range(opening, len(masked)):
        if masked[i] == masked[opening]:
            depth += 1
        elif masked[i] == close:
            depth -= 1
            if depth == 0:
                return i
    return -1


@dataclass(frozen=True)
class TypeBlock:
    name: str  # as the TRX names it: Namespace.Outer+Inner
    start: int
    body_start: int  # offset of "{"
    body_end: int  # offset of "}"


@dataclass(frozen=True)
class CaseBlock:
    __test__ = False  # not a pytest test class

    test_id: str  # dotnet:Namespace.Class#Method
    class_name: str
    method: str
    start: int  # line start of the first attribute
    end: int  # just past the body
    line: int
    body_start: int
    body_end: int
    expected: str  # an [ExpectedException(...)], normalized, or ""


def _namespaces(masked: str) -> list[tuple[str, int, int]]:
    """``(name, start, end)`` of each namespace: a block's braces, or a file-scoped one to the
    end of the file."""
    found: list[tuple[str, int, int]] = []
    for match in _NAMESPACE.finditer(masked):
        if match.group(2) == ";":
            found.append((match.group(1), match.end(), len(masked)))
            continue
        brace = match.end() - 1
        closing = matching(masked, brace)
        if closing > 0:
            found.append((match.group(1), brace, closing))
    return found


def types(masked: str) -> list[TypeBlock]:
    """Every named type declared in the file, named as the TRX names it."""
    spaces = _namespaces(masked)
    found: list[tuple[str, int, int, int]] = []
    for match in _TYPE.finditer(masked):
        position = match.end()
        depth = 0
        brace = -1
        while position < len(masked):
            c = masked[position]
            if c in "(<[":
                depth += 1
            elif c in ")>]":
                depth -= 1
            elif c == "{" and depth <= 0:
                brace = position
                break
            elif c == ";" and depth <= 0:
                break
            position += 1
        if brace < 0:
            continue
        closing = matching(masked, brace)
        if closing < 0:
            continue
        found.append((match.group(1), match.start(), brace, closing))
    blocks: list[TypeBlock] = []
    for name, start, body_start, body_end in found:
        outer = [f for f in found if f[2] < start and body_end <= f[3]]
        chain = [f[0] for f in sorted(outer, key=lambda f: f[2])] + [name]
        namespace = ".".join(n for n, s, e in sorted(spaces, key=lambda x: x[1]) if s < start < e)
        prefix = f"{namespace}." if namespace else ""
        blocks.append(TypeBlock(prefix + "+".join(chain), start, body_start, body_end))
    return blocks


def _owner(blocks: list[TypeBlock], offset: int) -> TypeBlock | None:
    inside = [b for b in blocks if b.body_start < offset < b.body_end]
    return max(inside, key=lambda b: b.body_start) if inside else None


def _at_member_level(masked: str, owner: TypeBlock, offset: int) -> bool:
    depth = 0
    for c in masked[owner.body_start + 1 : offset]:
        if c in "({[":
            depth += 1
        elif c in ")}]":
            depth -= 1
    return depth == 0


def _attribute_names(body: str) -> list[str]:
    """The attribute names in one ``[...]`` list (``Test``, ``TestCase``), suffix dropped."""
    names: list[str] = []
    depth = 0
    piece: list[str] = []
    for c in body + ",":
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        if c == "," and depth == 0:
            text = "".join(piece).strip()
            text = re.sub(r"^\w+\s*:\s*", "", text)  # [method: Test]
            match = re.match(r"(?:[\w]+\.)*(\w+)", text)
            if match:
                name = match.group(1)
                names.append(name[: -len("Attribute")] if name.endswith("Attribute") else name)
            piece = []
        else:
            piece.append(c)
    return names


def _method_after(masked: str, position: int) -> tuple[str, int, int] | None:
    """The name, body start and body end of the method declared at ``position``: a block
    (``{`` to ``}``) or an expression body (``=>`` to ``;``)."""
    paren = masked.find("(", position)
    if paren < 0:
        return None
    name = _IDENT_BEFORE.search(masked, position, paren)
    if name is None or any(c in masked[position:paren] for c in "{};="):
        return None
    closing = matching(masked, paren)
    if closing < 0:
        return None
    probe = closing + 1
    while probe < len(masked) and masked[probe] not in "{;=":
        probe += 1
    if probe >= len(masked):
        return None
    if masked[probe] == "{":
        end = matching(masked, probe)
        return (name.group(1), probe, end) if end > 0 else None
    if masked.startswith("=>", probe):
        end = masked.find(";", probe)
        return (name.group(1), probe + 1, end) if end > 0 else None
    return None


def _line_start(text: str, offset: int) -> int:
    start = text.rfind("\n", 0, offset) + 1
    return start if not text[start:offset].strip() else offset


def _normalize(call: str) -> str:
    return _SPACE_AROUND.sub(r"\1", _SPACES.sub(" ", call)).strip()


def find_cases(text: str) -> list[CaseBlock]:
    """Every test method in ``text``, in file order."""
    masked = mask_csharp(text)
    kept = mask_csharp(text, strings=False)
    blocks = types(masked)
    cases: list[CaseBlock] = []
    taken: set[int] = set()
    position = 0
    while (opening := masked.find("[", position)) >= 0:
        position = opening + 1
        owner = _owner(blocks, opening)
        if owner is None or not _at_member_level(masked, owner, opening):
            continue
        before = masked[:opening].rstrip()
        if before and before[-1] not in ";{}]":
            continue  # an indexer or array, not an attribute list
        first = opening
        lists: list[tuple[int, int]] = []
        probe = opening
        while probe < len(masked) and masked[probe] == "[":
            closing = matching(masked, probe)
            if closing < 0:
                break
            lists.append((probe, closing))
            probe = closing + 1
            while probe < len(masked) and masked[probe].isspace():
                probe += 1
        names = [n for start, end in lists for n in _attribute_names(masked[start + 1 : end])]
        position = probe
        if not _TEST_ATTRIBUTES.intersection(names):
            continue
        found = _method_after(masked, probe)
        if found is None or found[1] in taken:
            continue
        name, body_start, body_end = found
        taken.add(body_start)
        expected = ""
        for start, end in lists:
            match = re.search(
                r"(?:[\w]+\.)*ExpectedException(?:Attribute)?\s*\(", masked[start:end]
            )
            if match is not None:
                paren = start + match.end() - 1
                closing = matching(masked, paren)
                if 0 < closing <= end:
                    expected = _normalize(kept[start + match.start() : closing + 1])
        cases.append(
            CaseBlock(
                test_id=f"dotnet:{owner.name}#{name}",
                class_name=owner.name,
                method=name,
                start=_line_start(text, first),
                end=body_end + 1,
                line=text.count("\n", 0, first) + 1,
                body_start=body_start,
                body_end=body_end,
                expected=expected,
            )
        )
    return sorted(cases, key=lambda c: c.start)


def assertions(text: str, block: CaseBlock) -> list[str]:
    """The assertion calls in ``block``'s body (with their chained calls), normalized, in
    order; an ``[ExpectedException]`` comes first."""
    masked = mask_csharp(text)
    kept = mask_csharp(text, strings=False)
    found: list[str] = []
    if block.expected:
        found.append(f"[{block.expected}]")
    position = block.body_start
    while (match := _ASSERTION.search(masked, position, block.body_end)) is not None:
        opening = match.end() - 1
        closing = matching(masked, opening)
        if closing < 0 or closing > block.body_end:
            break
        end = closing + 1
        while True:  # x.Should().Be(3).And.NotBeNull()
            chained = re.match(
                r"\s*\.\s*(?:And\s*\.\s*)?\w+\s*(?:<[^<>()]*>)?\s*\(", masked[end : block.body_end]
            )
            if chained is None:
                break
            nested = matching(masked, end + chained.end() - 1)
            if nested < 0:
                break
            end = nested + 1
        start = match.start() + (1 if masked[match.start()] == "." else 0)
        found.append(_normalize(kept[start:end]))
        position = end
    return found


def disabled_markers(path: str, text: str) -> list[str]:
    """``path:line: marker`` for each attribute or ``Skip =`` that disables a test, and each
    call that skips one (``Assert.Ignore``, ``Assert.Inconclusive``, assumptions) (I-3)."""
    masked = mask_csharp(text)
    found: list[str] = []
    for match in _DISABLED.finditer(masked):
        line = masked.count("\n", 0, match.start()) + 1
        marker = match.group(0).lstrip("[").rstrip("(").strip()
        found.append(f"{path}:{line}: {marker}")
    return found
