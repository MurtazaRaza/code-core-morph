"""The normalized symbol index adapters produce (SPEC §7.2).

The index lives in SQLite (``index.sqlite`` in the run cache) rather than in memory, so
:class:`SymbolIndex` is a handle to that file plus its metadata. These models describe the
rows; ``codecoremorph.store.index`` reads and writes them.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import Field

from codecoremorph.core.models.base import CoreModel, WorktreePath


class FileRole(StrEnum):
    SOURCE = "source"
    HEADER = "header"
    TEST = "test"
    BUILD = "build"
    GENERATED = "generated"
    THIRD_PARTY = "third_party"


class LineEnding(StrEnum):
    LF = "lf"
    CRLF = "crlf"
    MIXED = "mixed"
    NONE = "none"  # no line break in the file


class FileEntry(CoreModel):
    path: WorktreePath
    language: str = Field(pattern=r"^[a-z][a-z0-9_]*$", description='e.g. "cpp" or "c"')
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    role: FileRole
    c_compatible: bool = Field(
        default=False, description="Included from a C translation unit (FR-2.3)"
    )
    public_api: bool = Field(default=False, description="Matched by project.public_api")
    encoding: str = "utf-8"
    line_ending: LineEnding = LineEnding.LF


class SymbolKind(StrEnum):
    NAMESPACE = "namespace"
    CLASS = "class"
    STRUCT = "struct"
    UNION = "union"
    ENUM = "enum"
    ENUM_CONSTANT = "enum_constant"
    FUNCTION = "function"
    METHOD = "method"
    CONSTRUCTOR = "constructor"
    DESTRUCTOR = "destructor"
    FIELD = "field"
    VARIABLE = "variable"
    TYPEDEF = "typedef"
    ALIAS = "alias"
    CLASS_TEMPLATE = "class_template"
    FUNCTION_TEMPLATE = "function_template"
    ALIAS_TEMPLATE = "alias_template"
    CONCEPT = "concept"
    MACRO = "macro"


class Visibility(StrEnum):
    PUBLIC = "public"
    PROTECTED = "protected"
    PRIVATE = "private"
    NONE = "none"  # not a class member


class Boundary(StrEnum):
    """Why a symbol sits on a language, ABI or API boundary (FR-2.2)."""

    NONE = "none"
    C_ABI = "c_abi"
    JNI = "jni"
    PINVOKE = "pinvoke"
    PUBLIC_API = "public_api"


class SourceRange(CoreModel):
    start_line: int = Field(ge=1)
    start_column: int = Field(ge=1)
    end_line: int = Field(ge=1)
    end_column: int = Field(ge=1)


class Symbol(CoreModel):
    id: str = Field(min_length=1, description="Adapter-stable ID, e.g. a clang USR")
    name: str
    qualified_name: str
    kind: SymbolKind
    file: WorktreePath
    range: SourceRange
    signature: str | None = None
    visibility: Visibility = Visibility.NONE
    is_definition: bool = False
    boundary: Boundary = Boundary.NONE


class ReferenceKind(StrEnum):
    CALL = "call"
    TYPE_USE = "type_use"
    INHERIT = "inherit"
    OVERRIDE = "override"
    MACRO_EXPAND = "macro_expand"
    USE = "use"  # any other use of a variable, field, function or constant


class Reference(CoreModel):
    from_file: WorktreePath
    from_symbol: str | None = Field(default=None, description="Innermost enclosing symbol")
    to_symbol: str
    kind: ReferenceKind
    line: int = Field(ge=1)
    column: int = Field(ge=1)


class DependencyKind(StrEnum):
    INCLUDE = "include"
    IMPORT = "import"
    MODULE = "module"


class FileDependency(CoreModel):
    from_file: WorktreePath
    to_file: WorktreePath
    kind: DependencyKind = DependencyKind.INCLUDE


class IndexMeta(CoreModel):
    adapter: str
    adapter_version: str
    tool_versions: dict[str, str] = Field(default_factory=dict[str, str])
    stats: dict[str, int] = Field(default_factory=dict[str, int])
    timings: dict[str, float] = Field(default_factory=dict[str, float])
    uncompiled: list[str] = Field(
        default_factory=list[str],
        description="Code files outside the excluded paths that no compile command reaches "
        "(another platform's sources, disabled targets): builds and tests can't check them",
    )


class SymbolIndex(CoreModel):
    """A finished index: open ``path`` with ``codecoremorph.store.index.IndexStore``."""

    path: Path
    meta: IndexMeta
    problems: list[str] = Field(
        default_factory=list[str],
        description="Translation units that failed to parse, and similar warnings",
    )
