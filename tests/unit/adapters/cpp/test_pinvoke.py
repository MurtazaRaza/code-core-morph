"""The C# declarations of a C# façade's bridge (SPEC §11.9, D-78), from the descriptions the C++
index helper writes for the bridge headers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from codecoremorph.adapters.cpp.pinvoke import BridgeProblem, csharp_declarations

INT32: dict[str, Any] = {"k": "int", "name": "int32_t"}
STATUS = INT32
VOID: dict[str, Any] = {"k": "void"}


def ptr(to: dict[str, Any], *, const: bool = False) -> dict[str, Any]:
    return {"k": "ptr", "const": const, "to": to}


CHAR: dict[str, Any] = {"k": "char"}
STR_IN = ptr(CHAR, const=True)
STR_OUT = ptr(ptr(CHAR))
DECIMAL: dict[str, Any] = {"k": "decimal"}


def handle(name: str) -> dict[str, Any]:
    return {"k": "handle", "name": name}


def fn(name: str, params: list[tuple[str, dict[str, Any]]], returns: dict[str, Any] = STATUS):
    return {
        "name": name,
        "line": 7,
        "returns": returns,
        "params": [{"name": n, "type": t} for n, t in params],
    }


def handle_exports(name: str) -> list[dict[str, Any]]:
    return [
        fn(f"{name}_release", [("handle", ptr(handle(name)))], VOID),
        fn(
            f"{name}_key",
            [("handle", ptr(handle(name), const=True))],
            {"k": "int", "name": "uint64_t"},
        ),
    ]


def test_scalars_handles_and_outs_are_plain_externs() -> None:
    headers = {
        "cpp/bridge/item.h": {
            "handles": ["shop_Item"],
            "callbacks": [],
            "functions": [
                *handle_exports("shop_Item"),
                fn(
                    "shop_Item_price",
                    [
                        ("self", ptr(handle("shop_Item"), const=True)),
                        ("result_amount", ptr(DECIMAL)),
                        ("result_currency", STR_OUT),
                        ("found", ptr({"k": "bool"})),
                        ("count", ptr(INT32)),
                        ("result", ptr(ptr(handle("shop_Item")))),
                    ],
                ),
            ],
        }
    }
    text, problems = csharp_declarations(headers, "shop_native")
    assert problems == []
    assert 'internal const string Library = "shop_native";' in text
    assert (
        "internal static extern int shop_Item_price(shop_ItemHandle self, out CcmDecimal "
        "result_amount, out IntPtr result_currency, [MarshalAs(UnmanagedType.U1)] out bool found, "
        "out int count, out shop_ItemHandle result);"
    ) in text
    # The release takes the raw pointer: ReleaseHandle can't marshal the handle being released.
    assert "internal static extern void shop_Item_release(IntPtr handle);" in text
    assert "internal static extern ulong shop_Item_key(shop_ItemHandle handle);" in text
    assert "internal sealed class shop_ItemHandle : SafeHandle, ICcmHandle" in text
    assert "CcmNative.shop_Item_release(handle);" in text


def test_strings_cross_as_utf8_bytes_through_a_wrapper() -> None:
    headers = {
        "cpp/bridge/cart.h": {
            "handles": [],
            "callbacks": [],
            "functions": [
                fn(
                    "shop_Cart_new",
                    [
                        ("currency", STR_IN),
                        ("stock_keys", ptr(STR_IN, const=True)),
                        ("stock_values", ptr(INT32, const=True)),
                        ("stock_count", INT32),
                        ("result", ptr(ptr({"k": "void"}))),
                    ],
                )
            ],
        }
    }
    text, problems = csharp_declarations(headers, "shop_native")
    assert problems == []
    assert 'ExactSpelling = true, EntryPoint = "shop_Cart_new")]' in text
    assert (
        "private static extern int shop_Cart_new_raw(byte[] currency, IntPtr[] stock_keys, "
        "int[] stock_values, int stock_count, out IntPtr result);"
    ) in text
    assert (
        "internal static int shop_Cart_new(string currency, string[] stock_keys, "
        "int[] stock_values, int stock_count, out IntPtr result)"
    ) in text
    assert "IntPtr[] stock_keys_utf8 = CcmInterop.NewStrings(stock_keys);" in text
    assert (
        "return shop_Cart_new_raw(CcmInterop.Utf8Z(currency), stock_keys_utf8, stock_values, "
        "stock_count, out result);"
    ) in text
    assert "CcmInterop.FreeStrings(stock_keys_utf8);" in text
    assert "LPUTF8Str" not in text  # netstandard2.0 doesn't have it


def test_arrays_out_are_a_pointer_and_a_count_the_facade_takes() -> None:
    """A byte[] the C# returns crosses as one malloc'd copy and its length (D-82), not an
    export per element."""
    byte: dict[str, Any] = {"k": "int", "name": "uint8_t"}
    headers = {
        "cpp/bridge/secret_box.h": {
            "handles": [],
            "callbacks": [],
            "functions": [
                fn(
                    "sodium_SecretBox_create",
                    [
                        ("message_data", ptr(byte, const=True)),
                        ("message_count", INT32),
                        ("result_data", ptr(ptr(byte))),
                        ("result_count", ptr(INT32)),
                    ],
                )
            ],
        }
    }
    text, problems = csharp_declarations(headers, "sodium_native")
    assert problems == []
    assert (
        "internal static extern int sodium_SecretBox_create(byte[] message_data, int "
        "message_count, out IntPtr result_data, out int result_count);"
    ) in text


def test_function_pointers_become_delegates() -> None:
    headers = {
        "cpp/bridge/discounts.h": {
            "handles": [],
            "callbacks": [
                {
                    "name": "shop_IDiscountPolicy_discount_fn",
                    "line": 3,
                    "returns": STATUS,
                    "params": [
                        {"name": "context", "type": ptr(VOID)},
                        {"name": "line", "type": ptr(handle("shop_CartLine"))},
                        {"name": "sku", "type": STR_IN},
                        {"name": "result_amount", "type": ptr(DECIMAL)},
                        {"name": "result_currency", "type": STR_OUT},
                        {"name": "matched", "type": ptr({"k": "bool"})},
                    ],
                }
            ],
            "functions": [
                fn(
                    "shop_IDiscountPolicy_from_managed",
                    [
                        ("context", ptr(VOID)),
                        ("release", {"k": "release"}),
                        ("discount", {"k": "callback", "name": "shop_IDiscountPolicy_discount_fn"}),
                    ],
                )
            ],
        }
    }
    text, problems = csharp_declarations(headers, "shop_native")
    assert problems == []
    assert "[UnmanagedFunctionPointer(CallingConvention.Cdecl)]" in text
    assert (
        "internal delegate int shop_IDiscountPolicy_discount_fn(IntPtr context, IntPtr line, "
        "IntPtr sku, out CcmDecimal result_amount, out IntPtr result_currency, "
        "[MarshalAs(UnmanagedType.U1)] out bool matched);"
    ) in text
    assert (
        "internal static extern int shop_IDiscountPolicy_from_managed(IntPtr context, "
        "CcmReleaseFn release, shop_IDiscountPolicy_discount_fn discount);"
    ) in text


def test_what_a_bridge_cannot_take_is_refused_at_its_line() -> None:
    headers = {
        "cpp/bridge/bad.h": {
            # Money: an opaque type no export passes (cbindgen declares one a constant names,
            # D-84), which is no handle and needs no exports.
            "handles": ["shop_Thing", "Money"],
            "callbacks": [],
            "functions": [
                fn("size_of", [("n", {"k": "unknown", "spelling": "size_t"})]),
                fn("name_of", [("out", ptr(CHAR))]),
                fn("value", [], {"k": "decimal"}),
                fn("ok", [("n", INT32)]),
                fn("thing", [("result", ptr(ptr(handle("shop_Thing"))))]),
            ],
        }
    }
    text, problems = csharp_declarations(headers, "x_native")
    assert problems == [
        BridgeProblem(
            "cpp/bridge/bad.h", 7, "size_of: n is of size_t, which a bridge doesn't take"
        ),
        BridgeProblem(
            "cpp/bridge/bad.h",
            7,
            "name_of: out is a char * that isn't const: a string in is const char *",
        ),
        BridgeProblem(
            "cpp/bridge/bad.h",
            7,
            "value: returns ccm_decimal: an export returns a status (ccm_status) or nothing",
        ),
        BridgeProblem(
            "cpp/bridge/bad.h",
            1,
            "the handle type shop_Thing has no shop_Thing_release or shop_Thing_key: declare it "
            "with CCM_HANDLE",
        ),
    ]
    assert "internal static extern int ok(int n);" in text
    assert "size_of" not in text
    assert "MoneyHandle" not in text


def test_the_output_is_stable() -> None:
    headers = {
        "cpp/bridge/b.h": {"handles": [], "callbacks": [], "functions": [fn("b", [])]},
        "cpp/bridge/a.h": {"handles": [], "callbacks": [], "functions": [fn("a", [])]},
    }
    first, _ = csharp_declarations(headers, "x_native")
    second, _ = csharp_declarations(dict(reversed(list(headers.items()))), "x_native")
    assert first == second
    assert first.index(" a()") < first.index(" b()")  # by header


def test_a_programs_export_takes_the_arguments_as_strings() -> None:
    """The export a C# program's Main calls (cpp-csharp-program writes it): Main passes its
    arguments and gets the exit code."""
    headers = {
        "cpp/bridge/shop_cli.h": {
            "handles": [],
            "callbacks": [],
            "functions": [
                fn(
                    "shop_shop_cli_main",
                    [
                        ("args_data", ptr(STR_IN)),
                        ("args_count", INT32),
                        ("result", ptr(INT32)),
                    ],
                )
            ],
        }
    }
    text, problems = csharp_declarations(headers, "shop_native")
    assert problems == []
    assert (
        "internal static int shop_shop_cli_main(string[] args_data, int args_count, out int result)"
    ) in text


def test_csharp_keywords_as_parameter_names_are_escaped() -> None:
    """A Rust bridge may name a parameter `this`, `object` or `event` (D-84); in C# they are
    reserved words, escaped with @."""
    headers = {
        "rust/zoo_rs/include/zoo_rs/bridge.h": {
            "handles": ["zoo_Pen"],
            "callbacks": [
                {
                    "name": "zoo_Pen_visit_fn",
                    "line": 3,
                    "returns": STATUS,
                    "params": [
                        {"name": "context", "type": ptr(VOID)},
                        {"name": "object", "type": ptr(handle("zoo_Pen"))},
                    ],
                }
            ],
            "functions": [
                *handle_exports("zoo_Pen"),
                fn(
                    "zoo_Pen_name",
                    [("this", ptr(handle("zoo_Pen"), const=True)), ("string", STR_OUT)],
                ),
            ],
        }
    }
    text, problems = csharp_declarations(
        headers, "zoo_rs", "rust/zoo_rs/include/zoo_rs/bridge.h", "Rust"
    )
    assert problems == []
    assert "zoo_Pen_name(zoo_PenHandle @this, out IntPtr @string);" in text
    assert "delegate int zoo_Pen_visit_fn(IntPtr context, IntPtr @object);" in text
    assert "(rust/zoo_rs/include/zoo_rs/bridge.h) when the Rust is" in text


def test_a_hierarchy_with_directors_holds_its_csharp_objects_for_each_call() -> None:
    """D-87: a handle type a director export makes carries its director, and every export that
    takes one holds a director's C# object for the call; other handles stay plain."""
    fee_fn = {
        "name": "fees_FeeRule_fee_fn",
        "line": 3,
        "returns": STATUS,
        "params": [
            {"name": "context", "type": ptr(VOID)},
            {"name": "amount", "type": DECIMAL},
            {"name": "result", "type": ptr(DECIMAL)},
        ],
    }
    rule = handle("fees_FeeRule")
    schedule = handle("fees_Schedule")
    headers = {
        "cpp/bridge/fees.h": {
            "handles": ["fees_FeeRule", "fees_Schedule"],
            "callbacks": [fee_fn],
            "functions": [
                *handle_exports("fees_FeeRule"),
                *handle_exports("fees_Schedule"),
                fn(
                    "fees_FeeRule_new_director",
                    [
                        ("name", STR_IN),
                        ("context", ptr(VOID)),
                        ("release", {"k": "release"}),
                        ("fee", {"k": "callback", "name": "fees_FeeRule_fee_fn"}),
                        ("result_director", ptr(ptr(VOID))),
                        ("result", ptr(ptr(rule))),
                    ],
                ),
                fn(
                    "fees_FeeRule_charge",
                    [
                        ("self", ptr(rule, const=True)),
                        ("amount", DECIMAL),
                        ("result", ptr(DECIMAL)),
                    ],
                ),
                fn("fees_Schedule_add", [("self", ptr(schedule)), ("rule", ptr(rule))]),
                fn(
                    "fees_Schedule_count",
                    [("self", ptr(schedule, const=True)), ("result", ptr(INT32))],
                ),
            ],
        }
    }
    text, problems = csharp_declarations(headers, "fees_native")
    assert problems == []
    assert "internal sealed class fees_FeeRuleHandle : SafeHandle, ICcmDirected" in text
    assert "public CcmDirector Director { get; set; }" in text
    assert "internal sealed class fees_ScheduleHandle : SafeHandle, ICcmHandle" in text
    # A director's object is held while native code may take its handle, self included.
    assert (
        "        internal static int fees_Schedule_add(fees_ScheduleHandle self, "
        "fees_FeeRuleHandle rule)\n"
        "        {\n"
        "            using (CcmDirectors.Passing(rule))\n"
        "            {\n"
        "                return fees_Schedule_add_raw(self, rule);\n"
        "            }\n"
        "        }\n"
    ) in text
    assert "using (CcmDirectors.Passing(self))\n" in text  # fees_FeeRule_charge
    # Strings and a director's handle out: the wrapper converts, and holds nothing.
    assert (
        "return fees_FeeRule_new_director_raw(CcmInterop.Utf8Z(name), context, release, fee, "
        "out result_director, out result);"
    ) in text
    assert "Passing(result)" not in text
    # A handle's identity and a plain handle's exports take no wrapper.
    assert "internal static extern ulong fees_FeeRule_key(fees_FeeRuleHandle handle);" in text
    assert (
        "internal static extern int fees_Schedule_count(fees_ScheduleHandle self, out int result);"
    ) in text


