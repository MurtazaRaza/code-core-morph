"""The Rust JNI glue of a Java façade's bridge for a Rust target (SPEC §11.12, D-92): a JNI
function per native method of ``codecoremorph.interop.CcmNative``, which converts its arguments
and calls the crate's own export through an ``extern "C"`` declaration with plain pointers, and a
trampoline per function pointer type, which calls the static ``CcmNative.<type>_call`` with the
context object. The crate's bridge ``include!``s it (``jni_g.rs``), so cbindgen, which expands no
macro, never takes the JNI functions for the bridge's; it uses the fixed part CodeCoreMorph ships
(``jni.rs``) over bindgen's JNI types (``jni_sys.rs``, from the JDK's ``jni.h`` at configure).
Generated from the :class:`~codecoremorph.adapters.java.jni.Bridge` the Java declarations come
from.
"""

from __future__ import annotations

from typing import cast

from codecoremorph.adapters.java.jni import Bridge, Callback, Crossing, Export, Type, kind

GENERATED_FILE = "jni_g.rs"
FIXED_FILE = "jni.rs"
SYS_FILE = "jni_sys.rs"

_RUST_SCALARS = {
    "int8_t": "i8",
    "uint8_t": "u8",
    "int16_t": "i16",
    "uint16_t": "u16",
    "int32_t": "i32",
    "uint32_t": "u32",
    "int64_t": "i64",
    "uint64_t": "u64",
    "float": "f32",
    "double": "f64",
}
# A Rust bridge's parameter that is a Rust keyword can't come from cbindgen, but a C++ bridge's
# header could name one; the glue escapes it.
_RUST_WORDS = (
    "as async await break const continue crate dyn else enum extern false fn for if impl in let "
    "loop match mod move mut pub ref return self Self static struct super trait true type unsafe "
    "use where while abstract become box do final macro override priv typeof unsized virtual "
    "yield try gen"
)
_RUST_KEYWORDS = frozenset(_RUST_WORDS.split())


def _r(name: str) -> str:
    return f"{name}_" if name in _RUST_KEYWORDS else name


def rust_type(t: Type) -> str:
    """A C parameter's type in the glue's extern declarations: handles and contexts as
    ``c_void`` pointers, function pointers as ``*const c_void``."""
    k = kind(t)
    if k in ("int", "float"):
        return _RUST_SCALARS.get(str(t.get("name")), "i32")
    if k == "bool":
        return "bool"
    if k == "release":
        return 'Option<unsafe extern "C" fn(*mut c_void)>'
    if k == "callback":
        return "*const c_void"
    if k == "void":
        return "()"
    if k == "ptr":
        to = cast(Type, t.get("to") or {})
        mutability = "const" if t.get("const") else "mut"
        if kind(to) == "char":
            return f"*{mutability} c_char"
        if kind(to) in ("int", "float", "bool", "ptr"):
            return f"*{mutability} {rust_type(to)}"
        return f"*{mutability} c_void"
    return "*mut c_void"


def _scalar_rust(c: str) -> str:
    return "bool" if c == "bool" else _RUST_SCALARS[c]


def _zero(c: str) -> str:
    """A scalar's zero as a Rust literal: an integer literal isn't a float."""
    return {"bool": "false", "float": "0.0", "double": "0.0"}.get(c, "0")


