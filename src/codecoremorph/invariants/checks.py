"""The safety invariants as pure checks over what an attempt changed (SPEC §7.10, FR-4.4).

Each function returns one :class:`InvariantResult`. A failed ``error`` result fails the attempt
and its message becomes the agent's feedback; a failed ``warning`` is a review item for the
report; ``needs_approval`` sends the unit to its gate when ``approvals.unit`` covers it.

I-4 (the assertion guard) arrives with characterization tests (Phase 6). I-5 (baseline parity)
and I-9 (rule post-conditions) are the verification's own steps; they are recorded as results
by the caller.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from codecoremorph.core.models import (
    Boundary,
    InvariantResult,
    Severity,
    Symbol,
    SymbolKind,
    TestBaseline,
    TestReport,
    Visibility,
)

PathTest = Callable[[str], bool]

MAX_LISTED = 10

# Markers that silence a compiler, linter or analyzer (I-7), by language family. A rule with
# `allow_suppressions` may add them.
SUPPRESSIONS: dict[str, re.Pattern[str]] = {
    "NOLINT": re.compile(r"\bNOLINT(?:NEXTLINE|BEGIN|END)?\b"),
    "#pragma warning(disable": re.compile(r"#\s*pragma\s+warning\s*\(\s*disable\b"),
    "#pragma diagnostic ignored": re.compile(
        r"#\s*pragma\s+(?:clang|GCC)\s+diagnostic\s+ignored\b"
    ),
    "-Wno-": re.compile(r"(?<![\w-])-Wno-[\w=+-]+"),
    "@SuppressWarnings": re.compile(r"@SuppressWarnings\b"),
    "#pragma warning disable": re.compile(r"#\s*pragma\s+warning\s+disable\b"),
    "[SuppressMessage]": re.compile(r"\[\s*(?:assembly\s*:\s*)?SuppressMessage\b"),
    "#[allow(...)]": re.compile(r"#!?\[\s*allow\s*\("),
}

# Symbols whose signature is an interface others depend on (I-6). Macros have no signature in
# the index, and a typedef rewritten as an alias declaration keeps its signature.
_API_VISIBILITY = frozenset({Visibility.PUBLIC, Visibility.PROTECTED, Visibility.NONE})
_UNCOMPARED_KINDS = frozenset({SymbolKind.NAMESPACE, SymbolKind.MACRO})


def _listed(items: Sequence[str]) -> str:
    shown = ", ".join(items[:MAX_LISTED])
    more = len(items) - MAX_LISTED
    return f"{shown} and {more} more" if more > 0 else shown


def _ok(invariant: str, message: str = "") -> InvariantResult:
    return InvariantResult(invariant=invariant, passed=True, message=message)


# --------------------------------------------------------------------------- the patch


@dataclass(frozen=True)
class PatchLines:
    """Added and removed lines of a unified diff, by file (the new path, or the old one for a
    deleted file)."""

    added: dict[str, list[str]] = field(default_factory=dict[str, list[str]])
    removed: dict[str, list[str]] = field(default_factory=dict[str, list[str]])


def _diff_path(header: str) -> str | None:
    name = header[4:].strip()
    if name == "/dev/null":
        return None
    if name.startswith('"') and name.endswith('"'):
        name = name[1:-1]
    return name[2:] if name[:2] in ("a/", "b/") else name


def patch_lines(patch: str) -> PatchLines:
    """Parse ``git diff`` output. Binary files contribute no lines."""
    lines = PatchLines()
    old: str | None = None
    current: str | None = None
    in_hunk = False
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            old, current, in_hunk = None, None, False
        elif not in_hunk and line.startswith("--- "):
            old = _diff_path(line)
        elif not in_hunk and line.startswith("+++ "):
            current = _diff_path(line) or old
        elif line.startswith("@@"):
            in_hunk = True
        elif in_hunk and current is not None and line[:1] == "+":
            lines.added.setdefault(current, []).append(line[1:])
        elif in_hunk and current is not None and line[:1] == "-":
            lines.removed.setdefault(current, []).append(line[1:])
    return lines


# --------------------------------------------------------------------------- I-1, I-2


def check_path_scope(changed: Iterable[str], *, excluded: PathTest) -> InvariantResult:
    """I-1: every changed path stays inside the worktree, out of ``.git/`` and out of the
    excluded (third-party, generated) paths."""
    bad: list[str] = []
    for path in sorted(set(changed)):
        parts = path.replace("\\", "/").split("/")
        if (
            path.startswith(("/", "\\"))
            or re.match(r"^[A-Za-z]:", path)
            or ".." in parts
            or ".git" in parts
            or excluded(path)
        ):
            bad.append(path)
    if not bad:
        return _ok("I-1")
    return InvariantResult(
        invariant="I-1",
        passed=False,
        message=f"Changes to paths that must not change (excluded, third-party or outside the "
        f"project): {_listed(bad)}. Leave these files as they are.",
    )


def check_unit_scope(changed: Iterable[str], *, allowed: PathTest) -> InvariantResult:
    """I-2: the attempt changed only the unit's files and the expansions its rules allow."""
    outside = sorted(p for p in set(changed) if not allowed(p))
    if not outside:
        return _ok("I-2")
    return InvariantResult(
        invariant="I-2",
        passed=False,
        message=f"Files outside the unit changed: {_listed(outside)}. Change only the unit's "
        "files.",
    )


