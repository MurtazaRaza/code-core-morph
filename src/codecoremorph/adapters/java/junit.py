"""JUnit test sources: their test methods, assertions, disabled tests and the determinism lint
(SPEC §7.11, §11.2; I-3, I-4).

A test method is one annotated ``@Test`` (JUnit 4 and 5), ``@ParameterizedTest``,
``@RepeatedTest``, ``@TestFactory`` or ``@TestTemplate``, or a JUnit 3 ``public void testX()``
in a class that extends ``TestCase``. Its test ID is the one Surefire reports,
``junit:<package>.<Class>#<method>``, with ``$`` between nested classes.

Assertions are the calls of ``assert…``, ``assertThat`` (with what is chained to it), ``fail``
and ``verify…`` in a method's body, and the ``expected``/``timeout`` of a JUnit 4 ``@Test``,
comments removed and whitespace normalized.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_TEST_ANNOTATION = re.compile(
    r"@(?:org\.junit\.(?:jupiter\.api\.|jupiter\.params\.)?)?"
    r"(Test|ParameterizedTest|RepeatedTest|TestFactory|TestTemplate)\b(?!\s*\.)"
)
_TYPE = re.compile(r"(?<![\w$.@])(class|interface|enum|record)\s+([A-Za-z_$][\w$]*)")
_PACKAGE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.MULTILINE)
_ANNOTATION = re.compile(r"@[\w$.]+")
_IDENT_BEFORE = re.compile(r"([A-Za-z_$][\w$]*)\s*$")
_JUNIT3_METHOD = re.compile(r"\bpublic\s+(?:final\s+)?void\s+(test[\w$]*)\s*\(\s*\)")
_EXTENDS_TESTCASE = re.compile(r"\bextends\s+(?:junit\.framework\.)?TestCase\b")
_ASSERTION = re.compile(
    r"(?<![\w$])(?:[\w$]+\.)*(assert[A-Z][\w$]*|assertThat|fail|verify[\w$]*)\s*\("
)
_DISABLED = re.compile(
    r"@(?:org\.junit\.)?Ignore\b|@(?:[\w.]*\.)?Disabled[\w$]*\b"
    r"|(?<![\w$])(?:[\w$]+\.)*assume[A-Z][\w$]*\s*\("
)
_SPACE_AROUND = re.compile(r"\s*([(),\[\]{}.])\s*")
_SPACES = re.compile(r"\s+")


def _blank(text: str) -> str:
    return re.sub(r"[^\n]", " ", text)


def mask_java(text: str, *, strings: bool = True) -> str:
    """``text`` with comments, and with ``strings`` also string, text-block and character
    literals, replaced by spaces (same length, line breaks kept)."""
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
        elif text.startswith('"""', i):
            j = i + 3
            while j < n and not text.startswith('"""', j):
                j += 2 if text[j] == "\\" else 1
            end = min(j + 3, n)
            out.append('"' + _blank(text[i + 1 : end - 1]) + '"' if strings else text[i:end])
            i = end
        elif c in "\"'":
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            end = min(j + 1, n)
            out.append(
                c + _blank(text[i + 1 : end - 1]) + text[end - 1] if strings else text[i:end]
            )
            i = end
        else:
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
    name: str  # the binary name: package.Outer$Inner
    start: int
    body_start: int  # offset of "{"
    body_end: int  # offset of "}"
    header: str  # the text between the name and "{"


@dataclass(frozen=True)
class CaseBlock:
    __test__ = False  # not a pytest test class

    test_id: str  # junit:package.Class#method
    class_name: str
    method: str
    start: int  # line start of the first annotation (or of the declaration)
    end: int  # just past the closing brace
    line: int
    body_start: int
    body_end: int
    annotation: str  # the @Test(...) arguments, normalized, or ""


def types(masked: str) -> list[TypeBlock]:
    """Every named type declared in the file, with its binary name."""
    package_match = _PACKAGE.search(masked)
    package = package_match.group(1) + "." if package_match else ""
    found: list[tuple[str, int, int, int, str]] = []
    for match in _TYPE.finditer(masked):
        position = match.end()
        depth = 0
        brace = -1
        while position < len(masked):
            c = masked[position]
            if c in "(<":
                depth += 1
            elif c in ")>":
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
        found.append((match.group(2), match.start(), brace, closing, masked[match.end() : brace]))
    blocks: list[TypeBlock] = []
    for name, start, body_start, body_end, header in found:
        outer = [f for f in found if f[2] < start and body_end <= f[3]]
        chain = [f[0] for f in sorted(outer, key=lambda f: f[2])] + [name]
        blocks.append(TypeBlock(package + "$".join(chain), start, body_start, body_end, header))
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


