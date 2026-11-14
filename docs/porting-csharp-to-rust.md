# Porting C# to Rust

CodeCoreMorph ports a C# library (C# 7.3 or newer, built with MSBuild, tested with NUnit, xUnit
or MSTest) to Rust one type at a time, then finishes the job: the programs become Cargo
programs, the tests become Rust integration tests, and the C# goes, leaving a Cargo workspace.
The branch builds and passes its tests at every commit. It is for native deployment without the
.NET runtime, and for the usual reason to move part of a .NET codebase: a library rewritten
without a garbage collector, kept behind its .NET API (`keep_source_api`), so .NET callers
don't change.

Each ported type keeps its C# API as a **façade**: its members keep their signatures, and their
bodies call the Rust through P/Invoke. So the rest of the library and the project's own C# tests
keep using the type unchanged, and they decide, unit by unit, whether the port is right. The
façades are exactly those of a [port to C++](porting-csharp-to-cpp.md): the Rust implements the
same C ABI the C++ bridge does (D-84), so everything on the C# side (the façades, the generated
declarations, the support file) is the same.

## What is lost, and what replaces it

The garbage collector goes, and with it C#'s freedom to share and mutate objects anywhere. What
replaces the runtime's checks is Rust's:

- the borrow checker, over safe Rust: no `unsafe` outside each crate's bridge module and, where
  the C# called a native library, its `native` module (`unsafe_in_bridge`), the crate root
  denying it everywhere else, and forbidding it after the retirement (denying it but in
  `native`, which stays);
- clippy, quiet about each unit's code (`lint_clean`, after clippy's own fixes);
- the tests in a debug build, where integer overflow panics: what C# lets wrap, the Rust writes
  as `wrapping_*`; what C# checked (`checked`, `CheckForOverflowUnderflow`) as `checked_*`
  returning `Exception::overflow`.

Every export runs in `ccm::guard`, which catches panics (none unwinds into .NET) and turns an
error or a panic into the .NET exception the façade throws, so a mistake fails the C# test that
reaches it with a message, not the test host.

**Ownership is the hard part.** C# shares mutable objects freely, and Rust needs an owner. The
guidance's order: values and owned trees first; a graph with back references as indices into a
`Vec`; shared objects as `Arc` (what a handle holds anyway), a type C# mutates while shared
keeping its state behind its own locks; `Rc<RefCell<T>>` only inside a type that never crosses
the bridge. A type **never holds a lock while it calls out** (a closure, a subscriber, a trait
object a C# class implements), since C# may call back into it. The report counts each unit's
shared cells (the `Mutex`, `RwLock`, `RefCell` and `Cell` fields and statics), not as a gate but
as what to review: each is shared mutation the port kept rather than redesigned.

## What you need

- A solution or project whose `dotnet build` and `dotnet test` pass. The types to port are in
  one library project; its programs (console projects) and test projects come along.
- Docker, and the image for ports from C#: `scripts/build-image csharp-port` (`.bat` on
  Windows). It holds the .NET SDK and runtimes, the Roslyn index helper, Rust (cargo, clippy,
  cargo-nextest, cbindgen) and the C++ toolchain, whose libclang reads the bridge's header. The
  restore step uses the network; Cargo never does.

## The manifest

```yaml
version: "1.0"
project:
  name: parking
  source_dirs: [src]
  test_dirs: [tests]
migration:
  source: {language: csharp, standard: csharp7.3, build_system: dotnet}
  target:
    language: rust
    standard: rust2021          # rust2018 to rust2024
    rules: [port_csharp_type_to_rust, port_csharp_program_to_rust, port_csharp_tests_to_rust,
            retire_csharp_to_rust]
sandbox: {mode: docker}
```

`migration.target.keep_source_api: true` keeps the library as a .NET library over the Rust.

## The plan

