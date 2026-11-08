# Translate a Rust file's tests to C

The unit translates the tests of one Rust file, an integration test (`tests/<name>.rs`) or a
module's `#[cfg(test)] mod tests`, into one C program over `codecoremorph_tap.h`, a small TAP
helper CodeCoreMorph wrote beside it. The modules they test are C already, behind their
façades; the translated tests call the C directly, so they keep working once the Rust is gone.

## What the codemod made

- `c/tests/test_<name>.c`: a `main` that selects a case and returns `ccm_done()`. The tests go
  here.
- `c/tests/codecoremorph_tap.h`: the helper. Don't edit it.
- `c/tests/CMakeLists.txt`: builds the program and makes **each case a CTest test named
  `<prefix>::<case>`**, read from the file's `CCM_RUN(case)` lines, so the CTest name of a case
  is its Rust test's ID without `nextest:`. Don't edit it.
- `c/test-map.toml`: the test map (below).

## Writing the cases

Each Rust test becomes one case: a `static void <test name>(void)` function, named exactly as
the Rust test function (`mean_of_three`), run from `main` with `CCM_RUN(mean_of_three);` after
`CCM_SELECT(argc, argv);`. Include the headers of the modules it tests (`#include
"stats/stats.h"`).

Checks:

- `CCM_CHECK(cond)` for `assert!(cond)`
- `CCM_CHECK_INT(got, expected)` / `CCM_CHECK_UINT(got, expected)` for integer `assert_eq!`
- `CCM_CHECK_STR(got, expected)` for strings
- `CCM_CHECK_NEAR(got, expected, within)` for floats (`0.0` for an exact comparison)

A test that reads a data file through `env!("CARGO_MANIFEST_DIR")` reads it through
`CCM_SOURCE_DIR`, the project's top directory (`CCM_SOURCE_DIR "/tests/sample.txt"`), and one
that runs a program (`env!("CARGO_BIN_EXE_<name>")`) runs the CMake program,
`CCM_BINARY_DIR "/c/<name>"`: CMake defines both for every translated test.

Every Rust assertion becomes at least one check: a `Some(3.0)` is a status check and a value
check. A test that expects a panic (`#[should_panic]`) checks the status code or the NULL the C
returns instead. Free everything a case allocates: the leak checker runs at the program's exit,
and a leak fails the test.

## What the unit removes

The Rust tests it translated: delete the integration test file, or the module's `#[cfg(test)]
mod tests` (and the imports only it used).

## The test map

`c/test-map.toml` gets one entry per Rust test the unit lists:

```toml
[[test]]
rust = "nextest:stats::stats::tests::mean_of_three"
c = "ctest:stats::stats::tests::mean_of_three"
```
