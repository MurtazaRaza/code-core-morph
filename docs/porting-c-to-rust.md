# Porting C to Rust

CodeCoreMorph ports an old C library and its programs to Rust one source file at a time, and
then finishes the job: the programs become Rust programs, the tests become Rust tests, and the C
and its Make build go, leaving a Cargo workspace. The branch builds and passes its tests at
every commit, and the project's own C tests check every step until they are translated.

There is no façade, as there is for C++: every C function already has C linkage, so a ported
file's functions are exported from Rust under their C names, and the headers stay exactly as
they are. The Make build folds the Rust crate into the library's archive (`libfoo.a`), so the C
that still calls those functions, the C programs and the C tests link against Rust unchanged.

Any C89 or newer project works. Make builds are the usual case for old C: a plain `Makefile`,
`./configure` and Make, or automake and libtool. They run in Docker for now.

## What you need

- A project whose `make` builds a static library and whose test target (`make check` or
  `make test`) passes, as for any migration. Tests that print TAP (`ok 1 - ...`) give one test
  per point; automake's test harness gives one per program; otherwise the test target's exit
  status counts as one test.
- Docker, and the Rust image: `scripts/build-image rust` (`.bat` on Windows). It holds the C
  toolchain, Rust, cbindgen and bindgen.

## The manifest

```yaml
version: "1.0"
project:
  name: wordfreq
  source_dirs: [src, include, tools]
  test_dirs: [tests]
  public_api: ["include/**"]
migration:
  source: {language: c, standard: c89, build_system: make}
  target:
    language: rust
    standard: rust2021
    rules: [port_c_module, port_c_executable, port_c_tests, retire_c]
sandbox: {mode: docker}
```

`build.make.test_targets` names the test target when it is neither `check` nor `test`, and
`build.make.configure_args` passes arguments to `./configure`. You can stop after any rule:
`rules: [port_c_module]` ports the library and leaves the programs, the tests and the C
headers in place.

## How the build is read

CodeCoreMorph builds the project in a copy of the worktree, so the old Makefile's objects never
reach your branch. Wrapper scripts stand in for the compiler and the archiver during the build
and the tests and record every call. From them come the compile commands for the index, the
libraries with their source files, the programs and the libraries they link, and the tests and
the program each test runs.

## The plan

1. **One unit per source file of a library** (`port_c_module`), ordered by the calls between
   the files: a file whose functions call no other file's go first. Old C often has one internal
   header that declares every module's functions, which would make every file depend on every
   other by its includes; the calls give the real order. Within a call cycle, the file that
   calls the fewest functions still in C goes first.
2. **One unit per program** (`port_c_executable`), once every file of the libraries it links is
   Rust.
3. **One unit per test file** (`port_c_tests`), once the files it tests are Rust.
4. **One retirement unit** (`retire_c`), last.

The code goes to a Cargo workspace beside the C project, one crate per library
(`rust/<library>_rs/`).

## Source file units

1. The `rust-module` codemod creates the workspace and crate the first time, with the unit's
   module: `src/<module>.rs` for the port (safe Rust) and `src/bridge/<module>.rs` for the C
   functions it exports, the one place `unsafe` code may go. It writes a marked block at the end
   of the Makefile that builds the workspace, makes the library's archive rule fold the crate
   into the archive, and adds Rust's native libraries to the links that use the library.
2. bindgen writes `src/bridge/sys.rs` from the headers the unit keeps, before every build: their
   types, constants and the functions still in C, each type's size and layout checked when the
   crate compiles. The crate uses these types and defines no copy of them.
3. The agent ports the file's logic to Rust, exports every function and global other files use
   under its C name and signature, deletes the C file and takes its object out of the Makefile.
   It maps each export in `rust/symbol-map.toml`.

CodeCoreMorph checks that the file's definitions are gone, that the exports match the headers'
declarations (`c_abi_kept`), that `unsafe` stays in the bridge, that clippy is quiet, and that
the symbol map is whole. The project's C programs and C tests then run against the Rust code.

Structs that C reads field by field stay C structs, kept up to date by the bridge; memory that C
frees comes from C's allocator; globals that C reads stay data symbols (an `AtomicUsize` for a
`size_t`). The rule's guidance has the mapping of pointers and lengths, `malloc` and `free`,
`goto` cleanup, error codes and `errno`, macros, and global state.

A function that takes `...` or a `va_list` (`printf`-style logging) stays C: stable Rust can't
define one. The unit says which. They stay in their file, reduced to formatting the arguments
and calling a function the bridge exports, and the retirement deletes them with the rest of the
C; Rust code formats with `format!` instead.

## Automake and libtool

With automake, the marked block goes at the end of the library's `Makefile.am`, and
CodeCoreMorph runs `autoreconf -fi` again whenever a `Makefile.am`, `configure.ac` or m4 file
changes. A project that commits its own `configure` keeps using it until then.

- The library takes the crate in its `LIBADD`, as one relocatable object that holds the crate and
  Rust's standard library. A libtool library takes it through a libtool object (`.lo`), so the
  shared library links all of it and the static archive holds it. libtool hands Rust's native
  libraries on to whatever links the library.
- A source file's unit takes the file out of the library's `_SOURCES`. When `configure.ac`'s
  `AC_CONFIG_SRCDIR` names that file, the unit names a file that stays, or configure would stop.
- A program gets no sources (`kvdump_SOURCES =`) and a link that copies its Rust `bin`
  (`kvdump_LINK`), so make puts the Rust program where libtool's wrapper was.
- automake's harness runs each test program as one test (`make:tests/test_value`). Its unit maps
  it to the Rust tests of its checks, `rust = [...]`.

## Programs, tests and the retirement

- A **program's unit** gives the program a Rust `bin` of the same name, which the Makefile
  copies to the program's path, so `make test` and your scripts run the Rust program. The agent
  ports it on the library crates' Rust API and adds tests for the output the test target
  compares with files.
- A **test file's unit** translates each test the C program reports into a Rust test of the
  crate's safe API, mapped in `rust/test-map.toml`. A C test that checks something with no Rust
  meaning (a NULL pointer, freeing) is mapped to a note that says why.
- The **retirement** deletes the C, the headers, the Makefile and the crates' C bridges; the
  workspace then builds and tests alone. A coarse test such as `make:test` maps to the Rust
  tests that took over its checks.

## Reviewing the result

The report's Port section lists the symbol map, the tests without a counterpart, the behavior
the port kept differently (from the maps' notes) and any `unsafe` code. The branch is yours to
review with `git diff`.

## Limits

- Make builds run in Docker only; native Windows builds come later.
- A static automake library (`lib_LIBRARIES`) hands Rust's native libraries only to the links in
  its own `Makefile.am`; a program in another directory that links it needs them added by hand.
- Keeping the C headers as the public API of a Rust library (`keep_source_api`) isn't ready for
  C yet.
