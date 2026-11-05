# Porting C++ to Rust

CodeCoreMorph ports a C++ project to Rust one module at a time, leaves first. Each module's
header stays as a C++ **façade**: its public API is unchanged, and its sources forward every
call to the Rust code. So the C++ that uses the module, and the project's own C++ tests, don't
change, and the tests check every step. The branch builds and passes its tests at every commit.

Any C++98 or newer project works; nothing has to be modernized first, though code whose
ownership is already explicit (`std::unique_ptr` rather than raw `new`) ports more naturally
(run the C++ upgrade first if you like). This phase ports the library code; the tests and the
executables stay C++, and a later phase translates them and retires the C++.

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
  target: {language: rust, standard: rust2021, rules: [port_module]}
```

The target's standard is a Rust edition (`rust2015` to `rust2024`). A port has no standard bump
and takes port rules only; `codecoremorph validate` checks each side against its own adapter.

## The plan

`codecoremorph plan` reads the CMake targets and the file graph, and makes one unit per module
of each library target: a header and its sources, in dependency order, so a module's
dependencies are Rust before it is. A header-only template goes with the last module that uses
it. Executables and test files aren't ported yet; the plan lists them as skipped.

Each unit's code goes to a Cargo workspace beside the CMake project, one crate per CMake library
target (`rust/<target>_rs/`), which CMake builds and links into that target with
[Corrosion](https://github.com/corrosion-rs/corrosion).

## What a unit does

1. The `rust-module` codemod creates the workspace and crate the first time, and the unit's
   module: `src/<module>.rs` for the port (safe Rust) and `src/bridge/<module>.rs` for its C
   interface, the one place `unsafe` code may go. The crate also gets `include/<crate>/bridge.hpp`
   (helpers that pass `std::string` both ways) and `cbindgen.toml`.
2. The agent ports the module's logic to Rust, writes thin `#[no_mangle] extern "C"` wrappers in
   the bridge, and turns the module's C++ sources into a façade that calls them. The bridge's C
   header, `include/<crate>/bridge.h`, is written by cbindgen from the crate before every build.
3. The agent adds the module's names to `rust/symbol-map.toml`: which Rust item took over each
   C++ function and type, with a note where behavior differs.
4. CodeCoreMorph builds the project with CMake, runs the C++ tests, and checks the unit:
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

## Porting a C interface

With `rules: [port_c_abi_module]`, a unit is the C++ code behind one C header: the agent
replaces it with a Rust crate (`rust/<project>_<module>/`) that exports every function the
header declares, deletes the C++ sources and takes them out of CMake. The header stays exactly
as it is, read-only, and CodeCoreMorph compares it with the C header the crate really exports.

## With Claude Code

The `codecoremorph` skill knows about ports: ask Claude Code to "port this project to Rust",
and it runs the plan's units as for any migration. See
[Migrating with Claude Code](claude-code.md).
