"""The Java side of a Java façade's bridge (SPEC §11.12, D-92): how each export of the bridge's C
ABI crosses from Java, read from what the C++ index helper (``--bridge-declarations``) read in the
bridge headers, and the Java declarations of the exports, ``codecoremorph.interop.CcmNative``.

The bridge is D-78's C ABI, whatever the target. Java can't call a C function without JNI, so
each export gets a ``native`` method of ``CcmNative`` and a JNI function of that method's name,
which converts the arguments and calls the export: written in C for a C++ target
(:mod:`codecoremorph.adapters.cpp.jniglue`) and in Rust for a Rust one
(:mod:`codecoremorph.adapters.rust.jniglue`), both from the :class:`Bridge` read here, so the
Java is the same for both but the library's name.

How a C parameter crosses (the bridge's conventions, D-78):

- a number or ``bool`` as the Java primitive of its width (unsigned ones as the signed);
- ``const char *`` as UTF-8 bytes, which the wrapper encodes from a ``String``: real UTF-8, never
  JNI's modified UTF-8;
- ``char **`` (a string out) as a ``byte[][]`` the glue fills, which the wrapper decodes into a
  ``String[]``;
- a handle as a ``long``, and ``X **`` or ``void **`` out as a ``long[]``;
- a non-const pointer to a number as an array of one element;
- ``void *`` as an ``Object`` the glue holds by a JNI global reference: native code's to release
  when the export also takes a ``ccm_release_fn``, deleted after the call otherwise;
- ``ccm_release_fn`` and function pointers not at all: the glue passes its own release and a
  trampoline, which calls the static ``CcmNative.<type>_call`` with the context object;
- ``const T *<p>_data`` beside ``int32_t <p>_count`` as a Java array (null: a count of -1),
  ``const char **`` as a ``String[]``, and ``T **<p>_data`` beside ``int32_t *<p>_count`` (an
  array out) as a ``T[][]`` the glue fills.

The generated Java is Java 8 and ASCII.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, cast

PACKAGE = "codecoremorph.interop"
CLASS = "CcmNative"
# Where the generated file goes, under a source root (src/main/java).
GENERATED_FILE = "codecoremorph/interop/CcmNative.java"
JNI_CLASS = f"{PACKAGE.replace('.', '/')}/{CLASS}"
# What the glue's names are prefixed with, by JNI's rules (`_` is `_1` in a method's name).
JNI_PREFIX = f"Java_{JNI_CLASS.replace('/', '_')}_"

_JAVA_WORDS = (
    "abstract assert boolean break byte case catch char class const continue default do double "
    "else enum extends final finally float for goto if implements import instanceof int "
    "interface long native new package private protected public return short static strictfp "
    "super switch synchronized this throw throws transient try void volatile while true false "
    "null var record yield _"
)
JAVA_KEYWORDS = frozenset(_JAVA_WORDS.split())

Type = Mapping[str, Any]


@dataclass(frozen=True)
class Scalar:
    """A C number's Java and JNI spellings."""

    c: str  # int32_t
    java: str  # int
    jni: str  # jint
    letter: str  # I, the JNI signature
    stem: str  # Int, JNI's array functions (GetIntArrayRegion)
    rust: str  # i32


SCALARS = {
    "int8_t": Scalar("int8_t", "byte", "jbyte", "B", "Byte", "i8"),
    "uint8_t": Scalar("uint8_t", "byte", "jbyte", "B", "Byte", "u8"),
    "int16_t": Scalar("int16_t", "short", "jshort", "S", "Short", "i16"),
    "uint16_t": Scalar("uint16_t", "short", "jshort", "S", "Short", "u16"),
    "int32_t": Scalar("int32_t", "int", "jint", "I", "Int", "i32"),
    "uint32_t": Scalar("uint32_t", "int", "jint", "I", "Int", "u32"),
    "int64_t": Scalar("int64_t", "long", "jlong", "J", "Long", "i64"),
    "uint64_t": Scalar("uint64_t", "long", "jlong", "J", "Long", "u64"),
    "float": Scalar("float", "float", "jfloat", "F", "Float", "f32"),
    "double": Scalar("double", "double", "jdouble", "D", "Double", "f64"),
}
BOOL = Scalar("bool", "boolean", "jboolean", "Z", "Boolean", "bool")