# --------------------------------------------------------------------------- I-3


def _marker_counts(markers: Iterable[str]) -> Counter[tuple[str, str]]:
    """``path:line: marker`` strings, counted by (path, marker): line numbers move."""
    counts: Counter[tuple[str, str]] = Counter()
    for item in markers:
        path, _, rest = item.partition(":")
        _, _, marker = rest.partition(":")
        counts[(path, marker.strip())] += 1
    return counts


def check_tests_kept(
    baseline: TestBaseline,
    report: TestReport,
    *,
    markers_before: Iterable[str] = (),
    markers_after: Iterable[str] = (),
) -> InvariantResult:
    """I-3: no test was removed or disabled. The test count doesn't drop below the baseline's
    (flaky tests may be missing in any run, so they don't count), and the adapter's disable
    markers (``DISABLED_``, ``GTEST_SKIP``, ``#if 0`` around tests, ...) don't increase."""
    problems: list[str] = []
    seen = {c.test_id for c in report.cases}
    expected = set(baseline.passing) | set(baseline.failing) | set(baseline.skipped)
    gone = sorted(expected - seen)
    if gone:
        problems.append(f"tests that existed before are gone: {_listed(gone)}")
    before = _marker_counts(markers_before)
    after = _marker_counts(markers_after)
    added = sorted(
        f"{path}: {marker}" for (path, marker), n in after.items() if n > before[(path, marker)]
    )
    if added:
        problems.append(f"tests were disabled or skipped: {_listed(added)}")
    if not problems:
        return _ok("I-3")
    return InvariantResult(
        invariant="I-3",
        passed=False,
        message="Tests must not be removed, disabled or skipped: "
        + "; ".join(problems)
        + ". Restore them; adapt a test's calls only where the migration requires it.",
    )


# --------------------------------------------------------------------------- I-6


@dataclass(frozen=True)
class ApiChange:
    qualified_name: str
    file: str
    before: tuple[str, ...]
    after: tuple[str, ...]
    boundary: Boundary
    symbol_ids: tuple[str, ...]

    def describe(self) -> str:
        if not self.after:
            return f"{self.qualified_name} ({self.file}) was removed"
        old = "; ".join(self.before)
        new = "; ".join(self.after)
        return f"{self.qualified_name} ({self.file}) changed from `{old}` to `{new}`"


def is_api_symbol(symbol: Symbol) -> bool:
    """A symbol other code may depend on: on a boundary (C ABI, JNI, P/Invoke, public API) and
    not private."""
    return (
        symbol.boundary is not Boundary.NONE
        and symbol.visibility in _API_VISIBILITY
        and symbol.kind not in _UNCOMPARED_KINDS
    )


# SFINAE as the indexer spells it, in a return type or a template argument. Moving the
# constraint to a requires-clause or a concept (use_concepts_for_templates) keeps the interface
# but changes the spelled signature, so comparisons reduce it to the type it yields.
_ENABLE_IF = re.compile(r"(?:typename\s+)?(?:std::)?enable_if(?P<alias>_t)?<")
_OPENING, _CLOSING = "([{", ")]}"


