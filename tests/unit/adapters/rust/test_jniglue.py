"""The Rust JNI glue of a Java façade's bridge for a Rust target (SPEC §11.12, D-92)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from tests.unit.adapters.java.test_jni import BOOL, INT32, VOID, callback, cart_bridge, fn, ptr

import codecoremorph.adapters.rust.java_runtime as java_runtime
from codecoremorph.adapters.java.jni import read_bridge
from codecoremorph.adapters.rust.jniglue import rust_glue

JNI_RS = Path(java_runtime.__file__).with_name("jni.rs")
NUMBERS = ("int8_t", "uint8_t", "int16_t", "uint16_t", "int32_t", "uint32_t", "int64_t")
NUMBERS += ("uint64_t", "float", "double")


def glue() -> str:
    return rust_glue(read_bridge(cart_bridge()))


def every_scalar_bridge() -> dict[str, dict[str, Any]]:
    """Each number type and bool by value, out, and in arrays, in an export and a call into
    Java."""
    types = [{"k": "float" if c in ("float", "double") else "int", "name": c} for c in NUMBERS]
    types.append(BOOL)
    values = [(f"v{i}", t) for i, t in enumerate(types)]
    outs = [(f"r{i}", ptr(t)) for i, t in enumerate(types)]
    arrays: list[tuple[str, dict[str, Any]]] = []
    array_outs: list[tuple[str, dict[str, Any]]] = []
    for i, t in enumerate(types):
        arrays += [(f"a{i}_data", ptr(t, const=True)), (f"a{i}_count", INT32)]
        array_outs += [(f"o{i}_data", ptr(ptr(t))), (f"o{i}_count", ptr(INT32))]
    return {
        "rust/x/include/x/bridge.h": {
            "handles": [],
            "callbacks": [fn("x_each_fn", [("context", ptr(VOID)), *values, *outs])],
            "functions": [
                fn("x_each", [*values, *outs]),
                fn("x_arrays", [*arrays, *array_outs]),
                fn("x_call", [("context", ptr(VOID)), ("each", callback("x_each_fn"))]),
            ],
        }
    }


def _jni_rs_names() -> set[str]:
    """What the fixed part of the glue defines or re-exports from bindgen's JNI types."""
    text = JNI_RS.read_text(encoding="utf-8")
    item = r'pub (?:unsafe )?(?:extern "C" )?(?:fn|const|struct|static|type) (\w+)'
    names = set(re.findall(item, text))
    use = re.search(r"pub use super::jni_sys::\{(.*?)\};", text, re.S)
    assert use is not None
    names |= {n.strip() for n in use.group(1).split(",") if n.strip()}
    for m in re.finditer(r"^numbers!\((.*?)\);", text, re.S | re.M):
        names |= {n.strip() for n in m.group(1).split(",")[1:]}
    return names


def test_the_glue_names_only_what_it_imports_and_jni_rs_defines() -> None:
    # The M14 benchmark's first Rust units found the glue naming jshort and jdouble, which it
    # didn't import, and zeroing an f64 with an integer literal: the agent crossed every double
    # as its bits instead.
    bridge = read_bridge(every_scalar_bridge())
    assert not bridge.problems
    text = rust_glue(bridge)
    use = re.search(r"use super::jni::\{self, (.*?)\};", text, re.S)
    assert use is not None
    imported = {n.strip() for n in use.group(1).split(",")}
    body = text[use.end() :]
    defined = _jni_rs_names()
    assert set(re.findall(r"\b(j[a-z]+(?:Array)?)\b", body)) - {"jni"} <= imported
    assert imported <= defined
    assert set(re.findall(r"\bjni::(\w+)", body)) <= defined
    assert "let mut c_r8: f32 = 0.0;" in text
    assert "let mut c_r9: f64 = 0.0;" in text
    assert "let mut c_r10: bool = false;" in text
    assert "let mut c_r2: i16 = 0;" in text
    assert "v9: jdouble" in text
    assert "jvalue { s: v3 as jshort }" in text
    assert "jvalue { d: v9 as jdouble }" in text
    assert "jvalue { z: v10 as jboolean }" in text