def _skip_annotations(masked: str, position: int) -> int:
    while True:
        while position < len(masked) and masked[position].isspace():
            position += 1
        match = _ANNOTATION.match(masked, position)
        if match is None:
            return position
        position = match.end()
        probe = position
        while probe < len(masked) and masked[probe].isspace():
            probe += 1
        if probe < len(masked) and masked[probe] == "(":
            closing = matching(masked, probe)
            if closing < 0:
                return len(masked)
            position = closing + 1


def _method_after(masked: str, position: int) -> tuple[str, int, int] | None:
    """The name, body ``{`` and body ``}`` of the method declared at ``position``."""
    paren = masked.find("(", position)
    if paren < 0:
        return None
    name = _IDENT_BEFORE.search(masked, position, paren)
    if name is None or any(c in masked[position:paren] for c in "{};="):
        return None
    closing = matching(masked, paren)
    if closing < 0:
        return None
    brace = closing + 1
    while brace < len(masked) and masked[brace] not in "{;":
        brace += 1
    if brace >= len(masked) or masked[brace] != "{":
        return None
    end = matching(masked, brace)
    if end < 0:
        return None
    return name.group(1), brace, end


def _line_start(text: str, offset: int) -> int:
    start = text.rfind("\n", 0, offset) + 1
    return start if not text[start:offset].strip() else offset


def _normalize(call: str) -> str:
    return _SPACE_AROUND.sub(r"\1", _SPACES.sub(" ", call)).strip()


def find_cases(text: str) -> list[CaseBlock]:
    """Every test method in ``text``, in file order."""
    masked = mask_java(text)
    blocks = types(masked)
    cases: list[CaseBlock] = []
    taken: set[int] = set()
    for match in _TEST_ANNOTATION.finditer(masked):
        owner = _owner(blocks, match.start())
        if owner is None or not _at_member_level(masked, owner, match.start()):
            continue
        # Annotations before this one belong to the same method.
        first = match.start()
        while True:
            before = masked.rfind("@", owner.body_start, first)
            if before < 0:
                break
            between = masked[before:first]
            while (flat := re.sub(r"\([^()]*\)", "", between)) != between:
                between = flat  # @ValueSource(ints = {1, 2}) keeps its braces in parentheses
            if any(c in between for c in ";{}"):
                break
            first = before
        declaration = _skip_annotations(masked, first)
        found = _method_after(masked, declaration)
        if found is None or found[1] in taken:
            continue
        name, brace, end = found
        taken.add(brace)
        args = ""
        probe = match.end()
        while probe < len(masked) and masked[probe].isspace():
            probe += 1
        if match.group(1) == "Test" and probe < len(masked) and masked[probe] == "(":
            closing = matching(masked, probe)
            if closing > 0:
                kept = mask_java(text, strings=False)
                args = _normalize(kept[probe + 1 : closing])
        cases.append(_case(text, owner, name, first, brace, end, args))
    for owner in blocks:
        if not _EXTENDS_TESTCASE.search(owner.header):
            continue
        for match in _JUNIT3_METHOD.finditer(masked, owner.body_start, owner.body_end):
            if _owner(blocks, match.start()) is not owner:
                continue
            found = _method_after(masked, match.start())
            if found is None or found[1] in taken:
                continue
            taken.add(found[1])
            cases.append(_case(text, owner, found[0], match.start(), found[1], found[2], ""))
    return sorted(cases, key=lambda c: c.start)


def _case(
    text: str, owner: TypeBlock, name: str, first: int, brace: int, end: int, args: str
) -> CaseBlock:
    start = _line_start(text, first)
    return CaseBlock(
        test_id=f"junit:{owner.name}#{name}",
        class_name=owner.name,
        method=name,
        start=start,
        end=end + 1,
        line=text.count("\n", 0, first) + 1,
        body_start=brace,
        body_end=end,
        annotation=args,
    )


