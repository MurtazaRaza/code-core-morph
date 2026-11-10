"""The C# declarations of a C# façade's bridge (SPEC §11.9, D-78), written from what the C++
index helper read in the bridge headers (``--bridge-declarations``): a ``DllImport`` per export, a
``SafeHandle`` per handle type and a delegate per function pointer type, into one generated file
of the façade's project.

The declarations use only what C# 7.3 and ``netstandard2.0`` allow. ``netstandard2.0`` has no
``UnmanagedType.LPUTF8Str``, so a string crosses as NUL-terminated UTF-8 bytes: an export that
takes strings gets a private raw ``extern`` and an internal wrapper with the C# types.

The bridge's conventions: a handle type ``X`` has the exports ``X_release`` (given the raw
pointer, from ``SafeHandle.ReleaseHandle``) and ``X_key``; an input array is a pointer to const
named ``<p>_<x>`` beside an ``int32_t <p>_count``; a non-const pointer is an out parameter, and
``char **`` is a string out, which the façade reads and frees.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

INTS = {
    "int8_t": "sbyte",
    "uint8_t": "byte",
    "int16_t": "short",
    "uint16_t": "ushort",
    "int32_t": "int",
    "uint32_t": "uint",
    "int64_t": "long",
    "uint64_t": "ulong",
}
U1 = "[MarshalAs(UnmanagedType.U1)] "
NAMESPACE = "CodeCoreMorph.Interop"
GENERATED_FILE = "CodeCoreMorph.Native.g.cs"

Type = Mapping[str, Any]


@dataclass(frozen=True)
class BridgeProblem:
    """A declaration the bridge can't take: the header, its line, and why."""

    header: str
    line: int
    message: str


class _RefusedError(Exception):
    pass


@dataclass(frozen=True)
class _Param:
    raw: str  # in the extern
    wrapper: str  # in the wrapper, with C# types
    arg: str  # what the wrapper passes
    strings: bool = False  # a string array, copied to native strings for the call


def _kind(t: Type) -> str:
    return str(t.get("k", "unknown"))


def _spell(t: Type) -> str:
    kind = _kind(t)
    if kind == "ptr":
        to = cast(Type, t["to"])
        return f"{'const ' if t.get('const') else ''}{_spell(to)} *"
    if kind in ("int", "float", "handle", "callback"):
        return str(t.get("name", kind))
    if kind == "unknown":
        return str(t.get("spelling", "?"))
    return {"decimal": "ccm_decimal", "release": "ccm_release_fn"}.get(kind, kind)


def _scalar(t: Type) -> str | None:
    kind = _kind(t)
    if kind == "int":
        return INTS.get(str(t.get("name")))
    if kind == "float":
        return str(t.get("name"))
    return None


def _plain(decl: str, arg: str) -> _Param:
    return _Param(decl, decl.replace(U1, ""), arg)


def _param(t: Type, name: str, arrays: set[str]) -> _Param:
    kind = _kind(t)
    scalar = _scalar(t)
    if name in arrays:
        to = cast(Type, t.get("to") or {})
        if kind == "ptr" and t.get("const") and _scalar(to):
            return _plain(f"{_scalar(to)}[] {name}", name)
        if kind == "ptr" and _kind(to) == "ptr" and _kind(cast(Type, to["to"])) == "char":
            return _Param(f"IntPtr[] {name}", f"string[] {name}", f"{name}_utf8", strings=True)
        raise _RefusedError(f"the array {name} is of {_spell(t)}: arrays are of numbers or strings")
    if scalar:
        return _plain(f"{scalar} {name}", name)
    if kind == "bool":
        return _plain(f"{U1}bool {name}", name)
    if kind == "decimal":
        return _plain(f"CcmDecimal {name}", name)
    if kind == "callback":
        return _plain(f"{t['name']} {name}", name)
    if kind == "release":
        return _plain(f"CcmReleaseFn {name}", name)
    if kind == "ptr":
        to = cast(Type, t["to"])
        target = _kind(to)
        if target == "char":
            if t.get("const"):
                return _Param(f"byte[] {name}", f"string {name}", f"CcmInterop.Utf8Z({name})")
            raise _RefusedError(f"{name} is a char * that isn't const: a string in is const char *")
        if target == "void":
            return _plain(f"IntPtr {name}", name)
        if target == "handle":
            return _plain(f"{to['name']}Handle {name}", name)
        if not t.get("const"):
            if _scalar(to):
                return _plain(f"out {_scalar(to)} {name}", f"out {name}")
            if target == "bool":
                return _plain(f"{U1}out bool {name}", f"out {name}")
            if target == "decimal":
                return _plain(f"out CcmDecimal {name}", f"out {name}")
            if target == "ptr":
                inner = cast(Type, to["to"])
                if (_kind(inner) == "char" and not to.get("const")) or _kind(inner) == "void":
                    return _plain(f"out IntPtr {name}", f"out {name}")
                if _kind(inner) == "handle":
                    return _plain(f"out {inner['name']}Handle {name}", f"out {name}")
    raise _RefusedError(f"{name} is of {_spell(t)}, which a bridge doesn't take")


