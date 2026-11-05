"""The Rust symbol index (D-58): item paths from a crate's sources."""

from __future__ import annotations

import pytest

from codecoremorph.adapters.rust.symbols import mask, module_path, scan_crate

LIB = """\
//! The crate. `fn not_an_item()` in a doc comment.
#![deny(unsafe_code)]

#[allow(unsafe_code)]
pub mod bridge;
pub mod rational;

/* a /* nested */ comment: struct Ghost; */
pub const LIMIT: usize = 10;
static mut COUNTER: u32 = 0;
pub type Pair = (i32, i32);

#[macro_export]
macro_rules! square {
    ($x:expr) => { $x * $x };
}

pub mod inline {
    pub fn helper() -> &'static str { "fn fake() {}" }
}
"""

RATIONAL = """\
use std::fmt;
use std::ops::Add;

/// A fraction.
#[derive(Clone, Copy)]
pub struct Rational { num: i64, den: i64 }

pub enum Error { ZeroDenominator, DivisionByZero = 2, Other(String), Named { code: i32 } }

pub trait Number {
    fn value(&self) -> f64;
    const ZERO: i64;
}

impl Rational {
    pub fn new(num: i64, den: i64) -> Result<Rational, Error> {
        fn inner() {}  // a function inside a body is not an item of the crate
        let c = '}';
        let s = r#"impl Ghost { fn ghost() {} }"#;
        Ok(Rational { num, den })
    }
    pub(crate) const fn num(self) -> i64 { self.num }
}

impl Add for Rational {
    type Output = Rational;
    fn add(self, other: Rational) -> Rational { other }
}

impl<'a, T: fmt::Display> fmt::Display for Wrapper<'a, T> where T: Clone {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result { Ok(()) }
}
"""

BRIDGE = """\
use crate::rational::Rational;

#[no_mangle]
pub unsafe extern "C" fn calc_rs_rational_new(num: i64, den: i64) -> i64 { 0 }

impl Rational {
    pub fn from_c(value: i64) -> Rational { todo!() }
}
"""


def _crate() -> dict[str, str]:
    files = {
        "src/lib.rs": LIB,
        "src/rational.rs": RATIONAL,
        "src/bridge/mod.rs": BRIDGE,
        "src/main.rs": "fn main() {}",
        "tests/t.rs": "fn t() {}",
    }
    items = scan_crate("calc_rs", {k: (f"rust/calc_rs/{k}", v) for k, v in files.items()})
    return {i.path: i.kind.value for i in items}


def test_items_by_path() -> None:
    found = _crate()
    assert found == {
        "calc_rs::LIMIT": "variable",
        "calc_rs::COUNTER": "variable",
        "calc_rs::Pair": "alias",
        "calc_rs::square": "macro",
        "calc_rs::inline": "namespace",
        "calc_rs::inline::helper": "function",
        "calc_rs::rational::Rational": "struct",
        "calc_rs::rational::Error": "enum",
        "calc_rs::rational::Error::ZeroDenominator": "enum_constant",
        "calc_rs::rational::Error::DivisionByZero": "enum_constant",
        "calc_rs::rational::Error::Other": "enum_constant",
        "calc_rs::rational::Error::Named": "enum_constant",
        "calc_rs::rational::Number": "class",
        "calc_rs::rational::Number::value": "method",
        "calc_rs::rational::Number::ZERO": "variable",
        "calc_rs::rational::Rational::new": "method",
        "calc_rs::rational::Rational::num": "method",
        "calc_rs::rational::Rational::Output": "alias",
        "calc_rs::rational::Rational::add": "method",
        "calc_rs::rational::Wrapper::fmt": "method",
        "calc_rs::bridge::calc_rs_rational_new": "function",
        # A method of an impl elsewhere belongs to its type's module.
        "calc_rs::rational::Rational::from_c": "method",
    }


def test_c_symbols_and_positions() -> None:
    items = scan_crate("calc_rs", {"src/bridge/mod.rs": ("rust/calc_rs/src/bridge/mod.rs", BRIDGE)})
    exported = [i for i in items if i.c_abi]
    assert [(i.path, i.line, i.public) for i in exported] == [
        ("calc_rs::bridge::calc_rs_rational_new", 4, True)
    ]
    assert exported[0].file == "rust/calc_rs/src/bridge/mod.rs"


@pytest.mark.parametrize(
    ("rel", "module"),
    [
        ("src/lib.rs", "c"),
        ("src/a.rs", "c::a"),
        ("src/a/mod.rs", "c::a"),
        ("src/a/b.rs", "c::a::b"),
        ("src/main.rs", None),
        ("src/bin/tool.rs", None),
        ("tests/it.rs", None),
        ("build.rs", None),
    ],
)
def test_module_path(rel: str, module: str | None) -> None:
    assert module_path("c", rel) == module


def test_mask_keeps_lifetimes_and_offsets() -> None:
    text = "fn f<'a>(x: &'a str) -> char { let c = 'x'; let s = \"a'b\"; c }"
    masked = mask(text)
    assert len(masked) == len(text)
    assert "'a>" in masked
    assert "&'a str" in masked
    assert "'x'" not in masked
    assert "a'b" not in masked
