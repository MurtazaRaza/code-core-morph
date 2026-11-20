"""The C JNI glue of a Java façade's bridge for a C++ target (SPEC §11.12, D-92): a JNI function
per native method of ``codecoremorph.interop.CcmNative``, which converts its arguments, calls the
export and copies its out parameters back, and a trampoline per function pointer type of the
bridge, which calls the static ``CcmNative.<type>_call`` with the context object. Compiled into
the bridge library ``<name>_native`` with the fixed part CodeCoreMorph ships
(``codecoremorph_jni.{h,c}``: ``JNI_OnLoad``, the thread's ``JNIEnv``, UTF-8 byte arrays, global
references), from the :class:`~codecoremorph.adapters.java.jni.Bridge` the Java declarations come
from.
"""

from __future__ import annotations

import posixpath

from codecoremorph.adapters.java.jni import Bridge, Callback, Crossing, Export, spell

GENERATED_FILE = "codecoremorph_jni.g.c"
FIXED_FILES = ("codecoremorph_jni.h", "codecoremorph_jni.c")


def _export(e: Export) -> list[str]:
    jni_params = ["JNIEnv *env", "jclass cls"]
    pre: list[str] = []
    args: list[str] = []
    ok: list[str] = []
    post: list[str] = []
    for p in e.params:
        n = p.name
        match p.crossing:
            case Crossing.SCALAR:
                s = p.number()
                jni_params.append(f"{s.jni} {n}")
                args.append(f"{n} == JNI_TRUE" if s.c == "bool" else f"({s.c}){n}")
            case Crossing.STRING:
                jni_params.append(f"jbyteArray {n}")
                pre.append(f"char *c_{n} = ccm_jni_utf8(env, {n});")
                args.append(f"c_{n}")
                post.append(f"free(c_{n});")
            case Crossing.HANDLE:
                jni_params.append(f"jlong {n}")
                args.append(f"({p.c_type})(intptr_t){n}")
            case Crossing.CONTEXT:
                jni_params.append(f"jobject {n}")
                pre.append(f"void *c_{n} = ccm_jni_ref(env, {n});")
                args.append(f"c_{n}")
                if not e.owns_context:
                    post.append(f"ccm_jni_release(c_{n});")
            case Crossing.RELEASE:
                args.append("ccm_jni_release")
            case Crossing.CALLBACK:
                args.append(f"ccm_jni_cb_{p.callback}")
            case Crossing.OUT_SCALAR:
                s = p.number()
                array = "jbooleanArray" if s.c == "bool" else f"{s.jni}Array"
                jni_params.append(f"{array} {n}")
                pre.append(f"{s.c} c_{n} = 0;")
                args.append(f"&c_{n}")
                value = f"c_{n} ? JNI_TRUE : JNI_FALSE" if s.c == "bool" else f"({s.jni})c_{n}"
                ok.append(
                    f"{{ {s.jni} v_{n} = {value}; "
                    f"(*env)->Set{s.stem}ArrayRegion(env, {n}, 0, 1, &v_{n}); }}"
                )
            case Crossing.OUT_STRING:
                jni_params.append(f"jobjectArray {n}")
                pre.append(f"char *c_{n} = NULL;")
                args.append(f"&c_{n}")
                ok.append(f"ccm_jni_put_string(env, {n}, c_{n});")
                ok.append(f"c_{n} = NULL;")
                post.append(f"ccm_free(c_{n});")
            case Crossing.OUT_POINTER:
                jni_params.append(f"jlongArray {n}")
                target = f"{p.c_type} *" if p.handle else "void *"
                pre.append(f"{target}c_{n} = NULL;".replace("* c_", " *c_"))
                args.append(f"&c_{n}")
                ok.append(
                    f"{{ jlong v_{n} = (jlong)(intptr_t)c_{n}; "
                    f"(*env)->SetLongArrayRegion(env, {n}, 0, 1, &v_{n}); }}"
                )
            case Crossing.ARRAY:
                s = p.number()
                array = "jbooleanArray" if s.c == "bool" else f"{s.jni}Array"
                jni_params.append(f"{array} {n}")
                pre += [
                    f"{s.jni} *c_{n} = {n} == NULL ? NULL : "
                    f"(*env)->Get{s.stem}ArrayElements(env, {n}, NULL);",
                    f"int32_t n_{n} = {n} == NULL ? -1 : "
                    f"(int32_t)(*env)->GetArrayLength(env, {n});",
                ]
                args += [f"(const {s.c} *)c_{n}", f"n_{n}"]
                post.append(
                    f"if (c_{n} != NULL) (*env)->Release{s.stem}ArrayElements(env, {n}, c_{n}, "
                    "JNI_ABORT);"
                )
            case Crossing.STRINGS:
                jni_params.append(f"jobjectArray {n}")
                pre += [
                    f"int32_t n_{n} = 0;",
                    f"char **c_{n} = ccm_jni_strings(env, {n}, &n_{n});",
                ]
                args += [f"(const char **)c_{n}", f"n_{n}"]
                post.append(f"ccm_jni_free_strings(c_{n}, n_{n});")
            case Crossing.ARRAY_OUT:
                s = p.number()
                jni_params.append(f"jobjectArray {n}")
                pre += [f"{s.c} *c_{n} = NULL;", f"int32_t n_{n} = 0;"]
                args += [f"&c_{n}", f"&n_{n}"]
                ok += [
                    f"{s.jni}Array a_{n} = (*env)->New{s.stem}Array(env, n_{n});",
                    f"if (a_{n} != NULL) {{",
                    f"    (*env)->Set{s.stem}ArrayRegion(env, a_{n}, 0, n_{n}, "
                    f"(const {s.jni} *)c_{n});",
                    f"    (*env)->SetObjectArrayElement(env, {n}, 0, a_{n});",
                    f"    (*env)->DeleteLocalRef(env, a_{n});",
                    "}",
                ]
                post.append(f"ccm_free(c_{n});")
    jret = {"int": "jint", "void": "void", "long": "jlong"}[e.returns]
    signature = f"JNIEXPORT {jret} JNICALL {e.jni_name}({', '.join(jni_params)}) {{"
    lines = ["", signature, "    (void)cls;"]
    if not pre and not ok and not post:
        lines.append("    (void)env;")
    lines += [f"    {x}" for x in pre]
    call = f"{e.name}({', '.join(args)})"
    if e.returns == "void":
        lines.append(f"    {call};")
    else:
        lines.append(f"    {jret} status = ({jret}){call};")
    if ok:
        lines.append("    if (status == CCM_OK) {")
        lines += [f"        {x}" for x in ok]
        lines.append("    }")
    lines += [f"    {x}" for x in post]
    if e.returns != "void":
        lines.append("    return status;")
    lines.append("}")
    return lines


