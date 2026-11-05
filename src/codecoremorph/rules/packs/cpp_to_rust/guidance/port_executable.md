# Port a C++ executable to a Rust program

The unit replaces one C++ program (a CMake `add_executable` that isn't a test) with a Rust
program of the same name. The library modules it uses are Rust already, so the program calls
their Rust API directly, not the C++ façades or the bridge.

## What the codemod made

- `rust/<target>_rs/Cargo.toml`: a package with one `[[bin]]` named after the C++ executable,
  and the crates of the libraries it linked as dependencies. Corrosion imports the program as
  a CMake executable of that name, so `add_test(... COMMAND <name> ...)` and scripts that run
  it keep working.
- `rust/<target>_rs/src/main.rs`: an empty `main` under `#![forbid(unsafe_code)]`. **The port
  goes here.**
- The C++ target's `add_executable` and `target_*` commands are gone from the CMake files. The
  C++ sources are still there for you to read: **delete them** once `main.rs` does their job
  (the unit isn't done while they exist).

## The program

- Same arguments, same standard input, same output byte for byte (`println!` writes `\n`; the
  C++ wrote `'\n'` too, unless it used `std::endl` or `\r\n`), same messages on standard error,
  same exit codes. Read arguments with `std::env::args_os()` and convert them as the C++ did
  (bytes, not necessarily UTF-8).
- Call the crates' Rust API (`calc_rs::calc::Session`), not their `bridge` module.
- Keep it small: the logic belongs in the library crates; a helper only the program used can
  live in `main.rs` or a module next to it (`src/<name>.rs` with `mod <name>;`).
- No `unsafe`, no new crates from crates.io, no `build.rs`.

## Its tests

The unit's tests are the CTest entries that run the program (`ctest:<name>`): they keep running
against the Rust program, and the unit translates each into a Rust integration test of the
package, `rust/<target>_rs/tests/<name>.rs`:

```rust
use std::process::Command;

#[test]
fn tinycalc_args() {
    let out = Command::new(env!("CARGO_BIN_EXE_tinycalc"))
        .args(["1 + 2 * 3", "ans / 4"])
        .output()
        .expect("the program runs");
    assert!(out.status.success());
    assert_eq!(String::from_utf8_lossy(&out.stdout), "7\n7/4\n");
}
```

A `PASS_REGULAR_EXPRESSION` becomes an exact comparison where the expression pins the whole
output (or a `contains`/line check where it doesn't); `WILL_FAIL` becomes
`assert!(!out.status.success())`; a `WORKING_DIRECTORY` becomes `.current_dir(...)`. Then map
each in `rust/test-map.toml`:

```toml
[[test]]
cpp = "ctest:tinycalc_args"
rust = "nextest:tinycalc_rs::tinycalc::tinycalc_args"
```

The Rust test ID is `nextest:<package>::<test file name>::<module path>::<fn>`.
