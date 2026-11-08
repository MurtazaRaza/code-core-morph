"""rust-analyzer's SCIP index of a crate (D-75): where each item is defined and every reference
to it, by file and line, which the item scanner of D-58 can't see.

A Rust source's port orders its modules by the references between them, as a C port orders its
translation units by calls (D-68). ``rust-analyzer scip`` writes a protobuf file; this module
reads the few fields it needs with its own wire-format reader, so no protobuf dependency.

rust-analyzer writes no SCIP relationships: a trait implementation shows in its symbol
(``geo/impl#[Circle][Shape]area().``), a ``use`` is an ordinary reference, and a reference to a
generic item carries no type arguments. Symbols map onto the scanner's item paths (``crate::
module::Type::method``), so the symbol IDs are the same whichever made them.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass

# SymbolRole bits (scip.proto).
DEFINITION = 0x1


class ScipError(ValueError):
    """The SCIP file isn't one this reader understands."""


@dataclass(frozen=True)
class Occurrence:
    symbol: str
    line: int  # 1-based
    column: int  # 1-based
    roles: int

    @property
    def definition(self) -> bool:
        return bool(self.roles & DEFINITION)


@dataclass(frozen=True)
class Document:
    path: str  # relative to the directory rust-analyzer indexed
    occurrences: tuple[Occurrence, ...]


def _varint(data: bytes, i: int) -> tuple[int, int]:
    shift = result = 0
    while True:
        if i >= len(data):
            raise ScipError("a varint runs past the end of its message")
        byte = data[i]
        i += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, i
        shift += 7
        if shift > 63:
            raise ScipError("a varint longer than 64 bits")


def _fields(data: bytes) -> Iterator[tuple[int, int | bytes]]:
    """A message's fields as ``(number, value)``: an int for varints, bytes otherwise."""
    i = 0
    while i < len(data):
        key, i = _varint(data, i)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, i = _varint(data, i)
            yield number, value
        elif wire == 2:
            size, i = _varint(data, i)
            if i + size > len(data):
                raise ScipError("a field runs past the end of its message")
            yield number, data[i : i + size]
            i += size
        elif wire == 1:
            yield number, data[i : i + 8]
            i += 8
        elif wire == 5:
            yield number, data[i : i + 4]
            i += 4
        else:
            raise ScipError(f"unknown wire type {wire}")


def _packed(data: bytes) -> list[int]:
    values: list[int] = []
    i = 0
    while i < len(data):
        value, i = _varint(data, i)
        values.append(value)
    return values


def _occurrence(data: bytes) -> Occurrence | None:
    span: list[int] = []
    symbol = ""
    roles = 0
    for number, value in _fields(data):
        if number == 1:  # range: packed, or one varint per element
            if isinstance(value, bytes):
                span += _packed(value)
            else:
                span.append(value)
        elif number == 2 and isinstance(value, bytes):
            symbol = value.decode("utf-8", errors="replace")
        elif number == 3 and isinstance(value, int):
            roles = value
    if not symbol or len(span) < 3:
        return None
    return Occurrence(symbol=symbol, line=span[0] + 1, column=span[1] + 1, roles=roles)


def read_index(data: bytes) -> list[Document]:
    """The documents of a SCIP index, each with its occurrences."""
    documents: list[Document] = []
    for number, value in _fields(data):
        if number != 2 or not isinstance(value, bytes):  # Index.documents
            continue
        path = ""
        occurrences: list[Occurrence] = []
        for field, item in _fields(value):
            if field == 1 and isinstance(item, bytes):  # Document.relative_path
                path = item.decode("utf-8", errors="replace")
            elif field == 2 and isinstance(item, bytes):  # Document.occurrences
                found = _occurrence(item)
                if found is not None:
                    occurrences.append(found)
        if path:
            documents.append(Document(path=path, occurrences=tuple(occurrences)))
    return documents


def _split_symbol(symbol: str) -> tuple[str, str] | None:
    """``(package name, descriptors)`` of a global symbol (``scheme manager name version
    descriptors``, a space in a part doubled), or None for a local one."""
    if symbol.startswith("local "):
        return None
    parts: list[str] = []
    current: list[str] = []
    i = 0
    while i < len(symbol) and len(parts) < 4:
        if symbol[i] == " ":
            if symbol.startswith("  ", i):
                current.append(" ")
                i += 2
                continue
            parts.append("".join(current))
            current = []
            i += 1
            continue
        current.append(symbol[i])
        i += 1
    if len(parts) < 4:
        return None
    return parts[2], symbol[i:]