def _export(e: Export) -> list[str]:
    params = ["env: *mut JNIEnv", "_cls: jclass"]
    pre: list[str] = []
    args: list[str] = []
    ok: list[str] = []
    post: list[str] = []
    for p in e.params:
        n = _r(p.name)
        match p.crossing:
            case Crossing.SCALAR:
                s = p.number()
                params.append(f"{n}: {s.jni}")
                args.append(f"{n} != 0" if s.c == "bool" else f"{n} as {_scalar_rust(s.c)}")
            case Crossing.STRING:
                params.append(f"{n}: jbyteArray")
                pre.append(f"let c_{n} = jni::utf8(env, {n});")
                args.append(f"c_{n}.as_ptr()")
            case Crossing.HANDLE:
                params.append(f"{n}: jlong")
                args.append(f"{n} as usize as *mut c_void")
            case Crossing.CONTEXT:
                params.append(f"{n}: jobject")
                pre.append(f"let c_{n} = jni::global(env, {n});")
                args.append(f"c_{n}")
                if not e.owns_context:
                    post.append(f"jni::release(c_{n});")
            case Crossing.RELEASE:
                args.append("Some(jni::release)")
            case Crossing.CALLBACK:
                args.append(f"cb_{p.callback} as *const c_void")
            case Crossing.OUT_SCALAR:
                s = p.number()
                params.append(f"{n}: jarray")
                pre.append(f"let mut c_{n}: {_scalar_rust(s.c)} = {_zero(s.c)};")
                args.append(f"&mut c_{n}")
                value = f"c_{n}" if s.c == "bool" else f"c_{n} as _"
                ok.append(f"jni::put_{s.java}(env, {n}, {value});")
            case Crossing.OUT_STRING:
                params.append(f"{n}: jobjectArray")
                pre.append(f"let mut c_{n}: *mut c_char = std::ptr::null_mut();")
                args.append(f"&mut c_{n}")
                ok.append(f"jni::put_string(env, {n}, c_{n});")
                ok.append(f"c_{n} = std::ptr::null_mut();")
                post.append(f"jni::free(c_{n}.cast());")
            case Crossing.OUT_POINTER:
                params.append(f"{n}: jarray")
                pre.append(f"let mut c_{n}: *mut c_void = std::ptr::null_mut();")
                args.append(f"&mut c_{n}")
                ok.append(f"jni::put_long(env, {n}, c_{n} as usize as i64);")
            case Crossing.ARRAY:
                s = p.number()
                params.append(f"{n}: jarray")
                pre.append(f"let (c_{n}, n_{n}) = jni::{s.java}_elements(env, {n});")
                args += [f"c_{n}.as_ptr_or_null().cast()", f"n_{n}"]
            case Crossing.STRINGS:
                params.append(f"{n}: jobjectArray")
                pre.append(f"let c_{n} = jni::strings(env, {n});")
                args += [f"c_{n}.as_ptr()", f"c_{n}.count()"]
            case Crossing.ARRAY_OUT:
                s = p.number()
                params.append(f"{n}: jobjectArray")
                pre += [
                    f"let mut c_{n}: *mut {_scalar_rust(s.c)} = std::ptr::null_mut();",
                    f"let mut n_{n}: i32 = 0;",
                ]
                args += [f"&mut c_{n}", f"&mut n_{n}"]
                ok.append(f"jni::put_{s.java}_array(env, {n}, c_{n}.cast(), n_{n});")
                post.append(f"jni::free(c_{n}.cast());")
    ret = {"int": " -> jint", "void": "", "long": " -> jlong"}[e.returns]
    lines = [
        "",
        "#[unsafe(no_mangle)]",
        f'pub unsafe extern "system" fn {e.jni_name}({", ".join(params)}){ret} {{',
        "    // SAFETY: the JVM calls it with its environment and the native method's arguments;",
        "    // the export gets pointers that live for the call.",
        "    unsafe {",
        "        let _ = env;",
        *[f"        {x}" for x in pre],
    ]
    call = f"{e.name}({', '.join(args)})"
    if e.returns == "void":
        lines.append(f"        {call};")
    else:
        lines.append(f"        let status = {call};")
    if ok:
        lines.append("        if status == jni::CCM_OK {")
        lines += [f"            {x}" for x in ok]
        lines.append("        }")
    lines += [f"        {x}" for x in post]
    if e.returns == "int":
        lines.append("        status")
    elif e.returns == "long":
        lines.append("        status as jlong")
    lines += ["    }", "}"]
    return lines


