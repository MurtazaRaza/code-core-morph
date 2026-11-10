"""The normalized symbol index adapters produce (SPEC §7.2).

The index lives in SQLite (``index.sqlite`` in the run cache) rather than in memory, so
:class:`SymbolIndex` is a handle to that file plus its metadata. These models describe the
rows; ``codecoremorph.store.index`` reads and writes them.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import ClassVar

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
    INTERFACE = "interface"  # Java (D-63)
    RECORD = "record"
    ANNOTATION = "annotation"
    STRUCT = "struct"
    UNION = "union"
    ENUM = "enum"
    ENUM_CONSTANT = "enum_constant"
    FUNCTION = "function"
    METHOD = "method"
    CONSTRUCTOR = "constructor"
    DESTRUCTOR = "destructor"
    FIELD = "field"
    PROPERTY = "property"  # C# (D-65)
    EVENT = "event"
    DELEGATE = "delegate"
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


class TargetKind(StrEnum):
    LIBRARY = "library"
    EXECUTABLE = "executable"
    TEST = "test"  # a project only for tests (a C# test project): no port cuts types from it
    OTHER = "other"  # custom targets and the like


class BuildTarget(CoreModel):
    """A target of the project's build (a CMake library or executable) and the files it
    compiles: a port makes one crate per library target and leaves executables to the end
    (D-58)."""

    name: str = Field(min_length=1)
    kind: TargetKind
    files: list[WorktreePath] = Field(default_factory=list[str])
    dependencies: list[str] = Field(
        default_factory=list[str],
        description="The project's targets it depends on (links), by name (D-60)",
    )
    checked_arithmetic: bool = Field(
        default=False,
        description="A C# project's CheckForOverflowUnderflow: its integer arithmetic throws "
        "on overflow outside `unchecked`, which a port keeps (D-82)",
    )
    artifacts: list[str] = Field(
        default_factory=list[str],
        description="What it builds, relative to the build directory (D-60)",
    )


class TestEntry(CoreModel):
    """A test the build registers (a CTest entry), and what it runs: a port gives an entry
    that runs a program to that program's unit (D-60)."""

    __test__: ClassVar[bool] = False  # not a pytest test class

    test_id: str = Field(min_length=1, description='The ID test reports use, "ctest:<name>"')
    runs: str | None = Field(
        default=None, description="The project's build target its command runs, if any"
    )
    file: WorktreePath | None = Field(default=None, description="The build file that adds it")


class PortRefusal(StrEnum):
    """What the first version of a port from C# refuses in a type (D-78)."""

    ASYNC = "async"
    DYNAMIC = "dynamic"
    REFLECTION = "reflection"
    # Serializers, dependency injection and the attributes that drive them.
    SERIALIZATION = "serialization"
    UNSAFE = "unsafe"
    EXPRESSION_TREE = "expression_tree"


class RefusalSite(CoreModel):
    """Where a type uses what a port refuses (D-78)."""

    what: PortRefusal
    detail: str = Field(description='What it is: "async Load", "System.Activator.CreateInstance"')
    file: WorktreePath
    line: int = Field(ge=1)
    member: str | None = Field(default=None, description="The member it is in, by symbol ID")


class PortNoticeKind(StrEnum):
    """What a port from C# takes over without refusing it, which a unit's note names (D-82)."""

    # Pointers, `fixed`, `stackalloc`, function pointers: plain pointers in the new code.
    UNSAFE = "unsafe"
    # A serializer's round trip in memory (BinaryFormatter into a MemoryStream and back): a
    # copy of the object graph.
    DEEP_COPY = "deep_copy"
    # Code that asks which platform it runs on, or loads native code itself.
    PLATFORM = "platform"


class PortNotice(CoreModel):
    """A site a port takes over, with what its unit's note says of it (D-82)."""

    what: PortNoticeKind
    detail: str = Field(description='What it is: "a fixed statement", "a deep copy of Shop.Cart"')
    file: WorktreePath
    line: int = Field(ge=1)
    member: str | None = Field(default=None, description="The member it is in, by symbol ID")


class NativeImport(CoreModel):
    """A P/Invoke declaration of a port's source (D-82): the new code calls the native function
    itself."""

    library: str = Field(description='As the declaration names it: "libsodium", "kernel32"')
    entry_point: str = Field(min_length=1)
    member: str = Field(min_length=1, description="The extern method, by symbol ID")
    signature: str = ""
    file: WorktreePath
    line: int = Field(ge=1)


class NativeLibraryProbe(CoreModel):
    """What the toolchain has of a native library a port's source calls through P/Invoke
    (D-82): the new code includes its C header and links it, so both must be there."""

    library: str = Field(description='As the P/Invoke names it: "libsodium"')
    header: str | None = Field(default=None, description='"/usr/include/sodium.h", or none found')
    link: str | None = Field(
        default=None, description='The library to link: "/usr/lib/x86_64-linux-gnu/libsodium.so"'
    )
    version: str | None = Field(default=None, description="Its pkg-config version, when known")