def _callback_param(t: Type, name: str) -> str:
    kind = _kind(t)
    scalar = _scalar(t)
    if scalar:
        return f"{scalar} {name}"
    if kind == "bool":
        return f"{U1}bool {name}"
    if kind == "decimal":
        return f"CcmDecimal {name}"
    if kind == "ptr":
        to = cast(Type, t["to"])
        target = _kind(to)
        if target == "char" and t.get("const"):
            return f"IntPtr {name}"  # read with CcmInterop.Utf8
        if target in ("void", "handle"):
            return f"IntPtr {name}"  # a handle the C# side owns
        if not t.get("const"):
            if _scalar(to):
                return f"out {_scalar(to)} {name}"
            if target == "bool":
                return f"{U1}out bool {name}"
            if target == "decimal":
                return f"out CcmDecimal {name}"
            if target == "ptr" and _kind(cast(Type, to["to"])) == "char" and not to.get("const"):
                return f"out IntPtr {name}"  # set with CcmInterop.NewString
    raise _RefusedError(f"{name} is of {_spell(t)}, which a call into C# doesn't take")


def _returns(t: Type) -> str:
    kind = _kind(t)
    if kind == "void":
        return "void"
    scalar = _scalar(t)
    if scalar:
        return scalar
    raise _RefusedError(f"returns {_spell(t)}: an export returns a status (ccm_status) or nothing")


def _arrays(names: Sequence[str]) -> set[str]:
    return {
        n
        for n in names
        if "_" in n and not n.endswith("_count") and n.rsplit("_", 1)[0] + "_count" in names
    }


def _function(entry: Mapping[str, Any], handles: set[str]) -> list[str]:
    name = str(entry["name"])
    returns = _returns(cast(Type, entry["returns"]))
    params_in = cast(list[Mapping[str, Any]], entry.get("params") or [])
    if name.endswith("_release") and name.removesuffix("_release") in handles:
        params = [_Param("IntPtr handle", "IntPtr handle", "handle")]
    else:
        names = [str(p["name"]) for p in params_in]
        arrays = _arrays(names)
        params = [_param(cast(Type, p["type"]), str(p["name"]), arrays) for p in params_in]
    attribute = (
        "        [DllImport(Library, CallingConvention = CallingConvention.Cdecl, "
        "ExactSpelling = true"
    )
    raw = ", ".join(p.raw for p in params)
    if all(p.raw.replace(U1, "") == p.wrapper for p in params) and not any(
        p.strings for p in params
    ):
        return ["", attribute + ")]", f"        internal static extern {returns} {name}({raw});"]
    lines = [
        "",
        attribute + f', EntryPoint = "{name}")]',
        f"        private static extern {returns} {name}_raw({raw});",
        "",
        f"        internal static {returns} {name}({', '.join(p.wrapper for p in params)})",
        "        {",
    ]
    call = f"{name}_raw({', '.join(p.arg for p in params)})"
    result = "return " if returns != "void" else ""
    copies = [p.wrapper.split()[-1] for p in params if p.strings]
    if not copies:
        lines.append(f"            {result}{call};")
    else:
        lines += [f"            IntPtr[] {c}_utf8 = CcmInterop.NewStrings({c});" for c in copies]
        lines += ["            try", "            {", f"                {result}{call};"]
        lines += ["            }", "            finally", "            {"]
        lines += [f"                CcmInterop.FreeStrings({c}_utf8);" for c in copies]
        lines.append("            }")
    lines.append("        }")
    return lines


