"""The Rust symbol index (D-58): item paths from a crate's sources, without building it.

A scanner, not a parser: it masks comments and literals, maps each file to its module path
(``src/lib.rs`` is the crate root, ``src/a.rs`` and ``src/a/mod.rs`` are ``crate::a``) and
walks the items: functions, structs, enums and their variants, unions, traits and their items,
type aliases, constants, statics, ``macro_rules!``, inline modules, and the functions and
constants of ``impl`` blocks, which belong to the implementing type
(``impl Add for Rational { fn add }`` gives ``calc_rs::rational::Rational::add``). Function
bodies are skipped. That is what the symbol map and a unit's context need: where each item is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from codecoremorph.core.models import SymbolKind

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CHAR = re.compile(r"b?'(?:[^'\\\n]|\\(?:[nrt\\0'\"]|x[0-9a-fA-F]{2}|u\{[0-9a-fA-F]{1,6}\}))'")
_RAW_STRING = re.compile(r'b?r(#*)"')
_SKIPPED_DIRS = ("bin", "tests", "examples", "benches")


@dataclass(frozen=True)
class RustItem:
    path: str  # crate::module::Item
    kind: SymbolKind
    file: str  # worktree-relative
    line: int
    column: int
    public: bool
    c_abi: bool = False  # #[no_mangle] or #[export_name]: a C symbol


def mask(text: str) -> str:
    """``text`` with comments and string and character literals blanked, offsets kept."""
    out = list(text)
    i, n = 0, len(text)

    def blank(start: int, end: int) -> None:
        for k in range(start, min(end, n)):
            if out[k] not in "\r\n":
                out[k] = " "

    while i < n:
        c = text[i]
        if text.startswith("//", i):
            end = text.find("\n", i)
            end = n if end < 0 else end
            blank(i, end)
            i = end
        elif text.startswith("/*", i):
            depth, j = 1, i + 2  # Rust block comments nest
            while j < n and depth:
                if text.startswith("/*", j):
                    depth, j = depth + 1, j + 2
                elif text.startswith("*/", j):
                    depth, j = depth - 1, j + 2
                else:
                    j += 1
            blank(i, j)
            i = j
        elif (raw := _RAW_STRING.match(text, i)) and (i == 0 or not _word(text[i - 1])):
            close = '"' + raw.group(1)
            end = text.find(close, raw.end())
            end = n if end < 0 else end + len(close)
            blank(i, end)
            i = end
        elif c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            blank(i, j + 1)
            i = j + 1
        elif c in "'b" and (char := _CHAR.match(text, i)) and (i == 0 or not _word(text[i - 1])):
            blank(i, char.end())
            i = char.end()
        else:
            i += 1
    return "".join(out)


def _word(c: str) -> bool:
    return c.isalnum() or c == "_"


@dataclass(frozen=True)
class _Token:
    text: str
    line: int
    column: int


def _tokens(masked: str) -> list[_Token]:
    tokens: list[_Token] = []
    line, line_start = 1, 0
    i, n = 0, len(masked)
    while i < n:
        c = masked[i]
        if c == "\n":
            line, line_start = line + 1, i + 1
            i += 1
        elif c.isspace():
            i += 1
        elif (ident := _IDENT.match(masked, i)) is not None:
            tokens.append(_Token(ident.group(), line, i - line_start + 1))
            i = ident.end()
        elif c.isdigit():
            j = i
            while j < n and (masked[j].isalnum() or masked[j] in "._"):
                j += 1
            tokens.append(_Token(masked[i:j], line, i - line_start + 1))
            i = j
        elif masked.startswith("::", i):
            tokens.append(_Token("::", line, i - line_start + 1))
            i += 2
        elif c == "'" and (ident := _IDENT.match(masked, i + 1)) is not None:
            tokens.append(_Token("'" + ident.group(), line, i - line_start + 1))  # a lifetime
            i = ident.end()
        else:
            tokens.append(_Token(c, line, i - line_start + 1))
            i += 1
    return tokens


def module_path(crate: str, rel_to_crate: str) -> str | None:
    """The module a file of the crate is, or None for files outside the library
    (``src/main.rs``, ``src/bin``, ``tests``, ``build.rs``)."""
    parts = PurePosixPath(rel_to_crate).parts
    if len(parts) < 2 or parts[0] != "src" or parts[1] in _SKIPPED_DIRS:
        return None
    inner = list(parts[1:])
    name = inner[-1]
    if not name.endswith(".rs") or name == "main.rs":
        return None
    if inner == ["lib.rs"]:
        return crate
    if name == "mod.rs":
        inner = inner[:-1]
    else:
        inner[-1] = name.removesuffix(".rs")
    return "::".join([crate, *inner])


_OPEN = {"{": "}", "(": ")", "[": "]"}


class _Scanner:
    def __init__(self, tokens: list[_Token], file: str):
        self.t = tokens
        self.file = file
        self.items: list[RustItem] = []
        self.impls: list[tuple[str, str, RustItem]] = []  # (impl module, type, item)

    def at(self, i: int) -> str:
        return self.t[i].text if i < len(self.t) else ""

    def skip_group(self, i: int) -> int:
        """From an opening bracket at ``i``, the index after its match."""
        close = _OPEN[self.at(i)]
        opener = self.at(i)
        depth = 0
        while i < len(self.t):
            text = self.t[i].text
            if text == opener:
                depth += 1
            elif text == close:
                depth -= 1
                if depth == 0:
                    return i + 1
            i += 1
        return i

    def skip_to_end(self, i: int) -> int:
        """Past the item that starts at ``i``: its ``;`` or its braced body, whichever comes
        first outside brackets (generics' ``<>`` hold no braces or semicolons)."""
        while i < len(self.t):
            text = self.at(i)
            if text == ";":
                return i + 1
            if text == "{":
                return self.skip_group(i)
            if text in ("(", "["):
                i = self.skip_group(i)
                continue
            i += 1
        return i

    def add(
        self, path: str, kind: SymbolKind, token: _Token, public: bool, c_abi: bool = False
    ) -> RustItem:
        item = RustItem(path, kind, self.file, token.line, token.column, public, c_abi)
        self.items.append(item)
        return item

    def items_until_close(self, i: int, prefix: str, scope: str, owner: str = "") -> int:
        """Items from ``i`` to the ``}`` that closes the scope (or the end of the file).
        ``scope`` is ``module``, ``impl`` or ``trait``; ``owner`` is the impl's type."""
        attrs: list[str] = []
        while i < len(self.t):
            text = self.at(i)
            if text == "}":
                return i + 1
            if text == "#":
                j = i + 1 + (self.at(i + 1) == "!")
                if self.at(j) == "[":
                    end = self.skip_group(j)
                    attrs.append(" ".join(tok.text for tok in self.t[j:end]))
                    i = end
                    continue
            public = False
            if text == "pub":
                public = True
                i += 1
                if self.at(i) == "(":
                    i = self.skip_group(i)
                text = self.at(i)
            while text in ("unsafe", "async", "default", "extern") or (
                text == "const" and self.at(i + 1) in ("fn", "unsafe", "async", "extern")
            ):
                i += 1
                text = self.at(i)
            c_abi = any("no_mangle" in a or "export_name" in a for a in attrs)
            attrs = []
            name_token = self.t[i + 1] if i + 1 < len(self.t) else None
            name = self.at(i + 1)
            if text == "fn" and name_token is not None:
                kind = SymbolKind.FUNCTION if scope == "module" else SymbolKind.METHOD
                item = self.add(f"{prefix}::{name}", kind, name_token, public, c_abi)
                if scope == "impl":
                    self.impls.append((prefix, owner, item))
                i = self.skip_to_end(i + 2)
            elif text in ("struct", "union") and name_token is not None:
                kind = SymbolKind.STRUCT if text == "struct" else SymbolKind.UNION
                self.add(f"{prefix}::{name}", kind, name_token, public)
                i = self.skip_to_end(i + 2)
            elif text == "enum" and name_token is not None:
                self.add(f"{prefix}::{name}", SymbolKind.ENUM, name_token, public)
                i = self.variants(i + 2, f"{prefix}::{name}", public)
            elif text == "trait" and name_token is not None:
                self.add(f"{prefix}::{name}", SymbolKind.CLASS, name_token, public)
                j = i + 2
                while j < len(self.t) and self.at(j) not in ("{", ";"):
                    j += 1
                i = (
                    self.items_until_close(j + 1, f"{prefix}::{name}", "trait")
                    if (self.at(j) == "{")
                    else j + 1
                )
            elif text == "impl":
                i = self.impl_block(i + 1, prefix)
            elif text == "mod" and name_token is not None:
                if self.at(i + 2) == "{":
                    self.add(f"{prefix}::{name}", SymbolKind.NAMESPACE, name_token, public)
                    i = self.items_until_close(i + 3, f"{prefix}::{name}", "module")
                else:
                    i = self.skip_to_end(i + 2)
            elif text in ("type", "const", "static") and name_token is not None:
                if name == "mut" and text == "static":
                    name_token = self.t[i + 2] if i + 2 < len(self.t) else name_token
                    name = name_token.text
                kind = SymbolKind.ALIAS if text == "type" else SymbolKind.VARIABLE
                if _IDENT.fullmatch(name) and name != "_":
                    item = self.add(f"{prefix}::{name}", kind, name_token, public)
                    if scope == "impl":
                        self.impls.append((prefix, owner, item))
                i = self.skip_to_end(i + 2)
            elif text == "macro_rules" and self.at(i + 1) == "!":
                token = self.t[i + 2] if i + 2 < len(self.t) else None
                if token is not None:
                    self.add(f"{prefix}::{token.text}", SymbolKind.MACRO, token, public)
                i = self.skip_to_end(i + 3)
            elif text in ("use", "let"):
                i = self.skip_to_end(i + 1)
            elif text in _OPEN:
                i = self.skip_group(i)
            else:
                i += 1
        return i

    def variants(self, i: int, enum: str, public: bool) -> int:
        while i < len(self.t) and self.at(i) != "{":
            i += 1
        i += 1
        expect = True
        while i < len(self.t):
            text = self.at(i)
            if text == "}":
                return i + 1
            if text == "#" and self.at(i + 1) == "[":
                i = self.skip_group(i + 1)
                continue
            if text in _OPEN:
                i = self.skip_group(i)
                continue
            if text == ",":
                expect = True
            elif expect and _IDENT.fullmatch(text):
                self.add(f"{enum}::{text}", SymbolKind.ENUM_CONSTANT, self.t[i], public)
                expect = False
            i += 1
        return i

    def impl_block(self, i: int, prefix: str) -> int:
        header: list[str] = []
        angle = 0
        while i < len(self.t) and not (self.at(i) == "{" and angle == 0):
            text = self.at(i)
            if text == ";":
                return i + 1
            if text == "<":
                angle += 1
            elif text == ">" and angle:
                angle -= 1
            elif angle == 0:
                header.append(text)
            i += 1
        if "for" in header:
            header = header[header.index("for") + 1 :]
        if "where" in header:
            header = header[: header.index("where")]
        names = [t for t in header if _IDENT.fullmatch(t) and t not in ("dyn", "mut")]
        owner = names[-1] if names else "_"
        return self.items_until_close(i + 1, f"{prefix}::{owner}", "impl", owner)


def scan_file(crate: str, rel_to_crate: str, worktree_rel: str, text: str) -> list[RustItem]:
    """The items of one file of crate ``crate``; methods are under the impl's module, for
    :func:`scan_crate` to move to their type."""
    module = module_path(crate, rel_to_crate)
    if module is None:
        return []
    scanner = _Scanner(_tokens(mask(text)), worktree_rel)
    scanner.items_until_close(0, module, "module")
    return scanner.items


def scan_crate(crate: str, files: dict[str, tuple[str, str]]) -> list[RustItem]:
    """Every item of crate ``crate``: ``files`` maps each source's path in the crate
    (``src/a.rs``) to its worktree path and text. A method of ``impl T`` in another module
    than ``T`` moves to ``T``'s module when one type of the crate has that name."""
    items: list[RustItem] = []
    for rel, (worktree_rel, text) in sorted(files.items()):
        items += scan_file(crate, rel, worktree_rel, text)
    types: dict[str, list[str]] = {}
    for item in items:
        if item.kind in (SymbolKind.STRUCT, SymbolKind.ENUM, SymbolKind.UNION, SymbolKind.CLASS):
            types.setdefault(item.path.rsplit("::", 1)[-1], []).append(item.path)
    moved: list[RustItem] = []
    for item in items:
        if item.kind is SymbolKind.METHOD and item.path.count("::") >= 2:
            *module, owner, name = item.path.split("::")
            defined = types.get(owner, [])
            here = "::".join([*module, owner])
            if len(defined) == 1 and defined[0] != here and here not in defined:
                item = RustItem(
                    f"{defined[0]}::{name}", item.kind, item.file, item.line, item.column,
                    item.public, item.c_abi,
                )  # fmt: skip
        moved.append(item)
    return moved