def _closing_bracket(text: str, start: int) -> int | None:
    """The index of the ``>`` closing the ``<`` just before ``start``; None if unbalanced."""
    angle, nested = 1, 0
    for index in range(start, len(text)):
        char = text[index]
        if char in _OPENING:
            nested += 1
        elif char in _CLOSING:
            nested -= 1
        elif nested == 0 and char == "<":
            angle += 1
        elif nested == 0 and char == ">":
            angle -= 1
            if angle == 0:
                return index
    return None


def _template_arguments(text: str) -> list[str]:
    """Split ``text`` at the commas outside brackets."""
    parts: list[str] = []
    depth, begin = 0, 0
    for index, char in enumerate(text):
        if char in _OPENING or char == "<":
            depth += 1
        elif char in _CLOSING or char == ">":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(text[begin:index])
            begin = index + 1
    parts.append(text[begin:])
    return [p.strip() for p in parts]


def comparable_signature(signature: str) -> str:
    """``signature`` with each ``std::enable_if<C, R>::type`` or ``std::enable_if_t<C, R>``
    replaced by ``R`` (``void`` without one), for the I-6 comparison."""
    text, position = signature, 0
    while (match := _ENABLE_IF.search(text, position)) is not None:
        close = _closing_bracket(text, match.end())
        if close is None:
            break
        end = close + 1
        if not match.group("alias"):
            if not text.startswith("::type", end):
                position = match.end()
                continue
            end += len("::type")
        arguments = _template_arguments(text[match.end() : close])
        result = arguments[1] if len(arguments) > 1 else "void"
        text = text[: match.start()] + result + text[end:]
        position = match.start()  # the result may hold another one
    return " ".join(text.split())


def _signatures(symbols: Iterable[Symbol]) -> dict[tuple[str, str], list[Symbol]]:
    grouped: dict[tuple[str, str], list[Symbol]] = {}
    for symbol in symbols:
        if is_api_symbol(symbol):
            grouped.setdefault((symbol.file, symbol.qualified_name), []).append(symbol)
    return grouped


def api_changes(
    before: Iterable[Symbol], after: Iterable[Symbol], *, files: Iterable[str]
) -> list[ApiChange]:
    """API symbols of ``files`` whose signatures changed or disappeared. Additions are not
    changes; a symbol that only moved between the files counts as unchanged, and so does a
    SFINAE constraint that became a requires-clause (:func:`comparable_signature`)."""
    wanted = set(files)
    old = {k: v for k, v in _signatures(before).items() if k[0] in wanted}
    # A partial specialization's name spells its arguments, enable_if included.
    new_by_name: dict[str, set[str]] = {}
    for (_, name), symbols in _signatures(after).items():
        new_by_name.setdefault(comparable_signature(name), set()).update(
            s.signature or s.kind.value for s in symbols
        )
    changes: list[ApiChange] = []
    for (path, name), symbols in sorted(old.items()):
        signatures = {s.signature or s.kind.value for s in symbols}
        now = new_by_name.get(comparable_signature(name), set())
        if {comparable_signature(s) for s in signatures} <= {comparable_signature(s) for s in now}:
            continue
        boundary = next(
            (s.boundary for s in symbols if s.boundary is not Boundary.PUBLIC_API),
            Boundary.PUBLIC_API,
        )
        changes.append(
            ApiChange(
                qualified_name=name,
                file=path,
                before=tuple(sorted(signatures)),
                after=tuple(sorted(now)),
                boundary=boundary,
                symbol_ids=tuple(sorted({s.id for s in symbols})),
            )
        )
    return changes