def test_the_exports_are_declared_with_plain_pointers() -> None:
    text = glue()
    assert "#[allow(clashing_extern_declarations)]" in text
    assert "    fn shop_Cart_new(currency: *const c_char, result: *mut *mut c_void) -> i32;" in text
    assert "    fn shop_Cart_release(handle: *mut c_void);" in text
    assert "    fn shop_Cart_key(handle: *const c_void) -> u64;" in text
    # A Rust keyword a C++ header might name is escaped; cbindgen's `this` isn't one.
    assert "fn shop_Cart_quantity(self_: *const c_void, n: i32, result: *mut i32) -> i32;" in text
    assert 'release: Option<unsafe extern "C" fn(*mut c_void)>, apply: *const c_void' in text


def test_each_native_method_is_a_jni_function_over_the_export() -> None:
    text = glue()
    assert (
        '#[unsafe(no_mangle)]\npub unsafe extern "system" fn '
        "Java_codecoremorph_interop_CcmNative_shop_1Cart_1new_1raw(env: *mut JNIEnv, "
        "_cls: jclass, currency: jbyteArray, result: jarray) -> jint {"
    ) in text
    new = text.split("shop_1Cart_1new_1raw", 1)[1].split("\n}\n", 1)[0]
    assert "let c_currency = jni::utf8(env, currency);" in new
    assert "let status = shop_Cart_new(c_currency.as_ptr(), &mut c_result);" in new
    assert new.index("if status == jni::CCM_OK {") < new.index("jni::put_long")
    managed = text.split("shop_1PriceRule_1from_1managed", 1)[1].split("\n}\n", 1)[0]
    assert "Some(jni::release), cb_shop_PriceRule_apply_fn as *const c_void" in managed
    assert "jni::release(c_context);" not in managed
    count = text.split("shop_1Cart_1count(", 1)[1].split("\n}\n", 1)[0]
    assert "jni::release(c_context);" in count


def test_arrays_and_unsigned_elements_cast_to_the_jni_helpers() -> None:
    text = glue()
    checkout = text.split("shop_1Cart_1checkout_1raw", 1)[1].split("\n}\n", 1)[0]
    assert "let c_keys = jni::strings(env, keys);" in checkout
    assert "c_keys.as_ptr(), c_keys.count()" in checkout
    assert "let (c_values, n_values) = jni::int_elements(env, values);" in checkout
    assert "c_values.as_ptr_or_null().cast(), n_values" in checkout
    assert "let mut c_result: *mut u8 = std::ptr::null_mut();" in checkout
    assert "jni::put_byte_array(env, result, c_result.cast(), n_result);" in checkout
    assert "jni::free(c_result.cast());" in checkout
    assert "jni::put_boolean(env, found, c_found);" in checkout
    assert "flag != 0" in checkout


def test_a_trampoline_calls_the_static_method() -> None:
    text = glue()
    assert (
        "static MID_shop_PriceRule_apply_fn: jni::Method = jni::Method::new("
        'c"shop_PriceRule_apply_fn_call", c"(Ljava/lang/Object;[BIJ[J[[B)I");'
    ) in text
    assert (
        'unsafe extern "C" fn cb_shop_PriceRule_apply_fn(context: *mut c_void, '
        "item: *const c_char, category: i32, price_cents: i64, result_cents: *mut i64, "
        "result_currency: *mut *mut c_char) -> i32 {"
    ) in text
    assert "jvalue { j: price_cents as jlong }" in text
    assert "*result_cents = jni::get_long(env, j_result_cents) as _;" in text
    assert "*result_currency = jni::take_string(env, j_result_currency);" in text
    assert "jni::delete_local(env, j_item);" in text
    assert "*result = jni::get_boolean(env, j_result);" in text
