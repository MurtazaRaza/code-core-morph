"""Rust tests as nextest names them, with their checks (D-60): the test map's target IDs and the
cross-language assertion guard."""

from __future__ import annotations

from pathlib import Path

import pytest

from codecoremorph.adapters.base import AdapterContext
from codecoremorph.adapters.rust import RustAdapter
from codecoremorph.adapters.rust import tests as rust_tests
from codecoremorph.adapters.rust.tests import scan_tests
from codecoremorph.core.models import LanguageConfig

SOURCE = """\
//! A test file. `#[test] fn in_a_doc()` is no test.
use calc_rs::rational::Rational;

fn helper() -> i32 {
    assert!(true); // a helper's checks count in each test calling it
    1
}

#[test]
fn adds() {
    let r = Rational::new(1, 2).unwrap();
    assert_eq!(r.num(), 1, "num {}", "}");
    assert!(Rational::new(1, 0).is_err());
}

mod suite {
    use super::*;

    #[test]
    #[should_panic(expected = "boom")]
    fn panics() {
        helper();
    }

    #[cfg(test)]
    mod inner {
        #[test]
        fn deep() {
            let e = f().unwrap_err();
            match e {
                _ => panic!("x"),
            }
        }
    }
}

#[cfg(test)]
fn not_a_test() {}

#[tokio::test]
async fn another_runner() {}
"""


def test_tests_with_their_module_paths_and_checks() -> None:
    found = [(t.path, t.line, t.checks) for t in scan_tests(SOURCE)]
    assert found == [
        (
            "adds",
            10,
            ('assert_eq!(r.num(), 1, "num {}", "}")', "assert!(Rational::new(1, 0).is_err())"),
        ),
        ("suite::panics", 21, ("#[should_panic]", "assert!(true)")),
        ("suite::inner::deep", 28, (".unwrap_err()", 'panic!("x")')),
    ]


PARAMETERIZED = """\
fn times(cents: i64, quantity: i32, expected: i64) {
    assert_eq!(expected, cents * i64::from(quantity));
    again(expected);
}

fn again(expected: i64) {
    assert!(expected >= 0);
    again(expected - 1); // a helper never counts inside itself
}

fn twice() {}

#[test]
fn times_199_3_597() {
    times(199, 3, 597);
}

#[test]
fn not_calls() {
    let b = Book::new();
    b.times(1, 2, 3); // a method
    other::times(1, 2, 3); // another module's
    twice();
}

#[test]
fn through_super() {
    super::times(1, 1, 1);
    super::times(2, 1, 2);
}
"""


def test_a_tests_checks_include_the_helpers_it_calls() -> None:
    """A parameterized C# test translates to one Rust test per case calling a helper with the
    checks (the order-book fixture's port, D-84): each case checks what the helper does."""
    found = {t.path: t.checks for t in scan_tests(PARAMETERIZED)}
    assert found["times_199_3_597"] == (
        "assert_eq!(expected, cents * i64::from(quantity))",
        "assert!(expected >= 0)",
    )
    assert found["not_calls"] == ()
    assert len(found["through_super"]) == 4


@pytest.mark.parametrize(
    ("rel", "binary"),
    [
        ("tests/rational_test.rs", ("calc_rs::rational_test", "")),
        ("tests/cli/main.rs", ("calc_rs::cli", "")),
        ("tests/cli/helpers.rs", None),
        ("src/lib.rs", ("calc_rs", "")),
        ("src/rational.rs", ("calc_rs", "rational")),
        ("src/a/mod.rs", ("calc_rs", "a")),
        ("src/main.rs", ("calc_rs::bin/calc_rs", "")),
        ("src/bin/tool.rs", ("calc_rs::bin/tool", "")),
        ("build.rs", None),
    ],
)
def test_test_binaries(rel: str, binary: tuple[str, str] | None) -> None:
    assert rust_tests.test_binary("calc_rs", '[package]\nname = "calc_rs"\n', rel) == binary


def test_a_named_bin_and_the_ids() -> None:
    manifest = '[package]\nname = "t_rs"\n\n[[bin]]\nname = "tinycalc"\npath = "src/main.rs"\n'
    assert rust_tests.test_binary("t_rs", manifest, "src/main.rs") == ("t_rs::bin/tinycalc", "")
    assert rust_tests.test_id("calc_rs::rational_test", "", "suite::adds") == (
        "nextest:calc_rs::rational_test::suite::adds"
    )
    assert (
        rust_tests.test_id("calc_rs", "rational", "tests::x")
        == "nextest:calc_rs::rational::tests::x"
    )


def test_the_adapter_reads_a_packages_tests(tmp_path: Path) -> None:
    package = tmp_path / "rust" / "calc_rs"
    (package / "tests").mkdir(parents=True)
    (package / "src").mkdir()
    (package / "Cargo.toml").write_text('[package]\nname = "calc_rs"\n', encoding="utf-8")
    (package / "tests" / "rational_test.rs").write_text(SOURCE, encoding="utf-8")
    unit = "#[cfg(test)]\nmod tests {\n    #[test]\n    fn one() { assert_eq!(1, 1); }\n}\n"
    (package / "src" / "rational.rs").write_text(unit, encoding="utf-8")
    ctx = AdapterContext(
        worktree=tmp_path,
        build_dir=tmp_path / "build",
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
        executor=None,  # type: ignore[arg-type]  # reading files runs no command
        lang=LanguageConfig(language="rust", standard="rust2021"),
    )
    found = RustAdapter().extract_assertions(
        ctx, ["rust/calc_rs/src/rational.rs", "rust/calc_rs/tests/rational_test.rs", "x.cpp"]
    )
    assert [(a.file, a.test_id, len(a.assertions)) for a in found] == [
        ("rust/calc_rs/src/rational.rs", "nextest:calc_rs::rational::tests::one", 1),
        ("rust/calc_rs/tests/rational_test.rs", "nextest:calc_rs::rational_test::adds", 2),
        ("rust/calc_rs/tests/rational_test.rs", "nextest:calc_rs::rational_test::suite::panics", 2),
        (
            "rust/calc_rs/tests/rational_test.rs",
            "nextest:calc_rs::rational_test::suite::inner::deep",
            2,
        ),
    ]