def check_api(
    changes: Sequence[ApiChange],
    *,
    api_changing: bool,
    covered: Callable[[ApiChange], bool],
    gated: bool,
) -> InvariantResult:
    """I-6: boundary and public-API signatures stay the same, unless the unit's rule is
    ``api_changing`` and every side of the change is in the unit (``covered``: every file
    referencing the symbol is one of the unit's files). A C ABI, JNI or P/Invoke boundary has a
    side in another language, so it is never covered. Other changes fail the attempt, or wait
    for approval when the unit gate covers API changes (``gated``)."""
    blocked = [
        c
        for c in changes
        if not (api_changing and c.boundary is Boundary.PUBLIC_API and covered(c))
    ]
    if not blocked:
        allowed = [c.describe() for c in changes]
        return _ok(
            "I-6", f"API changes with every side in the unit: {_listed(allowed)}" if allowed else ""
        )
    described = [c.describe() for c in blocked]
    if gated:
        return InvariantResult(
            invariant="I-6",
            passed=False,
            severity=Severity.WARNING,
            needs_approval=True,
            message=f"The unit changes an interface other code depends on: {_listed(described)}.",
        )
    return InvariantResult(
        invariant="I-6",
        passed=False,
        message=f"The unit changes an interface other code depends on: {_listed(described)}. "
        "Keep these signatures as they are; adapt the code behind them instead.",
    )


# --------------------------------------------------------------------------- I-7, I-8


def check_suppressions(lines: PatchLines, *, allowed: bool) -> InvariantResult:
    """I-7: no new warning suppressions, unless one of the unit's rules allows them. A marker
    only moved (removed and added again) is not new."""
    if allowed:
        return _ok("I-7")
    added: list[str] = []
    for path in sorted(set(lines.added) | set(lines.removed)):
        for name, pattern in SUPPRESSIONS.items():
            plus = sum(len(pattern.findall(text)) for text in lines.added.get(path, []))
            minus = sum(len(pattern.findall(text)) for text in lines.removed.get(path, []))
            if plus > minus:
                added.append(f"{name} in {path}")
    if not added:
        return _ok("I-7")
    return InvariantResult(
        invariant="I-7",
        passed=False,
        message=f"New warning suppressions: {_listed(added)}. Fix the cause of the warning "
        "instead of silencing it.",
    )


def check_diff_size(changed_lines: int, *, limit: int) -> InvariantResult:
    """I-8: the agent's diff stays within ``guardrails.max_unit_diff_lines``."""
    if changed_lines <= limit:
        return _ok("I-8")
    return InvariantResult(
        invariant="I-8",
        passed=False,
        message=f"The edits change {changed_lines} lines, more than the limit of {limit} "
        "(guardrails.max_unit_diff_lines). Make only the changes the unit's rules need.",
    )


# --------------------------------------------------------------------------- I-10


def check_warnings(
    before: Mapping[str, int],
    after: Mapping[str, int],
    *,
    files: Iterable[str],
    fail: bool,
) -> InvariantResult:
    """I-10: no unit file has more compiler warnings than before the unit. With
    ``new_warnings: fail`` that fails the attempt; with ``warn`` it is a review item."""
    more = [
        f"{path} ({before.get(path, 0)} -> {after[path]})"
        for path in sorted(set(files))
        if after.get(path, 0) > before.get(path, 0)
    ]
    if not more:
        return _ok("I-10")
    text = f"New compiler warnings in: {_listed(more)}."
    if fail:
        return InvariantResult(
            invariant="I-10", passed=False, message=f"{text} Fix them before submitting."
        )
    return InvariantResult(invariant="I-10", passed=False, severity=Severity.WARNING, message=text)


# --------------------------------------------------------------------------- outcome


@dataclass
class InvariantOutcome:
    results: list[InvariantResult] = field(default_factory=list[InvariantResult])

    @property
    def errors(self) -> list[InvariantResult]:
        return [r for r in self.results if not r.passed and r.severity is Severity.ERROR]

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def warnings(self) -> list[str]:
        return [
            f"{r.invariant}: {r.message}"
            for r in self.results
            if not r.passed and r.severity is not Severity.ERROR and not r.needs_approval
        ]

    @property
    def approvals(self) -> list[str]:
        return [f"{r.invariant}: {r.message}" for r in self.results if r.needs_approval]

    def feedback(self) -> str:
        lines = ["The changes break CodeCoreMorph's safety rules:"]
        lines += [f"- {r.invariant}: {r.message}" for r in self.errors]
        return "\n".join(lines)
