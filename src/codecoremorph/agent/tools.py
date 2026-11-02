"""The agent's tools: read-only, plus the terminal ``submit_edits`` (D-19, SPEC §7.6).

Every path an agent names is untrusted model output: it must stay inside the worktree and
out of ``.git``. Results are capped, and a capped result always says so (nothing is
truncated silently). Schemas follow strict-mode rules: closed objects, every property
required, optional values nullable. Descriptions are part of the cached prompt prefix; keep
them stable.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from codecoremorph.core.globs import PathMatcher
from codecoremorph.core.models import ToolSpec
from codecoremorph.core.models.base import check_worktree_path
from codecoremorph.patching.schema import SUBMIT_EDITS_TOOL, submit_edits_schema
from codecoremorph.patching.textfile import BinaryFileError, TextFile
from codecoremorph.store.index import IndexStore

MAX_READ_LINES = 1500
MAX_RESULT_CHARS = 60_000
MAX_SEARCH_MATCHES = 100
MAX_LISTED = 300
_SKIPPED_DIRS = {".git", ".codecoremorph"}

READ_FILE = "read_file"
LIST_DIR = "list_dir"
SEARCH_CODE = "search_code"
FIND_SYMBOL = "find_symbol"
GET_REFERENCES = "get_references"
GET_SIGNATURE = "get_signature"


def _schema(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


_PATH = {"type": "string", "description": "Path relative to the repository root."}
_NULLABLE_INT = {"type": ["integer", "null"]}
_SYMBOL_ID = {"type": "string", "description": "A symbol ID from find_symbol."}

TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name=READ_FILE,
        description="Read a file of the repository, optionally a line range (1-based, "
        "inclusive). Use it for files outside the unit; the unit's own files are in the "
        "first message.",
        input_schema=_schema(
            {
                "path": _PATH,
                "start_line": _NULLABLE_INT | {"description": "First line, or null."},
                "end_line": _NULLABLE_INT | {"description": "Last line, or null."},
            }
        ),
    ),
    ToolSpec(
        name=LIST_DIR,
        description="List a directory of the repository. Directories end with '/'.",
        input_schema=_schema({"path": _PATH | {"description": "Directory; '.' is the root."}}),
    ),
    ToolSpec(
        name=SEARCH_CODE,
        description="Search the repository's files with a regular expression (Python "
        "syntax). Returns path:line: text for each match.",
        input_schema=_schema(
            {
                "regex": {"type": "string"},
                "glob": {
                    "type": ["string", "null"],
                    "description": "Only files matching this glob, e.g. '*.h' or 'src/**', "
                    "or null for every file.",
                },
            }
        ),
    ),
    ToolSpec(
        name=FIND_SYMBOL,
        description="Find declarations and definitions of a symbol by name or qualified "
        "name, from the project's symbol index.",
        input_schema=_schema({"name": {"type": "string"}}),
    ),
    ToolSpec(
        name=GET_REFERENCES,
        description="List the places that reference a symbol.",
        input_schema=_schema({"symbol_id": _SYMBOL_ID}),
    ),
    ToolSpec(
        name=GET_SIGNATURE,
        description="Show a symbol's declarations with their signatures and locations.",
        input_schema=_schema({"symbol_id": _SYMBOL_ID}),
    ),
    ToolSpec(
        name=SUBMIT_EDITS_TOOL,
        description="Submit every edit for the work unit, then stop. The edits are applied "
        "all or nothing; the project is then built and tested.",
        input_schema=submit_edits_schema(),
    ),
)


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReadFileArgs(_Args):
    path: str
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)


class ListDirArgs(_Args):
    path: str


class SearchArgs(_Args):
    regex: str
    glob: str | None = None


class FindSymbolArgs(_Args):
    name: str = Field(min_length=1)


class SymbolArgs(_Args):
    symbol_id: str = Field(min_length=1)


_ARGS: dict[str, type[_Args]] = {
    READ_FILE: ReadFileArgs,
    LIST_DIR: ListDirArgs,
    SEARCH_CODE: SearchArgs,
    FIND_SYMBOL: FindSymbolArgs,
    GET_REFERENCES: SymbolArgs,
    GET_SIGNATURE: SymbolArgs,
}


class ToolInputError(ValueError):
    """The model's arguments don't validate; reported back to it as an error result."""


@dataclass(frozen=True)
class ToolOutput:
    content: str
    is_error: bool = False


def _cap(text: str, what: str) -> str:
    if len(text) <= MAX_RESULT_CHARS:
        return text
    return text[:MAX_RESULT_CHARS] + f"\n[{what} cut at {MAX_RESULT_CHARS} characters]"


@dataclass
class ToolBox:
    """Runs the read-only tools against the run worktree and its symbol index."""

    worktree: Path
    index_path: Path | None = None
    calls: int = 0
    _root: Path = field(init=False)

    def __post_init__(self) -> None:
        self._root = self.worktree.resolve()

    @property
    def specs(self) -> list[ToolSpec]:
        return list(TOOL_SPECS)

    def run(self, name: str, arguments: dict[str, Any]) -> ToolOutput:
        self.calls += 1
        model = _ARGS.get(name)
        if model is None:
            return ToolOutput(f"Unknown tool {name!r}.", is_error=True)
        try:
            args = model.model_validate(arguments)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or 'input'}: {e['msg']}" for e in exc.errors()
            )
            return ToolOutput(f"Invalid arguments for {name}: {problems}", is_error=True)
        try:
            if isinstance(args, ReadFileArgs):
                return ToolOutput(self.read_file(args))
            if isinstance(args, ListDirArgs):
                return ToolOutput(self.list_dir(args.path))
            if isinstance(args, SearchArgs):
                return ToolOutput(self.search(args.regex, args.glob))
            if isinstance(args, FindSymbolArgs):
                return ToolOutput(self.find_symbol(args.name))
            if isinstance(args, SymbolArgs) and name == GET_REFERENCES:
                return ToolOutput(self.references(args.symbol_id))
            if isinstance(args, SymbolArgs):
                return ToolOutput(self.signature(args.symbol_id))
        except ToolInputError as exc:
            return ToolOutput(str(exc), is_error=True)
        return ToolOutput(f"Unknown tool {name!r}.", is_error=True)

    # ------------------------------------------------------------------ files

    def _resolve(self, path: str) -> tuple[str, Path]:
        rel = path.replace("\\", "/").strip()
        rel = rel[2:] if rel.startswith("./") else rel
        if rel in ("", "."):
            return ".", self._root
        try:
            check_worktree_path(rel.rstrip("/"))
        except ValueError as exc:
            raise ToolInputError(f"{path!r} is not a repository path: {exc}") from None
        host = (self._root / rel).resolve()
        if not host.is_relative_to(self._root):
            raise ToolInputError(f"{path!r} is outside the repository")
        return rel.rstrip("/"), host

    def read_file(self, args: ReadFileArgs) -> str:
        rel, host = self._resolve(args.path)
        if not host.is_file():
            raise ToolInputError(f"{rel} is not a file")
        try:
            text = TextFile.decode(host.read_bytes()).text.replace("\r\n", "\n")
        except BinaryFileError:
            raise ToolInputError(f"{rel} is a binary file") from None
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        total = len(lines)
        start = args.start_line or 1
        end = min(args.end_line or total, total, start + MAX_READ_LINES - 1)
        if start > max(total, 1):
            raise ToolInputError(f"{rel} has {total} lines; line {start} doesn't exist")
        body = "\n".join(lines[start - 1 : end])
        header = f"{rel} (lines {start}-{end} of {total})"
        more = ""
        if end < (args.end_line or total):
            more = f"\n[stopped at line {end}; read from line {end + 1} for more]"
        return _cap(f"{header}\n{body}{more}", rel)

    def list_dir(self, path: str) -> str:
        rel, host = self._resolve(path)
        if not host.is_dir():
            raise ToolInputError(f"{rel} is not a directory")
        entries = sorted(
            (p.name + "/" if p.is_dir() else p.name)
            for p in host.iterdir()
            if p.name not in _SKIPPED_DIRS
        )
        shown = entries[:MAX_LISTED]
        tail = f"\n[{len(entries) - MAX_LISTED} more entries]" if len(entries) > MAX_LISTED else ""
        return f"{rel}/\n" + "\n".join(shown) + tail

    def _walk(self, glob: str | None) -> Iterable[tuple[str, Path]]:
        matcher = PathMatcher([glob]) if glob else None
        for root, dirs, files in os.walk(self._root):
            dirs[:] = sorted(d for d in dirs if d not in _SKIPPED_DIRS)
            base = Path(root)
            for name in sorted(files):
                host = base / name
                rel = host.relative_to(self._root).as_posix()
                if matcher is None or matcher.matches(rel):
                    yield rel, host

    def search(self, regex: str, glob: str | None) -> str:
        try:
            pattern = re.compile(regex)
        except re.error as exc:
            raise ToolInputError(f"invalid regular expression: {exc}") from None
        matches: list[str] = []
        for rel, host in self._walk(glob):
            try:
                text = TextFile.decode(host.read_bytes()).text
            except (BinaryFileError, OSError):
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if pattern.search(line):
                    matches.append(f"{rel}:{number}: {line.strip()[:200]}")
                    if len(matches) > MAX_SEARCH_MATCHES:
                        break
            if len(matches) > MAX_SEARCH_MATCHES:
                break
        if not matches:
            return "No matches."
        if len(matches) > MAX_SEARCH_MATCHES:
            return "\n".join(matches[:MAX_SEARCH_MATCHES]) + (
                f"\n[more than {MAX_SEARCH_MATCHES} matches; narrow the search]"
            )
        return "\n".join(matches)

    # ------------------------------------------------------------------ symbols

    def _index(self) -> IndexStore:
        if self.index_path is None or not self.index_path.is_file():
            raise ToolInputError("the symbol index isn't available; use search_code instead")
        return IndexStore.open(self.index_path)

    def find_symbol(self, name: str) -> str:
        short = name.rsplit("::", 1)[-1]
        with self._index() as store:
            found = [
                s
                for s in store.symbols(name=short)
                if s.qualified_name == name or s.name == name or s.qualified_name.endswith(name)
            ]
        if not found:
            return f"No symbol named {name!r} in the index."
        lines = [
            f"{s.id}  {s.kind.value} {s.qualified_name}  {s.file}:{s.range.start_line}"
            f"{' (definition)' if s.is_definition else ''}"
            for s in found[:50]
        ]
        if len(found) > 50:
            lines.append(f"[{len(found) - 50} more]")
        return "\n".join(lines)

    def references(self, symbol_id: str) -> str:
        with self._index() as store:
            refs = store.references_to(symbol_id)
        if not refs:
            return f"No references to {symbol_id} in the index."
        lines = [f"{r.from_file}:{r.line}:{r.column} {r.kind.value}" for r in refs[:200]]
        if len(refs) > 200:
            lines.append(f"[{len(refs) - 200} more]")
        return "\n".join(lines)

    def signature(self, symbol_id: str) -> str:
        with self._index() as store:
            symbols = store.symbols(symbol_id=symbol_id)
        if not symbols:
            return f"No symbol {symbol_id} in the index."
        return "\n".join(
            f"{s.file}:{s.range.start_line}: {s.signature or s.qualified_name}"
            f"{' (definition)' if s.is_definition else ''}"
            for s in symbols
        )
