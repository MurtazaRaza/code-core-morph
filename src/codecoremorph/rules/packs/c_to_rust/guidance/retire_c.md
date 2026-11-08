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
3. **The symbol map**: `rust/symbol-map.toml` describes the Rust as it is now. Drop the paths of
   items the codemod deleted (`<crate>::bridge::...`), and rewrite each `note` that describes
   the bridge or C code that ran beside the Rust during the port.
4. **The test map**: every baseline test needs an entry. The C tests mapped one by one already
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

## With `keep_source_api`

The library keeps its C interface for outside C code: the public headers stay exactly as they
are, with the headers they include, the Make build, each crate's bridge with its `sys` module,
the C files that still define a function the headers declare (one that takes `...` or a
`va_list`), and the C tests that use only kept headers. The codemod deleted only the rest: the
other headers and the C tests that used them. In place of step 1:

- **The bridge**: keep exporting every function the kept headers declare, with its signature
  (`c_abi_kept` checks the crates whole). Remove the exports that only a deleted header
  declared, and the bridge code only they used: `sys` no longer has that header's types.
- **The build**: take out of the Makefile (or `Makefile.am`) what the deleted files needed,
  such as the rules and test programs of the C tests that went. Keep the marked block.
- Keep `#![deny(unsafe_code)]` at the crate roots: the bridge still needs `unsafe`.

Steps 2 to 4 apply as they are; a C test that still runs keeps its entry.

## What else the unit must not do

- No crates from crates.io, no `build.rs`, no `unsafe` outside the bridge, no `#[allow(...)]`.
- Don't delete Rust tests, and don't weaken them: each still checks what its C test checked.