def _descriptors(text: str) -> list[tuple[str, str]] | None:
    """``[(name, suffix)]``: suffix ``/`` namespace, ``#`` type, ``.`` term, ``()`` method,
    ``[]`` type parameter, ``(`` parameter, ``:`` meta, ``!`` macro. None if malformed."""
    out: list[tuple[str, str]] = []
    i = 0
    n = len(text)

    def name_at(j: int) -> tuple[str, int] | None:
        if j < n and text[j] == "`":
            k = j + 1
            chars: list[str] = []
            while k < n:
                if text[k] == "`":
                    if text.startswith("``", k):
                        chars.append("`")
                        k += 2
                        continue
                    return "".join(chars), k + 1
                chars.append(text[k])
                k += 1
            return None
        k = j
        while k < n and (text[k].isalnum() or text[k] in "_+-$"):
            k += 1
        return (text[j:k], k) if k > j else None

    while i < n:
        if text[i] == "[":
            found = name_at(i + 1)
            if found is None or found[1] >= n or text[found[1]] != "]":
                return None
            out.append((found[0], "[]"))
            i = found[1] + 1
            continue
        if text[i] == "(":
            found = name_at(i + 1)
            if found is None or found[1] >= n or text[found[1]] != ")":
                return None
            out.append((found[0], "("))
            i = found[1] + 1
            continue
        found = name_at(i)
        if found is None:
            return None
        name, i = found
        if i >= n:
            return None
        if text[i] == "(":
            close = text.find(").", i)
            if close < 0:
                return None
            out.append((name, "()"))
            i = close + 2
        elif text[i] in "/#.:!":
            out.append((name, text[i]))
            i += 1
        else:
            return None
    return out


def _type_name(text: str) -> str:
    """``Stack<T>`` → ``Stack``; ``&mut Foo`` → ``Foo``; a path's last segment."""
    base = text.split("<", 1)[0].strip()
    for prefix in ("&mut ", "&", "dyn ", "*const ", "*mut "):
        base = base.removeprefix(prefix).strip()
    return base.rsplit("::", 1)[-1]


def rust_path(symbol: str, crates: Mapping[str, str]) -> str | None:
    """The scanner's path of a symbol of one of ``crates`` (SCIP package name → crate name):
    ``crate::module::Item``, an ``impl`` block's items under its type. None for a local, a
    parameter, another package's symbol or one this can't read."""
    split = _split_symbol(symbol)
    if split is None:
        return None
    package, text = split
    crate = crates.get(package)
    if crate is None:
        return None
    parsed = _descriptors(text)
    if not parsed:
        return None
    path = [crate]
    i = 0
    while i < len(parsed):
        name, suffix = parsed[i]
        if suffix == "(" or suffix == ":":
            return None  # a parameter, or meta data
        if name == "impl" and suffix == "#":
            params = []
            while i + 1 < len(parsed) and parsed[i + 1][1] == "[]":
                params.append(parsed[i + 1][0])
                i += 1
            if not params:
                return None
            path.append(_type_name(params[0]))  # [Type][Trait]: the item is the type's
        elif suffix == "[]":
            return None  # a type parameter
        elif name == "crate" and suffix == "/" and len(path) == 1:
            pass  # the crate root
        else:
            path.append(name)
        i += 1
    return "::".join(path) if len(path) > 1 else crate


@dataclass(frozen=True)
class Reference:
    file: str
    path: str  # the item's scanner path
    line: int
    column: int


def references(
    documents: list[Document], crates: Mapping[str, str], prefix: str = ""
) -> tuple[dict[str, tuple[str, int, int]], list[Reference]]:
    """Where each of ``crates``' items is defined (``path → (file, line, column)``) and every
    other occurrence of one, files prefixed with ``prefix`` (the indexed directory)."""
    defined: dict[str, tuple[str, int, int]] = {}
    found: list[Reference] = []
    for document in documents:
        file = f"{prefix}{document.path}"
        for occurrence in document.occurrences:
            path = rust_path(occurrence.symbol, crates)
            if path is None:
                continue
            if occurrence.definition:
                defined.setdefault(path, (file, occurrence.line, occurrence.column))
            else:
                found.append(Reference(file, path, occurrence.line, occurrence.column))
    return defined, found
