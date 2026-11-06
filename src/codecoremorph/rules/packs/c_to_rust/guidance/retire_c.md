# Retire the C

Every source file, program and test file is Rust now, and every C test has a Rust test mapped
to it. The codemod deleted what was left of the C: the sources, the headers, the Makefile (and
`configure`'s inputs, or automake's `configure.ac` and `Makefile.am` files), and each crate's
`bridge` with its `sys` module, its `staticlib` crate type and its metadata. The Cargo workspace
under `rust/` now builds and tests alone.

## What the unit does

1. **Make the workspace build alone.** Remove what only the bridge used: conversions between
   C and Rust types, functions kept for C callers, `#[no_mangle]` statics. Keep the crate roots'
   `#![forbid(unsafe_code)]`. A module that used the bridge's globals reads its own state now.
2. **Docs**: comments and `//!` docs that describe the C interface or the C files.
3. **The test map**: every baseline test needs an entry. The C tests mapped one by one already
   have theirs. A coarse one, such as `make:test` (the exit status of the test target) or a
   script among automake's `TESTS` (`make:tests/dump`), maps to the Rust tests that took over
   what it checked beyond those: output comparisons of a program (`./wordfreq 3 <
   tests/sample.txt` compared with `tests/sample.out`), checks a script made.
   Write those tests if no earlier unit did, and map the entry with a note:

   ```toml
   [[test]]
   c = "make:test"
   rust = ["nextest:wordfreq_rs::cli::top_three", "nextest:wordfreq_rs::cli::keys_only"]
   note = "Its TAP programs are mapped point by point; these take over its output comparisons."
   ```

## What else the unit must not do

- No crates from crates.io, no `build.rs`, no `unsafe`, no `#[allow(...)]`.
- Don't delete Rust tests, and don't weaken them: each still checks what its C test checked.