The plan is a port to C++'s ([the plan](porting-csharp-to-cpp.md#the-plan)): one unit per type,
leaves first by the references between types (a reference cycle is one unit, and so is a class
with virtual members together with the classes that derive from it); one unit per program; one
per test file; the retirement. Each unit's note names the public members to map, the generics'
type arguments in use, the static state, the exceptions thrown, what calls back into C#, and
what the C# runtime did that the Rust does itself. The plan's table of what the library depends
on (native libraries, an operating system's own, .NET packages) is the same too.

## Type units

The codemod makes the workspace on the first unit and adds to it on each:

- `rust/Cargo.toml`, the workspace, with its lockfile, and `rust/codecoremorph_runtime`, the
  crate CodeCoreMorph ships: `Decimal` (.NET's `decimal`, bit for bit, scale included: checked
  against .NET on 1,000,000 cases) and `Exception` (.NET's exceptions by type name, message,
  parameter and fields, with `Result<T>`). It stays after the retirement.
- `rust/<name>_rs`, the library's crate: a `cdylib`, which the façades load, and an `rlib`, with
  `cbindgen.toml`, `src/lib.rs` (denying unsafe code but in `mod bridge`), and `src/bridge/`
  (`ccm.rs`, the bridge's runtime, and `mod.rs`).
- `src/<unit>.rs` and `src/bridge/<unit>.rs`, empty: the safe Rust and its `#[unsafe(no_mangle)]
  extern "C"` exports go there.
- `include/<name>_rs/bridge.h`, which cbindgen writes from the bridge at every configure, and
  from which CodeCoreMorph writes the C# declarations (`CodeCoreMorph.Native.g.cs`) beside the
  library's project file, with the façades' support (`CodeCoreMorph.Interop.cs`).
- `rust/symbol-map.toml`: where each C# member went, by its symbol ID.
- Where the unit's types call a native library through P/Invoke (the plan's table names it):
  `src/native/mod.rs`, which links the library, and `src/native/<library>_sys.rs`
  (`sodium_sys` for `libsodium`), the declarations bindgen writes from the library's own header
  at every configure. The agent writes a safe function in `native/mod.rs` for each call the
  port makes, each `unsafe` block with a `// SAFETY:` comment; the rest of the crate calls
  those. The run's image needs the library's development files (the M12 benchmark's adds
  Ubuntu's `libsodium-dev`).

The agent writes the Rust and the bridge, turns each C# type into its façade, and maps every
public and protected member. The mapping, in short:

| C# | Rust |
|---|---|
| a class | a struct, shared as an `Arc` (a handle across the bridge, released by a `SafeHandle`); its mutable state behind its own lock |
| a struct | a value type; it crosses the bridge as its fields |
| an interface, a class hierarchy | a trait (`Send + Sync`); a closed hierarchy an enum; a C# implementation reaches the Rust through a proxy |
| a generic type | a generic type, exported once per type argument in use |
| `decimal` | `codecoremorph_runtime::Decimal` |
| `string` | `String`; a null C# allows is `None` |
| an exception | `Err(Exception)` of the .NET type, which the façade throws again in C#; a project's own is `Exception::custom` |
| a delegate, an event | a closure; an event's subscribers by token |
| `null` | `Option` |
| `List<T>`, `Dictionary<K,V>` | `Vec`, `HashMap` (with a `Vec` of keys where the C# enumerates in insertion order) |
| `IDisposable` | `dispose()` and `Drop` |
| LINQ | iterators |

A project's own exception stays a C# class while the façades last (the C# tests catch it): the
Rust returns an `Exception` of its .NET type with its constructor's arguments as fields, and
`CcmExceptions.cs` makes the C# one from them, as in a port to C++.

## Programs, tests and the retirement

- A **program's unit** moves the program into the library as `<program>_main(&[String]) ->
  i32`. The C# `Main` becomes a façade that calls the export, and a Cargo program
  (`src/bin/<program>.rs`) calls it too.
- A **test file's unit** translates its cases to `rust/<name>_rs/tests/<file>.rs`, an
  integration test binary nextest runs, each case a `#[test]` (each case of a parameterized
  test one of its own), each checking at least as much as the original (I-4 counts the
  `assert!` family and `unwrap_err` in each test's body and in the file's functions it calls,
  such as the one a parameterized test's cases share). `rust/test-map.toml` pairs each
  `dotnet:` ID with its `nextest:<crate>::<file>::<test>` IDs. A null C# allows is an `Option`,
  so most null tests translate; a case Rust can't have (a null delegate where the Rust takes a
  closure) gets an entry without `rust` and a note. Both suites run until the retirement.
- The **retirement** deletes the C# files, the projects, the solutions, the MSBuild files, the
  façades' support, each crate's bridge, its header, its cbindgen file and the `cdylib` crate
  type, and forbids unsafe code (a crate with a `native` module keeps it, its declarations as
  bindgen last wrote them, and denies unsafe code elsewhere), so Cargo builds and tests the
  Rust alone, `--locked` and offline. With `keep_source_api` only the programs go, with the test projects that need them:
  the library, its façades, the bridge, the `cdylib` and its C# tests stay, and the final
  verification runs both builds.

## The checks of each unit

- **I-6** compares each façade with the C# it replaced, member by member and signature by
  signature, as in a port to C++.
- **I-4** compares each translated test's checks with its C# case's.
- **`symbol_map`** wants an entry for every public and protected member of the unit's types,
  each naming what the Rust index has (`parking_rs::money::Money::new`; a trait impl's method
  under the implementing type, `parking_rs::money::Money::fmt` for `Display`).
- **`unsafe_in_bridge`** and **`lint_clean`** are the list in *What is lost*.

## Reviewing the result

- The report's port section lists the maps' notes (what the Rust couldn't keep or keeps
  differently), the tests without a counterpart, the unsafe code (none after the retirement),
  and the shared cells by file.
- Read each unit's shared cells first: a `Mutex` around a type's whole state is the C# design
  kept; one around a cache or a counter is usually right.
- The façades are gone with the retirement; `rust/symbol-map.toml` stays as the record of where
  each C# member went.

## The fixture: parking

`fixtures/csharp/parking` is the port to C++'s fixture ([parking](porting-csharp-to-cpp.md#the-fixture-parking)):
a garage in C# 7.3 with a `decimal` value type, a closed class hierarchy, an interface with a
C# test double, a generic type, a project exception, an event, a delegate parameter, LINQ and
`IDisposable`, and 60 NUnit tests. Its plan is the same 17 units. Ported whole through
`codecoremorph run --host-agent` (17 of 17 units, each on its first attempt, 14 minutes, the
retirement by its codemod alone), it replays from the recorded edits in
`tests/integration/test_csharp_to_rust_e2e.py`. The C# tests passed through the façades after
every type unit, the Cargo program prints what the C# one did, and all 60 tests are Rust tests
now: the two that pass a null, which the port to C++ couldn't translate, pass `None`. No unsafe
code is left, and the report counts 4 shared cells: the garage's state and its subscribers,
and a shift's counts (which its subscriber closure shares) and its subscription. With `keep_source_api` the same edits end as a .NET
library over the Rust: the retirement takes only the program, and the final verification runs
the 60 C# tests through the façades and the 60 Rust tests.

## The second fixture: order-book

`fixtures/csharp/order-book` is Phase 10's C# 7.3 fixture, unchanged ([order-book](porting-csharp-to-cpp.md#the-second-fixture-order-book)):
orders with `decimal?` prices, value classes, a snapshot made by a `BinaryFormatter` round trip
in memory, and a `kernel32.dll` declaration nothing calls. Its plan is the same 11 units. The
round trip is a deep copy: `Book::snapshot` makes each order, line and price again. Headless
Claude Code (Opus 5.5) ported it whole in 31 minutes, every unit on its first attempt but the
first, whose first try made the `Money` façade `ISerializable` (so the C# snapshot could
serialize it), which I-6 refused. 29 of its 30 tests are Rust tests now; the other passes a
null currency where the Rust takes a `&str`. No unsafe code is left, and the report counts 2
shared cells (the book's orders, an order's state). It replays from the recorded edits beside
parking's.

## The benchmark: libsodium-core

The M12 benchmark is the port to C++'s ([libsodium-core](porting-csharp-to-cpp.md#the-benchmark-libsodium-core))
with Rust as the target (`bench/csharp-rust-pin.json`): 8.8k lines of C# 9 over libsodium, 230
NUnit tests, the same plan of 68 units, the same image. Headless Claude Code (Opus 5.5) ported
it whole in 3 h 17 min, 0.97 of its estimate, three units taking a second attempt. The C# tests
passed through the façades at every commit; the final Cargo build passes 219 Rust tests, 219 of
the 230 map to them, and the other 11 pass a null, which a Rust slice can't be. The Rust calls
libsodium from `src/native/mod.rs` (82 calls, each in a safe wrapper), the crate's only unsafe
code; the rest is safe Rust with one shared cell, BLAKE2b's state. Over 24,292 seeded calls
with nulls and wrong sizes among the arguments, the C# façades over the Rust return or throw
exactly what those over the C++ did, so both differ from the C# only where the two libsodium
builds do. The port to C++ took 3 h 33 min with the same model: the agent's time was about the
same, and Cargo's checks cost each unit less than CMake's sanitizer build and clang-tidy.

## Limits

The first version refuses what a port to C++ refuses ([Limits](porting-csharp-to-cpp.md#limits)):
`async` and `dynamic`, reflection and attribute-driven behavior, expression trees, a type that
uses a .NET package's API or an operating system's own library, public generic types nothing
instantiates, a class C# outside the port derives from, a class with non-private instance
fields, and types in more than one library project. Also:

- No registry dependency: a port adds no crate, so it builds offline.
- Strings are UTF-8: C#'s lengths and indices count UTF-16 units, the same for ASCII; the symbol
  map's notes say where a port depends on the difference.
- Runs in Docker only for now.