def _callback(entry: Mapping[str, Any]) -> list[str]:
    returns = _returns(cast(Type, entry["returns"]))
    params = [
        _callback_param(cast(Type, p["type"]), str(p["name"]))
        for p in cast(list[Mapping[str, Any]], entry.get("params") or [])
    ]
    return [
        "",
        "    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]",
        f"    internal delegate {returns} {entry['name']}({', '.join(params)});",
    ]


def _handle(name: str) -> list[str]:
    return [
        "",
        f"    internal sealed class {name}Handle : SafeHandle, ICcmHandle",
        "    {",
        f"        public {name}Handle() : base(IntPtr.Zero, true)",
        "        {",
        "        }",
        "",
        f"        public {name}Handle(IntPtr handle) : base(IntPtr.Zero, true)",
        "        {",
        "            SetHandle(handle);",
        "        }",
        "",
        "        /// <summary>A null handle, for a null object.</summary>",
        f"        public static {name}Handle Null",
        "        {",
        f"            get {{ return new {name}Handle(); }}",
        "        }",
        "",
        "        public override bool IsInvalid",
        "        {",
        "            get { return handle == IntPtr.Zero; }",
        "        }",
        "",
        "        public ulong Key",
        "        {",
        f"            get {{ return CcmNative.{name}_key(this); }}",
        "        }",
        "",
        "        protected override bool ReleaseHandle()",
        "        {",
        f"            CcmNative.{name}_release(handle);",
        "            return true;",
        "        }",
        "    }",
    ]


def csharp_declarations(
    headers: Mapping[str, Mapping[str, Any]], library: str
) -> tuple[str, list[BridgeProblem]]:
    """The generated C# file for the bridge headers (``header → what the helper read``, keys
    as worktree paths) of the native library ``library``, and what the headers declare that a
    bridge can't take (left out of the file)."""
    problems: list[BridgeProblem] = []
    handles_of = {
        h: [str(n) for n in cast(list[Any], d.get("handles") or [])] for h, d in headers.items()
    }
    handles = {n for names in handles_of.values() for n in names}
    exported = {
        str(f["name"])
        for decl in headers.values()
        for f in cast(list[Mapping[str, Any]], decl.get("functions") or [])
    }
    functions: list[str] = []
    callbacks: list[str] = []
    for header in sorted(headers):
        decl = headers[header]
        for entry in cast(list[Mapping[str, Any]], decl.get("functions") or []):
            try:
                functions += _function(entry, handles)
            except _RefusedError as exc:
                problems.append(
                    BridgeProblem(header, int(entry.get("line", 1)), f"{entry['name']}: {exc}")
                )
        for entry in cast(list[Mapping[str, Any]], decl.get("callbacks") or []):
            try:
                callbacks += _callback(entry)
            except _RefusedError as exc:
                problems.append(
                    BridgeProblem(header, int(entry.get("line", 1)), f"{entry['name']}: {exc}")
                )
    for handle in sorted(handles):
        missing = [f"{handle}{s}" for s in ("_release", "_key") if f"{handle}{s}" not in exported]
        if missing:
            header = next((h for h, names in handles_of.items() if handle in names), "")
            problems.append(
                BridgeProblem(
                    header,
                    1,
                    f"the handle type {handle} has no {' or '.join(missing)}: declare it with "
                    "CCM_HANDLE",
                )
            )
    lines = [
        "// <auto-generated>",
        "// Written by CodeCoreMorph from the bridge headers (cpp/bridge/*.h) when the C++ is",
        "// configured; do not edit. C# 7.3 and netstandard2.0.",
        "// </auto-generated>",
        "using System;",
        "using System.Runtime.InteropServices;",
        "",
        f"namespace {NAMESPACE}",
        "{",
        "    internal static partial class CcmNative",
        "    {",
        f'        internal const string Library = "{library}";',
        *functions,
        "    }",
        *callbacks,
    ]
    for handle in sorted(handles):
        lines += _handle(handle)
    lines.append("}")
    return "\n".join(lines) + "\n", problems
