# Porting Rust to C

CodeCoreMorph ports a Rust crate to C (C99 or C11) one module at a time, then finishes the job:
the programs become C programs, the tests become C tests, and the Rust goes, leaving a CMake
project. The branch builds and passes its tests at every commit, and the crate's own Rust tests
check every step until they are translated. It is for platforms with only a C compiler
(embedded targets, DSPs, certified compilers), codebases with a C-only policy, and distribution
as plain C source.

It is the mirror image of a C++ port to Rust: each ported module keeps its Rust API as a
**façade** over the C, so the rest of the crate and its tests keep calling it unchanged.

## What is lost, and what replaces it

The borrow checker goes. In its place, every unit's C must pass:

- the compiler with `-Wall -Wextra`, with no warning;
- clang-tidy's bug-finding checks (`bugprone-*`, `clang-analyzer-*`), with no finding;
- every test under AddressSanitizer and UndefinedBehaviorSanitizer, from the first unit on: an
  out-of-bounds access, a use after free, a leak, signed overflow or a misaligned load fails the
  test that reaches it, with the sanitizer's report.

These are post-conditions, not advice. During the port the Rust tests run the C under the
sanitizers through the façades; afterwards the translated C tests do.

## What you need

- A crate whose `cargo build` and `cargo test` pass, with its `Cargo.lock` committed. One
  library package, at the repository's root or as a member of a Cargo workspace below it (the
  `rust/` workspace a port from C leaves, for one); its programs (`src/main.rs`,
  `src/bin/*.rs`, or packages of their own that use the library) and integration tests come
  along.
- Docker, and the Rust image: `scripts/build-image rust` (`.bat` on Windows). It holds Rust,
  rust-analyzer (the index of the crate), bindgen, and the C toolchain: clang, clang-tidy, CMake
  and Ninja.

## The manifest

```yaml
version: "1.0"
project:
  name: rpn
  source_dirs: [src]
  test_dirs: [tests]
migration:
  source: {language: rust, standard: rust2018}
  target:
    language: c
    standard: c99
    rules: [port_rust_module, port_rust_program, port_rust_tests, retire_rust]
sandbox: {mode: docker}
```

The C target builds with CMake (`migration.target.build_system` defaults to `cmake`).

## The plan

`codecoremorph plan` indexes the crate with rust-analyzer (its SCIP output: every item, and
every reference to it, by file and line) and cuts:

1. **One unit per module file**, leaves first by the references between them, so what a module
   uses is ported before it. The crate root goes with the retirement when it only declares the
   modules, and is in each module's unit for its crate attributes: a `#![forbid(unsafe_code)]`
   there stops every façade, since nothing can allow what `forbid` forbids, so the first unit
   makes it `#![deny(unsafe_code)]` and each façade allows unsafe code for itself.
2. **One unit per program.**
3. **One unit per file with tests**: a module's `#[cfg(test)] mod tests`, then each integration
   test, each after the modules it uses.
4. **The retirement.**

A unit's note lists the module's public items, which the symbol map must cover, and, for a
generic item, where the crate uses it: C has no generics, so each type it is used with becomes
one C function, or `void *` and a size where that is the C idiom. A lifetime alone
(`Word<'a>`) isn't generic in that sense, nor is a type parameter bound by an iteration over a
given item, a conversion or a closure (`I: IntoIterator<Item = &'a [u8]>`, `P: AsRef<Path>`,
`F: Fn(u8) -> bool`): the façade turns the argument into one concrete value (it collects the
iterator) and calls one C function, so such an item needs no use in the crate.

## Module units

The codemod makes the C build on the first unit and adds to it on each:

- `CMakeLists.txt` at the top: the project, the `CODECOREMORPH_SANITIZE` option (CodeCoreMorph's
  builds turn it on), and `add_subdirectory(c)`.
- `c/CMakeLists.txt`: the static library `<crate>_c`, one source per ported module.
- `c/include/<crate>/<module>.h` and `c/src/<module>.c`, empty: the unit's C goes there.
- `src/<module>/sys.rs` in the crate's directory: bindgen writes it from the module's header
  before every build.
- `build.rs` in the crate's directory, and `c/cargo-link`: Cargo links the C library through a
  build script that runs nothing; it finds `c/cargo-link` in the crate's directory or the
  nearest one above it. CMake builds the C before Cargo builds the crate. That script is the
  only one I-12 accepts, and nothing may change it.
- `c/symbol-map.toml`: where each Rust item went.

The C, its CMake build and the maps are at the repository's root, wherever the crate is.

