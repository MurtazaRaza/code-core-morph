# Retire the C++

The port's last unit. The modules, the programs and the tests are Rust; the C++ façades serve
only each other and the C++ tests, which the test map has paired with Rust tests. The codemod
deleted the C++ and what only the C++ needed; this unit makes what is left build and pass on
its own, as a Cargo workspace.

## What the codemod did

- Deleted every C++ source, header and test the unit lists, and the CMake build
  (`CMakeLists.txt`, `*.cmake`).
- Removed each crate's C bridge: `src/bridge/`, `include/` (the bridge headers), `cbindgen.toml`,
  the `staticlib` crate type and the CMake target in `[package.metadata.codecoremorph]`.
- Turned `#![deny(unsafe_code)]` into `#![forbid(unsafe_code)]` at each crate root.

From now on Cargo alone builds and tests the project (`cargo build`, `cargo nextest`), locked
and offline.

## What is left for you

Fix what the build, clippy and the checks report, in the crates:

- Code only the bridge used: `#[repr(C)]` on types no C sees any more, conversions to and from
  C types, `pub(crate)` helpers nothing calls (rustc's `dead_code`). Delete them, or make an
  item `pub` when it belongs to the crate's API.
- `c_long`/`c_int` in the API were there for the bridge; they may stay (the symbol map says
  what each became), or become `i64`/`i32` where nothing depends on the C size.
- Docs that describe the façade, the bridge or the C++ build.
- A baseline test the test map doesn't cover yet: add the Rust test and its entry (or an entry
  without `rust`, with a note that says why it has none).

Don't weaken a test to make it pass, and don't bring `unsafe` or `#[allow(...)]` back.

## With `keep_source_api`

The public headers stay, with the sources behind them, the bridge they call, the CMake build
and the C++ tests that use only them: a thin C++ library over the Rust crates for outside C++
consumers. The codemod deleted only the C++ they don't need (internal modules' façades, tests
of internal modules). Make the CMake build work without the deleted files (take them out of
`add_library` and the test lists); keep the public headers exactly as they are.