def _callback(c: Callback) -> list[str]:
    c_params = ", ".join(f"{spell(cp.type)} {cp.name}".replace("* ", "*") for cp in c.c_params)
    context = c.c_params[0].name
    pre: list[str] = []
    jargs = [f"(jobject){context}"]
    ok: list[str] = []
    local: list[str] = []
    for p in c.params:
        n = p.c_names[0]
        match p.crossing:
            case Crossing.SCALAR:
                s = p.number()
                jargs.append(f"{n} ? JNI_TRUE : JNI_FALSE" if s.c == "bool" else f"({s.jni}){n}")
            case Crossing.STRING:
                pre.append(f"jbyteArray j_{n} = ccm_jni_bytes(env, {n});")
                jargs.append(f"j_{n}")
                local.append(f"j_{n}")
            case Crossing.HANDLE:
                jargs.append(f"(jlong)(intptr_t){n}")
            case Crossing.OUT_SCALAR:
                s = p.number()
                array = "jbooleanArray" if s.c == "bool" else f"{s.jni}Array"
                pre.append(f"{array} j_{n} = (*env)->New{s.stem}Array(env, 1);")
                jargs.append(f"j_{n}")
                local.append(f"j_{n}")
                read = f"*{n} = v == JNI_TRUE;" if s.c == "bool" else f"*{n} = ({s.c})v;"
                ok.append(
                    f"{{ {s.jni} v = 0; (*env)->Get{s.stem}ArrayRegion(env, j_{n}, 0, 1, &v); "
                    f"{read} }}"
                )
            case Crossing.OUT_STRING:
                pre.append(f"jobjectArray j_{n} = ccm_jni_string_slot(env);")
                jargs.append(f"j_{n}")
                local.append(f"j_{n}")
                ok.append(f"*{n} = ccm_jni_take_string(env, j_{n});")
            case _:
                pass
    lines = [
        "",
        f"static jmethodID ccm_jni_mid_{c.name};",
        "",
        f"static ccm_status ccm_jni_cb_{c.name}({c_params}) {{",
        "    JNIEnv *env = ccm_jni_env();",
        "    if (env == NULL) return CCM_ERROR;",
        f'    jmethodID mid = ccm_jni_method(env, &ccm_jni_mid_{c.name}, "{c.method}", '
        f'"{c.signature}");',
        "    if (mid == NULL) return ccm_jni_pending(env);",
        *[f"    {x}" for x in pre],
        "    jint status = (*env)->CallStaticIntMethod(env, ccm_jni_native, mid, "
        f"{', '.join(jargs)});",
        "    if ((*env)->ExceptionCheck(env)) status = ccm_jni_pending(env);",
    ]
    if ok:
        lines.append("    if (status == CCM_OK) {")
        lines += [f"        {x}" for x in ok]
        lines.append("    }")
    lines += [f"    if ({v} != NULL) (*env)->DeleteLocalRef(env, {v});" for v in local]
    lines += ["    return (ccm_status)status;", "}"]
    return lines


def c_glue(bridge: Bridge) -> str:
    """``codecoremorph_jni.g.c`` for the bridge: it includes each bridge header by its file name
    (they are all in the bridge's directory)."""
    headers = sorted({posixpath.basename(h.replace("\\", "/")) for h in bridge.headers})
    lines = [
        "/* Generated by CodeCoreMorph from the bridge headers when the C++ is configured; do not",
        " * edit. The JNI functions of codecoremorph.interop.CcmNative's native methods. */",
        "#include <stdint.h>",
        "#include <stdlib.h>",
        "",
        '#include "codecoremorph_jni.h"',
        *[f'#include "{h}"' for h in headers],
    ]
    for callback in bridge.callbacks:
        lines += _callback(callback)
    for export in bridge.exports:
        lines += _export(export)
    return "\n".join(lines) + "\n"