The agent writes the C, turns the module into the façade, each public item keeping its
signature, docs and derives and calling the C through `sys`, and maps every public item. I-6
refuses a unit that drops a public item.

The mapping, in short:

| Rust | C |
|---|---|
| `&[T]`, `&str` | a pointer and a length |
| `Option<T>`, `Result<T, E>` | a status code and an out-parameter |
| an enum with data | a tagged union |
| a trait | a struct of function pointers (and a context) |
| a struct that owns memory | an opaque type with `_new` and `_free`; the façade frees it in `Drop` |
| a generic item | one function per type in use, or `void *` and a size |
| a closure | a callback and a `void *` context |
| a panic | a status code, or `abort()` |

## Programs, tests and the retirement

- A **program's unit** moves the program into the C library as `<crate>_<program>_main(int
  argc, char **argv)`. The Rust program becomes a façade that calls it, so the tests that run
  the program run the C, and a CMake program of the same name calls it too. The façade must
  name the crate (`extern crate <crate>;`): Cargo links the C to the crate's library only.
- A **test file's unit** translates the file's tests to a C program over `codecoremorph_tap.h`,
  the same small TAP helper as C characterization tests (D-74). Each case is a function named as
  its Rust test, and `c/tests/CMakeLists.txt` makes each case a CTest test named as the Rust
  test (`ctest:rpn::eval::tests::evaluates_in_order` for `nextest:rpn::eval::tests::...`), read
  from the file's `CCM_RUN` lines. `c/test-map.toml` pairs them, and the Rust tests go. A
  test that reads a data file through `env!("CARGO_MANIFEST_DIR")` reads it through
  `CCM_SOURCE_DIR`, the project's top directory, and one that runs a program
  (`env!("CARGO_BIN_EXE_<name>")`) runs CMake's, `CCM_BINARY_DIR "/c/<name>"`: CMake defines
  both for every translated test.
- The **retirement** deletes every package's manifest, build script and Rust files, the
  workspace's manifest and lock, and the link list. The CMake build at the top level stays,
  and builds and tests the C alone. Files Cargo didn't own stay where they were: a workspace's
  `.gitignore`, the maps of an earlier port to Rust. It lists for your review what still builds
  the Rust: the CI and any build file that runs Cargo, and a manifest it left (an excluded fuzz
  workspace) that takes a deleted package by its path.
- A crate's **Cargo features** are tested too: the tests run with the default features and
  again with each feature the defaults don't turn on, alone, their IDs ending in `[<feature>]`.
  The C keeps what each feature turns on, and the test map maps those IDs as well.

## The crate's own procedural macros

A crate may have a package of procedural macros of its own: a workspace member with
`proc-macro = true` whose derives, attributes and function-like macros the library uses
(`#[derive(Encode)]`, `#[throws(DecodeError)]`, `crc_table!(0xA001)`). They are code the
compiler runs on each site, and what they write the C has to provide (D-93):

- **The index records each site and what it expands to.** rust-analyzer's SCIP output names
  every site (a reference to a macro of the macro package, outside a `use`), and its language
  server expands each, offline, on the pinned stable toolchain, with rust-analyzer's own
  proc-macro server: the code the compiler sees there. CodeCoreMorph ships the small client that
  asks it (`rust-analyzer/expandMacro`, what an editor's "Expand macro recursively" runs). A
  crate without a macro package of its own runs none of this.
- **The macro package is no library to port.** Its files go with the retirement, which deletes
  it with the rest of the Rust; its own tests, which checked the macros, map to notes.
- **The agent ports from the expansions,** which a unit's message shows for each site in its
  files: what a derive writes becomes C functions of the type (`wire_header_encode`), an
  attribute's item keeps the signature the attribute gave it (`read_frame`'s `Result` a status
  and out-parameters, as any), and a function-like macro's value is the C's (a lookup table
  computed while the crate compiled becomes a static array, written out). Not from the macro's
  code: the expansion is exactly what compiles at that site.
- **A derive's methods are the module's to map:** they join the unit's exports, so the symbol
  map covers `wire::msg::Header::encode` with the module's own items.
- **The façade uses none of the crate's own macros.** It implements a derived trait by hand,
  over the C, and writes an attribute's item with its expanded signature; otherwise the macro's
  Rust, not the C, would do what it writes. A post-condition of each module's unit checks it.
  The standard derives (`Debug`, `Clone`) stay, and so do a module's `#[cfg(test)]` tests,
  whose test-only types may derive the crate's macros until their own unit translates them.
