"""How a Java façade's bridge crosses from Java, and its Java declarations (SPEC §11.12, D-92),
from the descriptions the C++ index helper writes for the bridge headers."""

from __future__ import annotations

from typing import Any

from codecoremorph.adapters.java.jni import (
    Crossing,
    initial_declarations,
    java_declarations,
    mangle,
    read_bridge,
)

INT32: dict[str, Any] = {"k": "int", "name": "int32_t"}
INT64: dict[str, Any] = {"k": "int", "name": "int64_t"}
UINT8: dict[str, Any] = {"k": "int", "name": "uint8_t"}
UINT64: dict[str, Any] = {"k": "int", "name": "uint64_t"}
BOOL: dict[str, Any] = {"k": "bool"}
DOUBLE: dict[str, Any] = {"k": "float", "name": "double"}
VOID: dict[str, Any] = {"k": "void"}
CHAR: dict[str, Any] = {"k": "char"}
RELEASE: dict[str, Any] = {"k": "release"}


def ptr(to: dict[str, Any], *, const: bool = False) -> dict[str, Any]:
    return {"k": "ptr", "const": const, "to": to}


STR_IN = ptr(CHAR, const=True)
STR_OUT = ptr(ptr(CHAR))


def handle(name: str) -> dict[str, Any]:
    return {"k": "handle", "name": name}


def callback(name: str) -> dict[str, Any]:
    return {"k": "callback", "name": name}


def fn(name: str, params: list[tuple[str, dict[str, Any]]], returns: dict[str, Any] = INT32):
    return {
        "name": name,
        "line": 9,
        "returns": returns,
        "params": [{"name": n, "type": t} for n, t in params],
    }


def handle_exports(name: str) -> list[dict[str, Any]]:
    return [
        fn(f"{name}_release", [("handle", ptr(handle(name)))], VOID),
        fn(f"{name}_key", [("handle", ptr(handle(name), const=True))], UINT64),
    ]


def cart_bridge() -> dict[str, dict[str, Any]]:
    """The spike's Cart, Money and PriceRule bridge, in short."""
    return {
        "cpp/bridge/cart.h": {
            "handles": ["shop_Cart", "shop_PriceRule"],
            "callbacks": [
                fn(
                    "shop_PriceRule_apply_fn",
                    [
                        ("context", ptr(VOID)),
                        ("item", STR_IN),
                        ("category", INT32),
                        ("price_cents", INT64),
                        ("result_cents", ptr(INT64)),
                        ("result_currency", STR_OUT),
                    ],
                ),
                fn(
                    "shop_Cart_accepts_fn",
                    [("context", ptr(VOID)), ("item", STR_IN), ("result", ptr(BOOL))],
                ),
            ],
            "functions": [
                *handle_exports("shop_Cart"),
                *handle_exports("shop_PriceRule"),
                fn(
                    "shop_Cart_new",
                    [("currency", STR_IN), ("result", ptr(ptr(handle("shop_Cart"))))],
                ),
                fn(
                    "shop_Cart_total",
                    [
                        ("this", ptr(handle("shop_Cart"), const=True)),
                        ("result_cents", ptr(INT64)),
                        ("result_currency", STR_OUT),
                    ],
                ),
                fn(
                    "shop_Cart_quantity",
                    [
                        ("self", ptr(handle("shop_Cart"), const=True)),
                        ("n", INT32),
                        ("result", ptr(INT32)),
                    ],
                ),
                fn(
                    "shop_PriceRule_from_managed",
                    [
                        ("context", ptr(VOID)),
                        ("release", RELEASE),
                        ("apply", callback("shop_PriceRule_apply_fn")),
                        ("result", ptr(ptr(handle("shop_PriceRule")))),
                    ],
                ),
                fn(
                    "shop_Cart_count",
                    [
                        ("self", ptr(handle("shop_Cart"), const=True)),
                        ("context", ptr(VOID)),
                        ("accepts", callback("shop_Cart_accepts_fn")),
                        ("result", ptr(INT32)),
                    ],
                ),
                fn(
                    "shop_Cart_checkout",
                    [
                        ("self", ptr(handle("shop_Cart"))),
                        ("keys_data", ptr(STR_IN, const=True)),
                        ("keys_count", INT32),
                        ("values_data", ptr(INT32, const=True)),
                        ("values_count", INT32),
                        ("result_data", ptr(ptr(UINT8))),
                        ("result_count", ptr(INT32)),
                        ("found", ptr(BOOL)),
                        ("ratio", DOUBLE),
                        ("flag", BOOL),
                        ("director", ptr(ptr(VOID))),
                    ],
                ),
            ],
        }
    }


def test_each_parameter_crosses_by_the_bridge_conventions() -> None:
    bridge = read_bridge(cart_bridge())
    assert bridge.problems == []
    by_name = {e.name: e for e in bridge.exports}
    checkout = by_name["shop_Cart_checkout"]
    assert [(p.name, p.crossing) for p in checkout.params] == [
        ("self", Crossing.HANDLE),
        ("keys", Crossing.STRINGS),
        ("values", Crossing.ARRAY),
        ("result", Crossing.ARRAY_OUT),
        ("found", Crossing.OUT_SCALAR),
        ("ratio", Crossing.SCALAR),
        ("flag", Crossing.SCALAR),
        ("director", Crossing.OUT_POINTER),
    ]
    # An array stands for its data and its count; the C call keeps both.
    assert checkout.params[2].c_names == ("values_data", "values_count")
    assert len(checkout.c_params) == 11
    managed = by_name["shop_PriceRule_from_managed"]
    assert [p.crossing for p in managed.params] == [
        Crossing.CONTEXT,
        Crossing.RELEASE,
        Crossing.CALLBACK,
        Crossing.OUT_POINTER,
    ]
    assert managed.owns_context
    assert not by_name["shop_Cart_count"].owns_context
    assert by_name["shop_Cart_release"].returns == "void"
    assert by_name["shop_Cart_key"].returns == "long"