def test_a_rust_bridges_header_problems_name_the_fix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cbindgen writes a C# port's Rust bridge header from the agent's Rust: what libclang can't
    read in it comes back with what to change in the Rust (D-89, as for a C port); a C++
    bridge's own header gets no such hint."""
    from codecoremorph.adapters.base import AdapterContext
    from codecoremorph.adapters.cpp import CppAdapter
    from codecoremorph.core.models import Diagnostic, DiagnosticOrigin, LanguageConfig, Severity
    from codecoremorph.sandbox.native import NativeExecutor

    problem = Diagnostic(
        file_path="shop_rs/include/shop/bridge.h",
        line=9,
        severity=Severity.ERROR,
        message="unknown type name 'Option'",
        origin=DiagnosticOrigin.COMPILER,
    )

    def read(*_: object) -> tuple[dict[str, Any], list[Diagnostic]]:
        return {}, [problem]

    monkeypatch.setattr(CppAdapter, "_bridge_declarations", read)
    ctx = AdapterContext(
        worktree=tmp_path,
        build_dir=tmp_path / "build",
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
        executor=NativeExecutor(log_dir=tmp_path / "exec"),
        lang=LanguageConfig(language="csharp", standard="csharp12"),
    )
    header = "shop_rs/include/shop/bridge.h"
    rust = CppAdapter().write_csharp_declarations(ctx, [header], [], "shop", [], header, "Rust")
    assert [d.line for d in rust] == [9]
    assert "not through a type alias" in rust[0].message
    cpp = CppAdapter().write_csharp_declarations(ctx, [header], [], "shop", [], header, "C++")
    assert [d.message for d in cpp] == ["unknown type name 'Option'"]
