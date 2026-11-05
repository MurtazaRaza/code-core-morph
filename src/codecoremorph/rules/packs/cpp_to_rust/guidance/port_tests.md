# Translate a C++ test file to Rust

The unit translates one C++ test file (GoogleTest) into a Rust integration test of the crate it
tests, case by case. The modules it tests are Rust already; the C++ file stays as it is and
keeps running, so both versions pass until the C++ is retired. The test map pairs each C++
test with the Rust test that took it over, and that pairing is the port's parity: from the
retirement on, the Rust test stands for the C++ one.

## What the codemod made

- `rust/<crate>/tests/<file>.rs`: empty but for its doc comment and `#![forbid(unsafe_code)]`.
  **The tests go here.** It is an integration test: it sees the crate's public API only
  (`use calc_rs::rational::Rational;`), the way outside code will.
- `rust/test-map.toml`: the test map. The CTest entry that only runs the GoogleTest binary is
  already mapped (to nothing, with a note) by the unit that translates the binary's last file.
- `dev-dependencies` on the other crates the file's tests use, when there are any.

## Translating

- **One Rust test per C++ case**, `#[test] fn <case_in_snake_case>()`, in a module per suite
  when the file has several suites (`mod rational { ... }`). Keep the cases' order.
- **Assertions:** `EXPECT_EQ`/`ASSERT_EQ` → `assert_eq!`, `EXPECT_NE` → `assert_ne!`,
  `EXPECT_TRUE(x)` → `assert!(x)`, `EXPECT_FALSE(x)` → `assert!(!x)`, `EXPECT_LT` and friends
  → `assert!(a < b)`, `EXPECT_NEAR(a, b, e)` → `assert!((a - b).abs() <= e)`,
  `EXPECT_STREQ` → `assert_eq!` on the bytes or `&str`. Keep every check: the translation must
  check at least as much as the original (the guard compares their counts). A loop of checks
  stays a loop.
- **Exceptions:** `EXPECT_THROW(f(), E)` → `assert!(matches!(f(), Err(Error::E { .. })))` or
  `assert_eq!(f(), Err(...))`, checking the error the Rust API returns where the C++ threw;
  `EXPECT_NO_THROW(f())` → `f().unwrap()` or `assert!(f().is_ok())`; a check of `what()` →
  a check of the error's `to_string()`.
- **`EXPECT_*` keeps going after a failure, `assert!` stops.** That only changes what a failing
  test reports; where a case's later checks depended on running after an earlier one failed,
  say so in the map's note.
- **Fixtures** (`TEST_F`): the fixture class becomes a helper struct with a `fn new()` (its
  `SetUp`) and `Drop` (its `TearDown`), or a plain helper function; each test builds it.
- **Test doubles:** a C++ subclass of a ported class (a fake, a mock) becomes a Rust type that
  implements the port's trait. A `std::function` becomes a closure.
- **Values the C++ test spelled as C++** (a `long`, a `std::string`, a `std::vector`) become
  their Rust API's types (`c_long`, `&[u8]` or `Vec<u8>`, `Vec`).
- Translate what the test checks, not how the C++ API spelled it: a test of a C++-only detail
  (a copy constructor, `operator=`) checks the Rust equivalent (`Clone`). A case with nothing
  left to check in Rust maps to nothing, with a note that says why.
- No `unsafe`, no `bridge` module, no new crates from crates.io.

## The test map

Add an entry to `rust/test-map.toml` for every C++ test of the file:

```toml
[[test]]
cpp = "gtest:Rational.AddsFractions"
rust = "nextest:calc_rs::rational_test::rational::adds_fractions"

[[test]]
cpp = "gtest:Rational.CopyIsIndependent"
rust = "nextest:calc_rs::rational_test::rational::clone_is_independent"
note = "Rust copies the value with Clone; the C++ copy constructor has no counterpart."
```

The C++ ID is `gtest:<Suite>.<Case>`; the Rust ID is
`nextest:<crate>::<test file name>::<module path>::<fn>`. Each Rust test stands for one C++
test, and each must pass.