class Crossing(StrEnum):
    """How a parameter crosses between Java and the export."""

    SCALAR = "scalar"  # a number or bool, by value
    STRING = "string"  # const char *
    HANDLE = "handle"  # a pointer to a handle type
    CONTEXT = "context"  # void *: a Java object
    RELEASE = "release"  # ccm_release_fn: the glue's own
    CALLBACK = "callback"  # a function pointer type: the glue's trampoline
    OUT_SCALAR = "out_scalar"  # T *: one element
    OUT_STRING = "out_string"  # char **
    OUT_POINTER = "out_pointer"  # X ** or void **: a long
    ARRAY = "array"  # const T *<p>_data, int32_t <p>_count
    STRINGS = "strings"  # const char **<p>_data, int32_t <p>_count
    ARRAY_OUT = "array_out"  # T **<p>_data, int32_t *<p>_count


@dataclass(frozen=True)
class Param:
    """A parameter of an export or a call into Java as it crosses: ``name`` is its Java name (a
    Java keyword gets a trailing ``_``), ``c_names`` the C parameters it stands for (two for an
    array), ``c_type`` the C type's spelling (the element's for an array)."""

    name: str
    crossing: Crossing
    c_names: tuple[str, ...]
    c_type: str
    scalar: Scalar | None = None
    callback: str | None = None  # the function pointer type, for CALLBACK
    handle: str | None = None  # the handle type, for HANDLE and OUT_POINTER
    const: bool = False  # a handle the export doesn't change

    def number(self) -> Scalar:
        """The number it is or holds (a scalar, an out scalar, an array's element)."""
        if self.scalar is None:
            raise ValueError(f"{self.name} ({self.crossing}) is no number")
        return self.scalar

    @property
    def java_raw(self) -> str | None:
        """The raw native method's parameter, or None (the glue supplies it)."""
        s = self.scalar
        match self.crossing:
            case Crossing.SCALAR:
                return f"{s.java if s else '?'} {self.name}"
            case Crossing.STRING:
                return f"byte[] {self.name}"
            case Crossing.HANDLE:
                return f"long {self.name}"
            case Crossing.CONTEXT:
                return f"Object {self.name}"
            case Crossing.OUT_SCALAR:
                return f"{s.java if s else '?'}[] {self.name}"
            case Crossing.OUT_STRING | Crossing.STRINGS:
                return f"byte[][] {self.name}"
            case Crossing.OUT_POINTER:
                return f"long[] {self.name}"
            case Crossing.ARRAY:
                return f"{s.java if s else '?'}[] {self.name}"
            case Crossing.ARRAY_OUT:
                return f"{s.java if s else '?'}[][] {self.name}"
            case Crossing.RELEASE | Crossing.CALLBACK:
                return None

    @property
    def java(self) -> str | None:
        """The parameter of the method the façades call, with Java types."""
        match self.crossing:
            case Crossing.STRING:
                return f"String {self.name}"
            case Crossing.OUT_STRING | Crossing.STRINGS:
                return f"String[] {self.name}"
            case _:
                return self.java_raw

    @property
    def signature(self) -> str:
        """The raw parameter's JNI signature."""
        s = self.scalar
        letter = s.letter if s else "?"
        return {
            Crossing.SCALAR: letter,
            Crossing.STRING: "[B",
            Crossing.HANDLE: "J",
            Crossing.CONTEXT: "Ljava/lang/Object;",
            Crossing.OUT_SCALAR: f"[{letter}",
            Crossing.OUT_STRING: "[[B",
            Crossing.STRINGS: "[[B",
            Crossing.OUT_POINTER: "[J",
            Crossing.ARRAY: f"[{letter}",
            Crossing.ARRAY_OUT: f"[[{letter}",
        }.get(self.crossing, "")

    @property
    def converts(self) -> bool:
        """The façades' method converts it from Java's types."""
        return self.crossing in (Crossing.STRING, Crossing.OUT_STRING, Crossing.STRINGS)


