# Retire the Rust

The last unit. Every module is C behind its façade, every test is a C test, and the programs
are C: the codemod deleted the Cargo manifests and lock files, the build script, the link list
and every Rust file. What is left is a CMake project: the top-level `CMakeLists.txt`, the C
library under `c/`, its tests under `c/tests` and its programs. Files Cargo didn't own stay
where they were (a workspace's `.gitignore`, the maps of an earlier port to Rust).

## What the unit does

1. Check that the project builds and its tests pass without the Rust: the build and the tests
   of the verification show it. Fix what still refers to the Rust (a comment in a header that
   names a façade, a path in a CMake file).
2. The test map: every Rust test of the baseline that no longer runs needs its entry, each
   mapped C test run and passing. The earlier units mapped theirs; add what is missing.
3. Rewrite notes in `c/symbol-map.toml` that describe the façades, which are gone; the map
   stays as the record of where each Rust item went.

Keep `CODECOREMORPH_SANITIZE` in the top-level `CMakeLists.txt`: it is how the tests run the C
under the sanitizers, and it is off unless a build turns it on.
