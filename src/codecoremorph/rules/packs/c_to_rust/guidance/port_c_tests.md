# Translate a C test file into Rust tests

The unit translates one C test file into an integration test of the crate it tests
(`rust/<crate>/tests/<file>.rs`). The C test stays as it is and keeps running, so both judge the
port until the C is retired; then the Rust tests stand alone. Each test the C program reports
(a TAP point such as `tap:tests/test_htab/the table grows`, or an automake test) becomes one
Rust test, mapped in `rust/test-map.toml`. An automake test without TAP is the whole program
(`make:tests/test_value`, its exit status): give each of its checks, or each group of checks
its `main` calls, a Rust test, and map the program's test to all of them (`rust = [...]`).

## Writing the tests

- **The crate's safe Rust API only**: `use wf_rs::htab::Table;`. Not its `bridge`, not `sys`, no
  `unsafe` (`#![forbid(unsafe_code)]` stays). The C test reached the code through the C
  interface; the Rust test reaches the same behavior through the Rust types that stand for it.
- **One test per C test, checking at least as much**: each `ok(...)` (or `is`, `cmp_ok`, an
  `assert`) becomes an `assert!`/`assert_eq!`, and a point that checks two things checks both.
  What the C checked through the C interface (a struct's fields, a NUL terminator, `errno`, a
  status code) is checked on the Rust values that stand for it: the buffer's bytes and length,
  an `Err` and its variant.
- **Names**: a snake_case function per test, from its description (`the table grows` →
  `fn the_table_grows()`).
- **Data files** the C test read (`tests/sample.txt`) are read from the project:
  `concat!(env!("CARGO_MANIFEST_DIR"), "/../../tests/sample.txt")`.
- A test that runs a program uses the program's package (`env!("CARGO_BIN_EXE_<name>")` works
  only in that package's own tests).

## The test map

One entry per C test the unit lists, by the IDs the test reports use:

```toml
[[test]]
c = "tap:tests/test_htab/the table grows"
rust = "nextest:wf_rs::test_htab::the_table_grows"
```

A C test that has no Rust counterpart (it checked a C-only detail with no Rust meaning) gets a
`note` instead of `rust`, saying why. A C test may map to several Rust tests (`rust = [...]`);
each Rust test stands for one C test.

## What else the unit must not do

- No crates from crates.io, no `build.rs`, no `#[allow(...)]`: fix what clippy reports.
- Don't edit the C tests, the crates' code or the Makefile.
