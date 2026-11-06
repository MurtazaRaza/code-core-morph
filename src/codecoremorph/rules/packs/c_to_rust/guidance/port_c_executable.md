# Port a C program to Rust

The unit moves one C program to a Rust program of the same name. Every source file of the
libraries it links is Rust already (its unit needs theirs), so the program calls the **Rust API
of the library crates**: their modules, never their `bridge`. The Make build copies the Rust
program to the C program's path, so `make test`, the project's scripts and its users run it as
before, and the tests that run it decide whether the port is right.

## What the codemod made

- `rust/<program>_rs/`: a package whose `bin` has the C program's name (`src/main.rs`, empty),
  with the library crates as dependencies.
- The program's rule in the Makefile now copies the Rust program to the program's path (`cp
  $(CODECOREMORPH_RUST_BIN_<program>) $@`); the marked block builds it with the workspace. With
  automake, the program's `Makefile.am` gives it no sources (`<program>_SOURCES =`) and a link
  that copies the Rust program (`<program>_LINK`, in the marked block). Don't edit the block.
- `rust/test-map.toml`: the map of C tests to the Rust tests that took them over.

## What the unit does

1. Port the program to `src/main.rs`, in safe Rust (`#![forbid(unsafe_code)]` stays).
2. Delete its C sources (listed under Left to do) and the headers only they used, and remove
   what the build files still say about them: their objects, compile rules and dependency lines
   (a `Makefile.am` says nothing more about them).
3. If tests run the program directly (listed in the unit), translate them into integration
   tests of the package (`tests/`) and map each in `rust/test-map.toml`.
4. If the test target compares the program's output with files (`make test` runs
   `./wordfreq 3 < tests/sample.txt > tests/sample.got` and `cmp`s it with `tests/sample.out`,
   or a script among automake's `TESTS` does), add an integration test of the package for each
   such run, running the program the same way
   (`std::process::Command::new(env!("CARGO_BIN_EXE_wordfreq"))`, the data files from
   `concat!(env!("CARGO_MANIFEST_DIR"), "/../../tests/...")`). They stand for that part of the
   test target once the C is retired.

## Behavior to keep, byte for byte

The tests compare the program's output with files. Keep:

- **Arguments**: the same options, their order and combinations, the same usage message on the
  same stream, and the same exit status for each error. Parse `std::env::args_os` by hand as the
  C did (no argument-parsing crates).
- **Output**: the same bytes. `printf("%lu %s\n", ...)` → `writeln!(out, "{} {}", ...)` on a
  locked, buffered `stdout`; flush before exiting. Bytes that aren't UTF-8 stay bytes (`&[u8]`,
  `write_all`), and numbers keep C's formatting (`%5.2f`, padding, signs).
- **Input**: read standard input or files as bytes (`read_to_end`); a C program that reads lines
  with `fgets` into a fixed buffer splits long lines: keep what the tests rely on.
- **Exit status**: `std::process::exit` with the C program's codes; `main` returning `Err`
  exits with 1, which may not be the C's.
- **Errors**: messages to `stderr` with the same text, `strerror(errno)` → the
  `std::io::Error`'s message where the C printed one.

## What else the unit must not do

- No crates from crates.io, no `build.rs`, no `unsafe`, no `#[allow(...)]`: fix what clippy
  reports.
- Don't call the library crates' `bridge` or `sys` modules: their C interface is for C.
- Don't edit the tests, the kept headers, or the marked block of the Makefile.