def assertions(text: str, block: CaseBlock) -> list[str]:
    """The assertion calls in ``block``'s body (with their chained calls), normalized, in
    order; a JUnit 4 ``@Test(expected = …)`` comes first."""
    masked = mask_java(text)
    kept = mask_java(text, strings=False)
    found: list[str] = []
    if block.annotation:
        found.append(f"@Test({block.annotation})")
    position = block.body_start
    while (match := _ASSERTION.search(masked, position, block.body_end)) is not None:
        opening = match.end() - 1
        closing = matching(masked, opening)
        if closing < 0 or closing > block.body_end:
            break
        end = closing + 1
        while True:  # assertThat(x).isEqualTo(y).hasSize(2)
            chained = re.match(r"\s*\.\s*[\w$]+\s*\(", masked[end : block.body_end])
            if chained is None:
                break
            nested = matching(masked, end + chained.end() - 1)
            if nested < 0:
                break
            end = nested + 1
        found.append(_normalize(kept[match.start() : end]))
        position = end
    return found


def disabled_markers(path: str, text: str) -> list[str]:
    """``path:line: marker`` for each annotation that disables a test and each assumption,
    which skips one (I-3)."""
    masked = mask_java(text)
    found: list[str] = []
    for match in _DISABLED.finditer(masked):
        line = masked.count("\n", 0, match.start()) + 1
        marker = match.group(0).rstrip("(").strip()
        found.append(f"{path}:{line}: {marker}")
    return found


def drop_cases(text: str, test_ids: set[str]) -> str:
    """``text`` without the test methods ``test_ids`` (and the line breaks after them)."""
    out: list[str] = []
    position = 0
    for block in find_cases(text):
        if block.test_id not in test_ids or block.start < position:
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


# The determinism lint (FR-7.3): what a characterization test may not use.
_FREE = r"(?<![\w$])"
_CHECKS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(p), why)
    for p, why in [
        (r"\bSystem\s*\.\s*(currentTimeMillis|nanoTime)\s*\(", "reads the clock"),
        (_FREE + r"new\s+(java\.util\.)?Date\s*\(\s*\)", "reads the clock"),
        (r"\b(Instant|LocalDate|LocalTime|LocalDateTime|ZonedDateTime|OffsetDateTime|Year"
         r"|YearMonth|MonthDay)\s*\.\s*now\s*\(", "reads the clock"),
        (r"\bClock\s*\.\s*system\w*\s*\(", "reads the clock"),
        (r"\bCalendar\s*\.\s*getInstance\s*\(", "reads the clock"),
        (r"\bMath\s*\.\s*random\s*\(", "uses randomness"),
        (_FREE + r"new\s+(java\.util\.)?Random\s*\(\s*\)", "uses randomness"),
        (r"\b(ThreadLocalRandom|SecureRandom|SplittableRandom)\b", "uses randomness"),
        (r"\bUUID\s*\.\s*randomUUID\s*\(", "uses randomness"),
        (_FREE + r"new\s+(java\.io\.)?(File|FileInputStream|FileOutputStream|FileReader"
         r"|FileWriter|RandomAccessFile|PrintWriter\s*\(\s*\")\b", "uses the filesystem"),
        (r"\b(Files|Paths)\s*\.\s*\w+\s*\(", "uses the filesystem"),
        (r"\bPath\s*\.\s*of\s*\(", "uses the filesystem"),
        (r"@TempDir\b|\bTemporaryFolder\b", "uses the filesystem"),
        (_FREE + r"new\s+(java\.net\.)?(Socket|ServerSocket|DatagramSocket|URL)\s*\(",
         "uses the network"),
        (r"\b(HttpURLConnection|HttpClient|InetAddress|URLConnection)\b", "uses the network"),
        (r"\bThread\s*\.\s*sleep\s*\(", "depends on thread timing"),
        (r"\bTimeUnit\s*\.\s*\w+\s*\.\s*sleep\s*\(", "depends on thread timing"),
        (_FREE + r"new\s+Thread\s*\(", "depends on thread timing"),
        (r"\b(Executors|CompletableFuture|ForkJoinPool|CountDownLatch|CyclicBarrier)\b",
         "depends on thread timing"),
    ]
]  # fmt: skip


def nondeterminism(path: str, text: str) -> list[str]:
    """``path:line: what`` for each line of a test that breaks the lint, once per reason."""
    masked = mask_java(text)
    found: list[str] = []
    for number, line in enumerate(masked.splitlines(), start=1):
        reasons: list[str] = []
        for pattern, why in _CHECKS:
            match = pattern.search(line)
            if match is not None and why not in reasons:
                reasons.append(why)
                found.append(f"{path}:{number}: {why} ({match.group(0).strip()})")
    return found