def test_the_java_declarations_are_natives_with_wrappers_where_strings_convert() -> None:
    text = java_declarations(read_bridge(cart_bridge()), "shop_native")
    assert text.isascii()
    assert "package codecoremorph.interop;" in text
    assert 'public static final String LIBRARY = "shop_native";' in text
    assert "System.loadLibrary(LIBRARY);" in text
    assert "public static native void shop_Cart_release(long handle);" in text
    assert "public static native long shop_Cart_key(long handle);" in text
    # Nothing converts: the native method is the façades'.
    assert "public static native int shop_Cart_quantity(long self, int n, int[] result);" in text
    # Strings convert through a wrapper over the raw method.
    assert "private static native int shop_Cart_new_raw(byte[] currency, long[] result);" in text
    assert "public static int shop_Cart_new(String currency, long[] result) {" in text
    assert "return shop_Cart_new_raw(CcmInterop.utf8(currency), result);" in text
    # A Java keyword gets a trailing underscore (a Rust bridge's `this`).
    assert (
        "private static native int shop_Cart_total_raw(long this_, long[] result_cents, "
        "byte[][] result_currency);" in text
    )
    assert "byte[][] result_currency_utf8 = new byte[1][];" in text
    assert "result_currency[0] = CcmInterop.string(result_currency_utf8[0]);" in text
    # No parameter for the release or the function pointer: the glue supplies them.
    assert (
        "public static native int shop_PriceRule_from_managed(Object context, long[] result);"
        in text
    )
    assert (
        "private static native int shop_Cart_checkout_raw(long self, byte[][] keys, int[] values, "
        "byte[][] result, boolean[] found, double ratio, boolean flag, long[] director);"
    ) in text
    assert "shop_Cart_checkout_raw(self, CcmInterop.utf8s(keys), values," in text


def test_a_call_into_java_is_an_interface_and_a_static_method_that_catches() -> None:
    text = java_declarations(read_bridge(cart_bridge()), "shop_native")
    assert "public interface shop_PriceRule_apply_fn {" in text
    assert (
        "int call(String item, int category, long price_cents, long[] result_cents, "
        "String[] result_currency);"
    ) in text
    assert (
        "private static int shop_PriceRule_apply_fn_call(Object context, byte[] item, "
        "int category, long price_cents, long[] result_cents, byte[][] result_currency) {"
    ) in text
    assert (
        "int status = ((shop_PriceRule_apply_fn) context).call(CcmInterop.string(item), category, "
        "price_cents, result_cents, result_currency_text);"
    ) in text
    assert "result_currency[0] = CcmInterop.utf8(result_currency_text[0]);" in text
    assert "return CcmInterop.managed(thrown);" in text
    bridge = read_bridge(cart_bridge())
    apply = next(c for c in bridge.callbacks if c.name == "shop_PriceRule_apply_fn")
    assert apply.signature == "(Ljava/lang/Object;[BIJ[J[[B)I"
    assert apply.method == "shop_PriceRule_apply_fn_call"


def test_the_declarations_add_no_warning_suppression() -> None:
    """The declarations are regenerated at each unit's configure, and I-7 counts suppressions
    on everything the unit's attempt changed: a unit that adds a callback would add one."""
    from codecoremorph.invariants.checks import SUPPRESSIONS

    text = java_declarations(read_bridge(cart_bridge()), "shop_native")
    assert not [name for name, pattern in SUPPRESSIONS.items() if pattern.search(text)]


def test_jni_names_escape_underscores() -> None:
    bridge = read_bridge(cart_bridge())
    by_name = {e.name: e for e in bridge.exports}
    assert by_name["shop_Cart_new"].jni_name == (
        "Java_codecoremorph_interop_CcmNative_shop_1Cart_1new_1raw"
    )
    assert by_name["shop_Cart_quantity"].jni_name == (
        "Java_codecoremorph_interop_CcmNative_shop_1Cart_1quantity"
    )
    assert mangle("a_b") == "a_1b"
    assert mangle("café") == "caf_000e9"


def test_what_the_bridge_cant_take_is_a_problem_at_its_line() -> None:
    headers = {
        "cpp/bridge/bad.h": {
            "handles": ["shop_Bad"],
            "callbacks": [fn("shop_Bad_cb_fn", [("item", STR_IN)])],
            "functions": [
                fn("shop_Bad_text", [("text", ptr(CHAR))]),
                fn("shop_Bad_struct", [("value", {"k": "unknown", "spelling": "struct pair"})]),
                fn("shop_Bad_returns", [], DOUBLE),
                fn("shop_Bad_release", [("handle", ptr(handle("shop_Bad")))], VOID),
            ],
        }
    }
    bridge = read_bridge(headers)
    messages = [p.message for p in bridge.problems]
    assert all(p.header == "cpp/bridge/bad.h" for p in bridge.problems)
    assert any("a string in is const char *" in m for m in messages)
    assert any("struct pair" in m for m in messages)
    assert any("returns double" in m for m in messages)
    assert any("takes its void *context first" in m for m in messages)
    assert any("has no shop_Bad_key" in m and "cbindgen expands no macro" in m for m in messages)
    names = {e.name for e in bridge.exports}
    assert "shop_Bad_text" not in names
    assert "shop_Bad_release" in names


def test_the_initial_declarations_name_the_library() -> None:
    text = initial_declarations("shop_rs")
    assert 'public static final String LIBRARY = "shop_rs";' in text
    assert "static native" not in text
