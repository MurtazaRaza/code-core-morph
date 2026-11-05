# Porting C++ to Rust

CodeCoreMorph ports a C++ project to Rust one module at a time, leaves first, and then finishes
the job: the programs become Rust programs, the tests become Rust tests, and the C++ and its
CMake build go, leaving a Cargo workspace. The branch builds and passes its tests at every
commit, and the project's own tests check every step.

While modules are being ported, each module's header stays as a C++ **façade**: its public API
is unchanged, and its sources forward every call to the Rust code. So the C++ that uses the
module, and the project's C++ tests, don't change. Once everything is Rust, the C++ tests are
translated case by case, each paired with its Rust test in a **test map**, and the last unit
retires the C++.

Any C++98 or newer project works; nothing has to be modernized first. Code whose ownership is
already explicit (`std::unique_ptr` rather than raw `new`) ports more naturally, so running the
C++ upgrade first (with `convert_raw_pointers_to_smart_pointers`) can help.

A smaller variant ports only the C++ behind a C header (functions declared `extern "C"`) to a
crate that exports the same C interface: [below](#porting-a-c-interface).

## What you need

- A CMake project whose tests build and pass, as for any migration.
- The Rust toolchain. Natively: `codecoremorph toolchain install rust` (it installs the C++
  toolchain too, since a port builds both). In Docker: build the image once with
  `scripts/build-image rust` (`.bat` on Windows).

## The manifest

```yaml
version: "1.0"
project:
  name: tinycalc
  source_dirs: [src, include, app]
  test_dirs: [tests]
  public_api: ["include/**"]
migration:
  source: {language: cpp, standard: cpp98, build_system: cmake}
  target:
    language: rust
    standard: rust2021
    rules: [port_module, port_executable, port_tests, retire_cpp]
```

The target's standard is a Rust edition (`rust2015` to `rust2024`). A port has no standard bump
and takes port rules only; `codecoremorph validate` checks each side against its own adapter.

The rules run in that order, and you can stop earlier: `rules: [port_module]` ports the library
and leaves the programs, the tests and the C++ façades in place. Each rule reads the tree as it
is, so a later run on the branch plans only what is left.

## The plan

`codecoremorph plan` reads the CMake targets, the CTest entries and the file graph, and makes:

1. **One unit per module** of each library target (`port_module`): a header and its sources, in
   dependency order, so a module's dependencies are Rust before it is. A header-only template
   goes with the last module that uses it.
2. **One unit per program** (`port_executable`): each CMake executable that isn't a test, once
   the modules it uses are Rust.
3. **One unit per test file** (`port_tests`): each GoogleTest source, once the modules it uses
   are Rust.
4. **One retirement unit** (`retire_cpp`), last.

A unit lists the units it needs; if one of them doesn't commit, the run skips it and says why.

Each module's code goes to a Cargo workspace beside the CMake project, one crate per CMake
library target (`rust/<target>_rs/`), which CMake builds and links into that target with
[Corrosion](https://github.com/corrosion-rs/corrosion).

## Module units

1. The `rust-module` codemod creates the workspace and crate the first time, and the unit's
   module: `src/<module>.rs` for the port (safe Rust) and `src/bridge/<module>.rs` for its C
   interface, the one place `unsafe` code may go. The crate also gets `include/<crate>/bridge.hpp`
   (helpers that pass `std::string` both ways) and `cbindgen.toml`.
2. The agent ports the module's logic to Rust, writes thin `#[no_mangle] extern "C"` wrappers in
   the bridge, and turns the module's C++ sources into a façade that calls them. The bridge's C
   header, `include/<crate>/bridge.h`, is written by cbindgen from the crate before every build.
3. The agent adds the module's names to `rust/symbol-map.toml`: which Rust item took over each
   C++ function and type, with a note where behavior differs.
4. CodeCoreMorph builds the project with CMake, runs the C++ tests and the crates' Rust tests,
   and checks the unit:
   - every function of the module forwards to Rust, or is trivial;
   - the headers' public API is unchanged;
   - `unsafe` code stays in the bridge, and clippy has nothing to say about the new code;
   - the symbol map covers the module's names;
   - a header with C linkage is still exported with the same signatures.

   Cargo's and clippy's messages go back to the agent when something fails.

The unit may edit the module's own files, the CMake files and anything under `rust/`, but not
the tests or the C++ that uses the module. Builds run offline and `--locked`. New crates from
crates.io, build scripts (`build.rs`) and procedural macros are refused.

The rule's guidance tells the agent how C++ maps to Rust: ownership (`unique_ptr` → `Box`,
`shared_ptr` → `Arc`), errors (exceptions → `Result`, with the façade throwing the same
exception again), class hierarchies → traits (with C++ subclasses called back through a
function table), strings as bytes, integer semantics, static state and threads.

## Program units

The `rust-bin` codemod adds a package `rust/<target>_rs/` whose `bin` keeps the C++
executable's name, depending on the crates of the libraries it linked, and takes the C++
target's commands out of the CMake files. Corrosion imports the Rust program as a CMake
executable of the same name, so `add_test(... COMMAND <name> ...)` and your scripts keep working.
The agent writes `src/main.rs` (same arguments, output and exit codes), deletes the C++ sources,
and translates the CTest entries that run the program into the package's integration tests.

## Test units

Each C++ test file is translated into an integration test of the crate it tests,
`rust/<crate>/tests/<file>.rs`, one Rust test per GoogleTest case. The C++ file stays as it is
and keeps running until the retirement, so both versions pass in between. Each pair goes into
`rust/test-map.toml`:

```toml
[[test]]
cpp = "gtest:Rational.Parse"
rust = "nextest:calc_rs::rational_test::parse"
note = "Rational::parse returns an Option where the C++ wrote into an argument."
```

The map is the port's parity. While a C++ test runs, it must pass and so must its Rust test;
once the C++ test is gone, its Rust test stands for it. A test with no Rust counterpart (a
CTest entry that only ran a GoogleTest binary, behavior Rust can't have) gets an entry without
`rust` and a note that says why. The assertion guard compares each pair's checks and flags a
Rust test that checks less than its original, for review.

## The retirement

The last unit's codemod, `rust-retire`, deletes the C++ files and the CMake build, removes each
crate's C bridge (`src/bridge/`, `include/`, `cbindgen.toml`, the `staticlib` crate type) and
makes each crate root `#![forbid(unsafe_code)]`. The agent fixes what is left: code only the
bridge used, docs that describe the C++. From then on Cargo alone builds and tests the project,
and the final verification builds and tests the workspace from clean, `--locked` and offline.

To keep a C++ API for outside consumers, set `migration.target.keep_source_api: true`: the
public headers stay, with the sources and bridge behind them, the CMake build and the C++ tests
that use only them, as a thin C++ library over the Rust crates.

## The report

Besides the usual sections, a port's report lists the symbol map and test map sizes, every note
the agent recorded (behavior the port couldn't keep, or kept differently), the tests without a
Rust counterpart, each use of `unsafe` with its location, and any C++ left with the reason.

## A real project: snappy

The benchmark of `bench/port-pin.json` is google/snappy 1.1.9, about 6k lines of C++11. Its
manifest shows what a real project needs:

```yaml
project:
  name: snappy
  test_dirs: [snappy_unittest.cc, "snappy-test.*", "snappy_test_data.*"]
  exclude: ["third_party/**", snappy_benchmark.cc, "snappy_*_fuzzer.cc"]
  public_api: [snappy.h, snappy-c.h, snappy-sinksource.h]
migration:
  source: {language: cpp, standard: cpp11, build_system: cmake}
  target:
    language: rust
    standard: rust2021
    rules: [port_module, port_executable, port_tests, retire_cpp]
build:
  cmake:
    configure_args:  # snappy asks for CMake 3.1, older than the toolchain supports
      [-DCMAKE_POLICY_VERSION_MINIMUM=3.5, -DSNAPPY_BUILD_BENCHMARKS=OFF, -DSNAPPY_INSTALL=OFF]
```

- snappy keeps its tests beside its code, so `test_dirs` names them by file. The
  characterization tests CodeCoreMorph writes count as tests whatever `test_dirs` says.
- What the build doesn't compile (the benchmark, the fuzzers) can't be checked, so it stays out
  of the port through `exclude`; the report lists it as left.
- A CMake install step that exports the library fails once the library links a Rust crate
  (Corrosion's imported targets can't join an export set): turn it off for the port, or make
  it the agent's to fix.

The port took 12 units: the four modules (the compressor among them, 2.2k lines of C++), the
test tool as a Rust program, six test files (snappy's own and five of characterization tests)
and the retirement. Claude Code ran it in 47 minutes, every unit committed, 58 of the 62
original tests mapped to Rust tests and the others noted, with no `unsafe` left. The fast
paths (SSSE3, BMI2) didn't come along; the symbol map's notes say so. clippy's own fixes are
applied to the new code before each build, so mechanical lints cost the agent no attempt.

## Porting a C interface

With `rules: [port_c_abi_module]`, a unit is the C++ code behind one C header: the agent
replaces it with a Rust crate (`rust/<project>_<module>/`) that exports every function the
header declares, deletes the C++ sources and takes them out of CMake. The header stays exactly
as it is, read-only, and CodeCoreMorph compares it with the C header the crate really exports.

## With Claude Code

The `codecoremorph` skill knows about ports: ask Claude Code to "port this project to Rust",
and it runs the plan's units as for any migration. See
[Migrating with Claude Code](claude-code.md).