def _callback(c: Callback) -> list[str]:
    params = [f"{_r(c.c_params[0].name)}: *mut c_void"]
    context = _r(c.c_params[0].name)
    pre: list[str] = []
    jargs = [f"jvalue {{ l: {context}.cast() }}"]
    ok: list[str] = []
    local: list[str] = []
    for p in c.params:
        n = _r(p.c_names[0])
        match p.crossing:
            case Crossing.SCALAR:
                s = p.number()
                params.append(f"{n}: {_scalar_rust(s.c)}")
                if s.c == "bool":
                    jargs.append(f"jvalue {{ z: {n} as jboolean }}")
                else:
                    jargs.append(f"jvalue {{ {s.letter.lower()}: {n} as {s.jni} }}")
            case Crossing.STRING:
                params.append(f"{n}: *const c_char")
                pre.append(f"let j_{n} = jni::bytes(env, {n});")
                jargs.append(f"jvalue {{ l: j_{n} }}")
                local.append(f"j_{n}")
            case Crossing.HANDLE:
                params.append(f"{n}: *const c_void")
                jargs.append(f"jvalue {{ j: {n} as usize as jlong }}")
            case Crossing.OUT_SCALAR:
                s = p.number()
                params.append(f"{n}: *mut {_scalar_rust(s.c)}")
                pre.append(f"let j_{n} = jni::new_{s.java}_array(env, 1);")
                jargs.append(f"jvalue {{ l: j_{n} }}")
                local.append(f"j_{n}")
                read = f"jni::get_{s.java}(env, j_{n})"
                ok.append(f"*{n} = {read};" if s.c == "bool" else f"*{n} = {read} as _;")
            case Crossing.OUT_STRING:
                params.append(f"{n}: *mut *mut c_char")
                pre.append(f"let j_{n} = jni::string_slot(env);")
                jargs.append(f"jvalue {{ l: j_{n} }}")
                local.append(f"j_{n}")
                ok.append(f"*{n} = jni::take_string(env, j_{n});")
            case _:
                pass
    lines = [
        "",
        f'static MID_{c.name}: jni::Method = jni::Method::new(c"{c.method}", c"{c.signature}");',
        "",
        f'unsafe extern "C" fn cb_{c.name}({", ".join(params)}) -> i32 {{',
        "    // SAFETY: native code calls it with the context the glue made and the bridge's",
        "    // arguments; the JNI calls run on an attached thread.",
        "    unsafe {",
        "        let env = jni::env();",
        "        if env.is_null() {",
        "            return jni::CCM_ERROR;",
        "        }",
        *[f"        {x}" for x in pre],
        f"        let status = MID_{c.name}.call_int(env, &[{', '.join(jargs)}]);",
    ]
    if ok:
        lines.append("        if status == jni::CCM_OK {")
        lines += [f"            {x}" for x in ok]
        lines.append("        }")
    lines += [f"        jni::delete_local(env, {v});" for v in local]
    lines += ["        status", "    }", "}"]
    return lines


def rust_glue(bridge: Bridge) -> str:
    """``jni_g.rs`` for the bridge."""
    lines = [
        "// Generated by CodeCoreMorph from the bridge's header when the Rust is configured; do",
        "// not edit. The JNI functions of codecoremorph.interop.CcmNative's native methods; the",
        "// bridge include!s it, so cbindgen leaves them out of the header.",
        "use std::ffi::{c_char, c_void};",
        "",
        "use super::jni::{self, jarray, jboolean, jbyte, jbyteArray, jclass, jdouble, jfloat,",
        "    jint, jlong, jobject, jobjectArray, jshort, jvalue, JNIEnv};",
        "",
        "#[allow(clashing_extern_declarations)]",
        'unsafe extern "C" {',
    ]
    for e in bridge.exports:
        decl = ", ".join(f"{_r(cp.name)}: {rust_type(cp.type)}" for cp in e.c_params)
        ret = {"int": " -> i32", "void": "", "long": " -> u64"}[e.returns]
        lines.append(f"    fn {e.name}({decl}){ret};")
    lines.append("}")
    for callback in bridge.callbacks:
        lines += _callback(callback)
    for export in bridge.exports:
        lines += _export(export)
    return "\n".join(lines) + "\n"