@dataclass(frozen=True)
class CParam:
    """A C parameter as the header declares it."""

    name: str
    type: Type


@dataclass(frozen=True)
class Export:
    """An export of the bridge: its JNI glue calls it with ``c_params``."""

    name: str
    returns: str  # "int" (a ccm_status), "void" (a release) or "long" (a key, uint64_t)
    params: tuple[Param, ...]
    c_params: tuple[CParam, ...]
    header: str
    line: int

    @property
    def converts(self) -> bool:
        return any(p.converts for p in self.params)

    @property
    def native_name(self) -> str:
        """The native method's name: the export's, ``_raw`` when a wrapper converts."""
        return f"{self.name}_raw" if self.converts else self.name

    @property
    def jni_name(self) -> str:
        return JNI_PREFIX + mangle(self.native_name)

    @property
    def owns_context(self) -> bool:
        """It takes a ccm_release_fn: native code keeps the context and releases it."""
        return any(p.crossing is Crossing.RELEASE for p in self.params)


@dataclass(frozen=True)
class Callback:
    """A function pointer type of the bridge, a call into Java: its ``void *context`` first,
    then ``params``. It returns a ccm_status."""

    name: str
    params: tuple[Param, ...]
    c_params: tuple[CParam, ...]
    header: str
    line: int

    @property
    def method(self) -> str:
        """The static CcmNative method its trampoline calls."""
        return f"{self.name}_call"

    @property
    def signature(self) -> str:
        return "(Ljava/lang/Object;" + "".join(p.signature for p in self.params) + ")I"


@dataclass(frozen=True)
class Problem:
    """A declaration the bridge can't take: the header, its line, and why."""

    header: str
    line: int
    message: str


@dataclass
class Bridge:
    """What the bridge headers declare, as it crosses from Java."""

    exports: list[Export] = field(default_factory=list[Export])
    callbacks: list[Callback] = field(default_factory=list[Callback])
    handles: list[str] = field(default_factory=list[str])
    problems: list[Problem] = field(default_factory=list[Problem])
    headers: list[str] = field(default_factory=list[str])


class _RefusedError(Exception):
    pass


def ident(name: str) -> str:
    """A C parameter's name in Java (and in the glue): a Java keyword gets a trailing ``_``."""
    return f"{name}_" if name in JAVA_KEYWORDS else name


def mangle(name: str) -> str:
    """A Java method's name as JNI spells it in a symbol: ``_`` is ``_1``, and a character
    outside ASCII letters and digits is ``_0xxxx``."""
    out: list[str] = []
    for c in name:
        if c == "_":
            out.append("_1")
        elif c.isascii() and c.isalnum():
            out.append(c)
        else:
            out.append(f"_0{ord(c):04x}")
    return "".join(out)


def kind(t: Type) -> str:
    return str(t.get("k", "unknown"))


def scalar_of(t: Type) -> Scalar | None:
    if kind(t) in ("int", "float"):
        return SCALARS.get(str(t.get("name")))
    if kind(t) == "bool":
        return BOOL
    return None


def spell(t: Type) -> str:
    """A C type's spelling, from the helper's description."""
    k = kind(t)
    if k == "ptr":
        to = cast(Type, t.get("to") or {})
        return f"{'const ' if t.get('const') else ''}{spell(to)} *"
    if k in ("int", "float", "handle", "callback"):
        return str(t.get("name", k))
    if k == "unknown":
        return str(t.get("spelling", "?"))
    return {"bool": "bool", "void": "void", "char": "char", "release": "ccm_release_fn"}.get(k, k)


def _pointee(t: Type) -> Type:
    return cast(Type, t.get("to") or {})