class PackageUse(CoreModel):
    """A package's API that a type of a port's source uses (D-82)."""

    package: str = Field(min_length=1)
    symbol: str = Field(min_length=1, description='"Newtonsoft.Json.JsonConvert.SerializeObject"')
    file: WorktreePath
    line: int = Field(ge=1)
    member: str | None = None


class PackageEntry(CoreModel):
    """A package a project of a port's source restored (D-82), from ``project.assets.json``."""

    project: WorktreePath
    name: str = Field(min_length=1)
    version: str = ""
    assemblies: list[str] = Field(
        default_factory=list[str], description="The managed assemblies it brings, by name"
    )
    native: list[str] = Field(
        default_factory=list[str],
        description='Its native files by runtime: "linux-x64:libsodium.so"',
    )


class ThrowSite(CoreModel):
    """An exception a member throws (D-78): the port's C++ throws its mirror, and the façade
    throws it again."""

    exception: str = Field(min_length=1, description='Its type: "System.ArgumentNullException"')
    file: WorktreePath
    line: int = Field(ge=1)
    member: str | None = Field(default=None, description="The member that throws it, by ID")


class GenericUse(CoreModel):
    """A use of a generic type or method of the project with its type arguments (D-78): each
    instantiation in use is what a port exports, as Roslyn's semantic model tells them."""

    symbol: str = Field(min_length=1, description="The generic type or method, by symbol ID")
    arguments: list[str] = Field(default_factory=list[str], description='"int", "Shop.Money"')
    open: bool = Field(
        default=False,
        description="An argument is a type parameter of the code that uses it: the code's own "
        "instantiations decide",
    )
    file: WorktreePath
    line: int = Field(ge=1)
    member: str | None = Field(default=None, description="The member that uses it, by ID")


class TypeEntry(CoreModel):
    """A type a port's source declares (D-78): what the planner cuts its type units by, and
    what their notes say."""

    id: str = Field(min_length=1, description='Its symbol ID: "csharp:Shop.Cart"')
    name: str = Field(min_length=1, description='Qualified, without type parameters: "Shop.Cart"')
    kind: SymbolKind
    files: list[WorktreePath] = Field(
        default_factory=list[WorktreePath], description="Where it is declared (a partial type's)"
    )
    project: WorktreePath | None = Field(default=None, description="The project that compiles it")
    outer: str | None = Field(default=None, description="The type it is nested in, by ID")
    base: str | None = Field(default=None, description="Its base class, when the project's")
    interfaces: list[str] = Field(
        default_factory=list[str], description="The project's interfaces it implements, by ID"
    )
    external_bases: list[str] = Field(
        default_factory=list[str],
        description="Its other base class and interfaces: System.Exception, IEquatable<Money>",
    )
    abstract: bool = False
    sealed: bool = False
    static: bool = False
    type_parameters: list[str] = Field(default_factory=list[str])
    members: list[str] = Field(
        default_factory=list[str], description="Its public and protected members, by ID"
    )
    virtual_members: list[str] = Field(
        default_factory=list[str],
        description="The members a derived class can override (virtual, abstract, or an "
        "override that isn't sealed), by ID",
    )
    instance_fields: list[str] = Field(
        default_factory=list[str],
        description="Its non-private instance fields, by name: a class's can't be forwarded",
    )
    static_state: list[str] = Field(
        default_factory=list[str],
        description="Its static fields that aren't constants (nor readonly of an immutable "
        "type), and static properties and events with storage, by ID",
    )
    refusals: list[RefusalSite] = Field(default_factory=list[RefusalSite])
    throws: list[ThrowSite] = Field(default_factory=list[ThrowSite])
    native_only: bool = Field(
        default=False,
        description="It only declares native functions (with their constants and structs): the "
        "new code calls the native library itself, so it has no unit (D-82)",
    )
    notices: list[PortNotice] = Field(default_factory=list[PortNotice])
    native_imports: list[NativeImport] = Field(default_factory=list[NativeImport])
    package_uses: list[PackageUse] = Field(default_factory=list[PackageUse])


class TestMethod(CoreModel):
    """A test method of a C# test project and the IDs of its cases (D-78). A case's ID is the
    one the TRX report gives; ``exact`` is false when the source can't tell them all (a data
    source, a combinatorial attribute, an argument the framework formats its own way): the
    baseline's report has them."""

    __test__: ClassVar[bool] = False  # not a pytest test class

    file: WorktreePath
    class_name: str = Field(min_length=1, description='As the TRX spells it: "Ns.Outer+Inner"')
    method: str = Field(min_length=1)
    framework: str = Field(pattern=r"^(nunit|xunit|mstest)$")
    project: WorktreePath | None = None
    cases: list[str] = Field(default_factory=list[str], description='"dotnet:Ns.C#M(1,\\"x\\")"')
    exact: bool = True


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
