# Retire the C#

The last unit. Every type is Rust behind its façade, every test is a Rust test, and the
programs are Rust: the codemod deleted the C# projects and their files, the solution, the
MSBuild files only the C# needed (`Directory.Build.props`, `global.json`, `nuget.config`), the
generated declarations and the façades' support, and each crate's bridge (`src/bridge/`, the
`include/` headers, `cbindgen.toml`, the `cdylib` crate type), and made each crate root
`#![forbid(unsafe_code)]`; a crate whose Rust calls a native library keeps its `native` module
(`src/native/`: the safe wrappers and bindgen's declarations, which stay as the last configure
wrote them), and its root denies unsafe code everywhere else. What is left is the Cargo
workspace under `rust/`: the library's crate, its tests and programs, and
`codecoremorph_runtime`, which the crate uses.

With `migration.target.keep_source_api`, the C# public API stays as a .NET library over the
Rust, for .NET consumers: the codemod deleted only the programs, which are Cargo programs now,
and the test projects that reference them, and took them out of the solution. The library's
façade project, the bridge, the `cdylib`, the support files and the C# tests of the kept API
stay, and both builds run in the verification.

## What the unit does

1. Check that the workspace builds and its tests pass without the C#: the build and the tests
   of the verification show it, `--locked` and offline. Fix what still refers to the C# or the
   bridge (a `use` of a bridge item, a comment that names a façade, code only the bridge
   called, which clippy now reports as dead).
2. The test map: every C# test of the baseline that no longer runs needs its entry, each mapped
   Rust test run and passing. The earlier units mapped theirs; add what is missing.
3. Rewrite notes in `rust/symbol-map.toml` that describe the façades, which are gone; the map
   stays as the record of where each C# member went.

`codecoremorph_runtime` stays: the Rust uses its `Decimal` and `Exception`. No `unsafe` is
left outside a `native` module, and none may come back.