def _arrays(names: Sequence[str]) -> set[str]:
    """The arrays among parameters: ``<p>_data`` beside ``<p>_count``."""
    return {
        n.removesuffix("_data")
        for n in names
        if n.endswith("_data") and n.removesuffix("_data") + "_count" in names
    }


def _export_params(params: Sequence[CParam]) -> tuple[Param, ...]:
    names = [p.name for p in params]
    arrays = _arrays(names)
    counts = {f"{a}_count" for a in arrays}
    out: list[Param] = []
    for p in params:
        if p.name in counts:
            continue
        t = p.type
        k = kind(t)
        base = p.name.removesuffix("_data")
        if p.name.endswith("_data") and base in arrays:
            out.append(_array(base, t, p.name))
            continue
        name = ident(p.name)
        scalar = scalar_of(t)
        if scalar is not None:
            out.append(Param(name, Crossing.SCALAR, (p.name,), spell(t), scalar))
        elif k == "release":
            out.append(Param(name, Crossing.RELEASE, (p.name,), "ccm_release_fn"))
        elif k == "callback":
            callback = str(t.get("name"))
            out.append(Param(name, Crossing.CALLBACK, (p.name,), callback, callback=callback))
        elif k == "ptr":
            out.append(_pointer(name, p.name, t))
        else:
            raise _RefusedError(f"{p.name} is of {spell(t)}, which a bridge doesn't take")
    return tuple(out)


def _array(base: str, t: Type, c_name: str) -> Param:
    to = _pointee(t)
    names = (c_name, f"{base}_count")
    name = ident(base)
    if kind(t) == "ptr" and t.get("const") and scalar_of(to) is not None:
        return Param(name, Crossing.ARRAY, names, spell(to), scalar_of(to))
    if kind(t) == "ptr" and kind(to) == "ptr" and kind(_pointee(to)) == "char":
        return Param(name, Crossing.STRINGS, names, "const char *")
    element = _pointee(to)
    if kind(t) == "ptr" and not t.get("const") and kind(to) == "ptr" and scalar_of(element):
        return Param(name, Crossing.ARRAY_OUT, names, spell(element), scalar_of(element))
    raise _RefusedError(f"the array {base} is of {spell(t)}: arrays are of numbers or strings")


def _pointer(name: str, c_name: str, t: Type) -> Param:
    to = _pointee(t)
    tk = kind(to)
    const = bool(t.get("const"))
    if tk == "char":
        if const:
            return Param(name, Crossing.STRING, (c_name,), "const char *")
        raise _RefusedError(f"{c_name} is a char * that isn't const: a string in is const char *")
    if tk == "void":
        return Param(name, Crossing.CONTEXT, (c_name,), "void *")
    if tk == "handle":
        handle = str(to.get("name"))
        return Param(name, Crossing.HANDLE, (c_name,), spell(t), handle=handle, const=const)
    if not const:
        scalar = scalar_of(to)
        if scalar is not None:
            return Param(name, Crossing.OUT_SCALAR, (c_name,), spell(to), scalar)
        if tk == "ptr":
            inner = _pointee(to)
            if kind(inner) == "char" and not to.get("const"):
                return Param(name, Crossing.OUT_STRING, (c_name,), "char *")
            if kind(inner) in ("handle", "void"):
                handle = str(inner.get("name")) if kind(inner) == "handle" else None
                return Param(name, Crossing.OUT_POINTER, (c_name,), spell(inner), handle=handle)
    raise _RefusedError(f"{c_name} is of {spell(t)}, which a bridge doesn't take")


