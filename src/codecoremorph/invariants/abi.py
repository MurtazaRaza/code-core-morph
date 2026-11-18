"""The C interface a port keeps (D-56, SPEC §11.5): the functions its kept headers declare,
against the ones its new code exports, both as the source adapter reads C (canonical types).

A port replaces the code behind a C header with code in another language. The header never
changes, so callers compile as before; this check makes sure they also link and call the same
thing: every function the header declares is exported, with the same signature.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

MAX_SHOWN = 20
_TAGS = frozenset({"enum", "struct", "union"})
_TOKEN = re.compile(r"\w+|[^\w\s]")


def _same(a: str, b: str) -> bool:
    """Whether two canonical C signatures name the same types. Spacing doesn't count, nor a tag
    keyword one side spells and the other doesn't: libclang spells an anonymous enum or struct
    that a typedef names by the typedef's name (snappy-c.h's `snappy_status`), and cbindgen's
    `typedef enum snappy_status {...} snappy_status;` as `enum snappy_status` (D-62). Canonical
    types have no typedefs left, so a bare name there is such a tag type. Two different
    keywords still differ."""
    x, y = _TOKEN.findall(a), _TOKEN.findall(b)
    i = j = 0
    while i < len(x) and j < len(y):
        if x[i] == y[j]:
            i, j = i + 1, j + 1
        elif x[i] in _TAGS and y[j] not in _TAGS:
            i += 1
        elif y[j] in _TAGS and x[i] not in _TAGS:
            j += 1
        else:
            return False
    return i == len(x) and j == len(y)


def c_abi_differences(expected: Mapping[str, str], exported: Mapping[str, str]) -> list[str]:
    """What the new code gets wrong, one line per function, sorted by name: a function the
    headers declare that it doesn't export, or exports with another signature. Exports the
    headers don't declare are the new code's own business."""
    problems: list[str] = []
    for name, signature in sorted(expected.items()):
        found = exported.get(name)
        if found is None:
            problems.append(f"`{name}` isn't exported; the header declares `{signature}`")
        elif not _same(found, signature):
            problems.append(
                f"`{name}` is exported as `{found}`, but the header declares `{signature}`"
            )
    return problems


def c_abi_feedback(rule_id: str, headers: Sequence[str], problems: Sequence[str]) -> str:
    """The post-condition's feedback for ``problems`` (empty when there are none)."""
    if not problems:
        return ""
    lines = [
        f"{rule_id}: the new code must export the C interface of {', '.join(headers)}, with the "
        "same signatures (canonical C types; parameter names don't matter):"
    ]
    lines += [f"- {p}" for p in problems[:MAX_SHOWN]]
    if len(problems) > MAX_SHOWN:
        lines.append(f"- … and {len(problems) - MAX_SHOWN} more")
    return "\n".join(lines)


_RUST_TYPES = r"(?:Option|Box|Vec|Result|String|&\w*|\w+<)"
# clang's in C and C++, gcc's in C, and gcc's in C++.
_RUST_TYPE = re.compile(
    rf"unknown type name '{_RUST_TYPES}|'{_RUST_TYPES}[^']*' does not name a type"
)
_CONFLICT = re.compile(r"conflicting (?:types for|declaration of C function) '(?:[^'(]*\s)?(\w+)")
CBINDGEN_MARK = "cbindgen"  # in the warning at the top of each header cbindgen writes for a port


def cbindgen_tip(reason: str) -> str:
    """A hint for a header cbindgen wrote from the new code that doesn't parse as C (or
    compile in the C++ that includes it), by the error (D-89): what cbindgen copied as it was,
    or a bridge's own C declarations."""
    if _RUST_TYPE.search(reason):
        return (
            " cbindgen copies a type it can't translate into the header as it is: write a "
            'function pointer parameter as `Option<unsafe extern "C" fn(..) -> T>` in the '
            "signature itself, not through a type alias, and keep Rust-only types out of "
            "exported signatures."
        )
    found = _CONFLICT.search(reason)
    if found:
        return (
            f" cbindgen wrote the crate's own declaration of `{found.group(1)}` (an "
            '`extern "C"` block calling C) into the header, where it conflicts with the C '
            "library's: put such declarations in a module marked `/// cbindgen:ignore`."
        )
    return ""


def cbindgen_build_tip(errors: Sequence[tuple[str, str]]) -> str:
    """The hint for a build's errors in a header cbindgen wrote (a C++ port's bridge header,
    which the C++ includes, D-89): ``errors`` are (the start of the file the error is in,
    its message). Empty when no error is in such a header or none has a hint."""
    for head, message in errors:
        if CBINDGEN_MARK in head:
            tip = cbindgen_tip(message)
            if tip:
                return f"Those errors are in a header cbindgen writes from the Rust.{tip}"
    return ""