- **A test file's own sites** (a test-only type's derives) go into its C test program, from
  their expansions: a struct, and static functions for what the derives write.
- **Still refused, by site:** a macro the library re-exports (`pub use wire_macros::Encode`),
  which code outside the crate applies to its own types and C can't offer, and a site
  rust-analyzer gave no expansion for. A registry package's procedural macros (`serde_derive`)
  aren't the crate's own and aren't lifted.

## Reviewing the result

The report lists every unit, the symbol map's notes (behavior the C keeps differently, such as
a reducer looked up by name returning a static struct instead of a `Box<dyn Reducer>`), and the
test map. The final verification is a clean CMake build whose tests, every one a translated
Rust test, pass under the sanitizers.

## The fixtures

`fixtures/rust/rpn` is a reverse Polish notation calculator, edition 2018: a lexer with an enum
with data and `Result` errors, a generic stack, an evaluator, a reducer trait with three
implementations and a closure-taking filter, the `rpn` program, nine unit tests and three tests
that run the program. Ported whole through `codecoremorph run --host-agent` (11 of 11 units,
the first ten on their first attempt, the retirement by its codemod alone, in 7.8 minutes), it
replays from the recorded edits in `tests/integration/test_rust_e2e.py`.

`fixtures/rust/wire` is a framed binary message format with a macro package of its own
(`macros/`): `derive(Encode)` and `derive(Decode)` with a helper attribute `#[wire(skip)]`,
on two structs and an enum with data; `#[throws(DecodeError)]`, which turns a function's
`T` into a `Result`; `crc_table!(0xA001)`, which computes a CRC-16 table while the crate
compiles; and a test-only type in `tests/frames.rs` that derives both. Ported whole through
`codecoremorph run --host-agent` (8 of 8 units, each agent unit on its first attempt, the
retirement by its codemod alone, in 14 minutes), it ends as C with no macro package left,
and replays from the recorded edits like `rpn`.

## The benchmark: shlex

M10 is shlex 1.3.0 (`bench/rust-port-pin.json`), the crate that splits and quotes words as a
POSIX shell does: two modules and their unit tests, 934 lines, edition 2015, no dependencies.
It doesn't commit its `Cargo.lock`, so `bench/fetch.py` commits one on top of the pinned commit;
its `fuzz/` workspace is excluded. Headless Claude Code ported it whole with `bench/run.py`:
five units, each agent unit on its first attempt, in 32 minutes. Its 10 tests are C tests that
pass under the sanitizers, and over 30,000 seeded inputs the C splits and quotes byte for byte
as the original crate does.

## The round trip

`tests/integration/test_round_trip.py` is the eval of both directions together:
`fixtures/c/c-legacy` (a word-frequency library and its program, C89 with a Makefile) ported to
Rust with Phase 15's recorded run, then that Rust ported back to C. The second leg starts from
what a port from C leaves: a Cargo workspace under `rust/`, the library's crate (`wf_rs`) and
the program's (`wordfreq_rs`, which depends on it), the C → Rust maps, and the data files under
`tests/` that the program's tests read. Its plan has the five modules leaves first, the program,
the five test files and the retirement; the C comes back under `c/` with the CMake build at the
root, beside the earlier port's maps.

An original C test still passes when each of its Rust tests (`rust/test-map.toml`) maps to C
tests (`c/test-map.toml`) that pass in the final verification. 37 of c-legacy's 41 C tests had
Rust tests and come back as passing C tests, `make:test` among them (the program's output,
byte for byte against `tests/sample.out` and `tests/sample-k.out`); the other 4 checked a NULL
pointer or freeing, which the first leg mapped to notes.

## Limits

- One library crate: the root package, or a workspace's member beside packages of programs and
  tests that use it. A workspace of several libraries is refused.
- Refused: a crate that is only a procedural macro package, a macro the crate re-exports,
  `async` code, and a public generic item that nothing in the crate uses (it has no
  instantiation to port), unless its type parameters are all iterations over a given item,
  conversions or closures. A registry package's procedural macros aren't lifted, nor refused
  yet.
- A changed signature of a public item shows in the build and the tests, not in I-6: the Rust
  index records items, not their signatures.
- The index sees the default features only: code behind another feature isn't in the plan,
  though its tests run. How the C keeps what such a feature changes is the agent's to work out.
- `unsafe` in the façades isn't checked: the Rust is retired anyway, and the façades are the
  only place the port adds it.
- Docker only, for now: a native Windows run (the sanitizers with llvm-mingw and Rust's
  `gnullvm` target) hasn't been tried.