def _callback_params(name: str, params: Sequence[CParam]) -> tuple[Param, ...]:
    if not params or kind(params[0].type) != "ptr" or kind(_pointee(params[0].type)) != "void":
        raise _RefusedError(f"{name}: a call into Java takes its void *context first")
    out: list[Param] = []
    for p in params[1:]:
        t = p.type
        pname = ident(p.name)
        scalar = scalar_of(t)
        if scalar is not None:
            out.append(Param(pname, Crossing.SCALAR, (p.name,), spell(t), scalar))
            continue
        if kind(t) == "ptr":
            to = _pointee(t)
            tk = kind(to)
            if tk == "char" and t.get("const"):
                out.append(Param(pname, Crossing.STRING, (p.name,), "const char *"))
                continue
            if tk == "handle":
                handle = str(to.get("name"))
                out.append(Param(pname, Crossing.HANDLE, (p.name,), spell(t), handle=handle))
                continue
            if not t.get("const"):
                if scalar_of(to) is not None:
                    out.append(
                        Param(pname, Crossing.OUT_SCALAR, (p.name,), spell(to), scalar_of(to))
                    )
                    continue
                if tk == "ptr" and kind(_pointee(to)) == "char" and not to.get("const"):
                    out.append(Param(pname, Crossing.OUT_STRING, (p.name,), "char *"))
                    continue
        raise _RefusedError(
            f"{name}: {p.name} is of {spell(t)}, which a call into Java doesn't take"
        )
    return tuple(out)


def _returns(name: str, t: Type) -> str:
    if kind(t) == "void":
        return "void"
    if kind(t) == "int" and t.get("name") == "int32_t":
        return "int"
    if kind(t) == "int" and t.get("name") == "uint64_t":
        return "long"
    raise _RefusedError(f"{name} returns {spell(t)}: an export returns a status (ccm_status)")


def _c_params(entry: Mapping[str, Any]) -> tuple[CParam, ...]:
    return tuple(
        CParam(str(p["name"]), cast(Type, p["type"]))
        for p in cast(list[Mapping[str, Any]], entry.get("params") or [])
    )


def read_bridge(headers: Mapping[str, Mapping[str, Any]]) -> Bridge:
    """The bridge the headers declare (``header → what the helper read``, keys as worktree
    paths), and what they declare that a bridge can't take."""
    bridge = Bridge(headers=sorted(headers))
    for header in sorted(headers):
        declared = headers[header]
        for name in cast(list[Any], declared.get("handles") or []):
            if str(name) not in bridge.handles:
                bridge.handles.append(str(name))
        for entry in cast(list[Mapping[str, Any]], declared.get("functions") or []):
            name = str(entry["name"])
            line = int(entry.get("line", 1))
            c_params = _c_params(entry)
            try:
                bridge.exports.append(
                    Export(
                        name,
                        _returns(name, cast(Type, entry.get("returns") or {})),
                        _export_params(c_params),
                        c_params,
                        header,
                        line,
                    )
                )
            except _RefusedError as exc:
                bridge.problems.append(Problem(header, line, f"{name}: {exc}"))
        for entry in cast(list[Mapping[str, Any]], declared.get("callbacks") or []):
            name = str(entry["name"])
            line = int(entry.get("line", 1))
            c_params = _c_params(entry)
            try:
                _returns(name, cast(Type, entry.get("returns") or {}))
                bridge.callbacks.append(
                    Callback(name, _callback_params(name, c_params), c_params, header, line)
                )
            except _RefusedError as exc:
                bridge.problems.append(Problem(header, line, str(exc)))
    exported = {e.name for e in bridge.exports}
    for handle in bridge.handles:
        used = any(p.handle == handle for e in bridge.exports for p in e.params) or any(
            p.handle == handle for c in bridge.callbacks for p in c.params
        )
        if not used and f"{handle}_release" not in exported:
            continue
        missing = [f"{handle}{s}" for s in ("_release", "_key") if f"{handle}{s}" not in exported]
        if missing:
            header = next(
                (
                    h
                    for h in sorted(headers)
                    if handle in [str(n) for n in cast(list[Any], headers[h].get("handles") or [])]
                ),
                "",
            )
            bridge.problems.append(
                Problem(
                    header,
                    1,
                    f"the handle type {handle} has no {' or '.join(missing)}: declare it with "
                    "CCM_HANDLE (a Rust bridge writes both exports out: cbindgen expands no "
                    "macro)",
                )
            )
    return bridge


# ------------------------------------------------------------------ the Java declarations


def initial_declarations(library: str) -> str:
    """``CcmNative.java`` before the bridge exports anything: the library's name, which the
    support file needs to compile."""
    return java_declarations(Bridge(), library)


def java_declarations(bridge: Bridge, library: str, source: str = "the bridge headers") -> str:
    """``codecoremorph.interop.CcmNative``: the native library ``library``'s exports as the
    façades call them, and the interfaces of its calls into Java."""
    lines = [
        f"// Generated by CodeCoreMorph from {source} when the native code is configured; do",
        "// not edit. Java 8.",
        f"package {PACKAGE};",
        "",
        "/** The native library's exports, as the facades call them (CcmInterop.check each). */",
        f"public final class {CLASS} {{",
        f'    public static final String LIBRARY = "{library}";',
        "",
        f"    private {CLASS}() {{",
        "    }",
        "",
        "    static {",
        "        System.loadLibrary(LIBRARY);",
        "    }",
        "",
        "    /** Loads the library: CcmInterop calls it before its own natives. */",
        "    static void load() {",
        "    }",
    ]
    for export in bridge.exports:
        lines += _java_export(export)
    for callback in bridge.callbacks:
        lines += _java_callback(callback)
    lines.append("}")
    return "\n".join(lines) + "\n"


def _java_export(e: Export) -> list[str]:
    raw = ", ".join(p.java_raw for p in e.params if p.java_raw)
    if not e.converts:
        return ["", f"    public static native {e.returns} {e.name}({raw});"]
    wrapper = ", ".join(p.java for p in e.params if p.java)
    lines = [
        "",
        f"    private static native {e.returns} {e.native_name}({raw});",
        "",
        f"    public static {e.returns} {e.name}({wrapper}) {{",
    ]
    args: list[str] = []
    after: list[str] = []
    for p in e.params:
        match p.crossing:
            case Crossing.STRING:
                args.append(f"CcmInterop.utf8({p.name})")
            case Crossing.STRINGS:
                args.append(f"CcmInterop.utf8s({p.name})")
            case Crossing.OUT_STRING:
                lines.append(f"        byte[][] {p.name}_utf8 = new byte[1][];")
                args.append(f"{p.name}_utf8")
                after.append(f"        {p.name}[0] = CcmInterop.string({p.name}_utf8[0]);")
            case Crossing.RELEASE | Crossing.CALLBACK:
                pass
            case _:
                args.append(p.name)
    call = f"{e.native_name}({', '.join(args)})"
    if e.returns == "void":
        lines += [f"        {call};", *after]
    elif after:
        lines += [f"        {e.returns} status = {call};", *after, "        return status;"]
    else:
        lines.append(f"        return {call};")
    lines.append("    }")
    return lines


def _java_callback(c: Callback) -> list[str]:
    wrapper = ", ".join(p.java or "" for p in c.params)
    raw = ", ".join(["Object context", *[p.java_raw or "" for p in c.params]])
    lines = [
        "",
        f"    /** A call into Java of {c.name}: what its context object implements. */",
        f"    public interface {c.name} {{",
        f"        int call({wrapper});",
        "    }",
        "",
        "    // The JNI glue calls it.",
        f"    private static int {c.method}({raw}) {{",
        "        try {",
    ]
    args: list[str] = []
    after: list[str] = []
    for p in c.params:
        match p.crossing:
            case Crossing.STRING:
                args.append(f"CcmInterop.string({p.name})")
            case Crossing.OUT_STRING:
                lines.append(f"            String[] {p.name}_text = new String[1];")
                args.append(f"{p.name}_text")
                after.append(f"            {p.name}[0] = CcmInterop.utf8({p.name}_text[0]);")
            case _:
                args.append(p.name)
    lines.append(f"            int status = (({c.name}) context).call({', '.join(args)});")
    lines += after
    lines += [
        "            return status;",
        "        } catch (Throwable thrown) {",
        "            return CcmInterop.managed(thrown);",
        "        }",
        "    }",
    ]
    return lines
