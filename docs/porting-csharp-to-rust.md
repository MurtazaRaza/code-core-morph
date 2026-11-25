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
on (native libraries, an operating system's own, .NET packages) is the same too: the port is built for Linux, so a call into Windows' or macOS's own library only in branches for that platform goes with them (D-94).

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
- Where the unit's types use a .NET package CodeCoreMorph maps (D-95, the plan's table names
  the counterpart): a C library goes in the native module, bindgen reading the headers the
  mapping names (`lz4.h` and `lz4hc.h` for K4os.Compression.LZ4); a crate is added to the
  crate's dependencies at the exact version and features the mapping pins, and to
  `rust/Cargo.lock` with the checksum CodeCoreMorph ships (`xxhash-rust` 0.8.21 for
  K4os.Hash.xxHash), fetched at configure with the network on for that alone, every build after
  offline and `--locked`; Microsoft.Extensions.Logging.Abstractions is the runtime crate's
  `logging` module (kept at the retirement), with the bridge's glue (`src/bridge/ccm_logging.rs`)
  and the façades' support (`CodeCoreMorph.Logging.cs`): the Rust calls back into the C#
  logger the façade passes until the retirement, each call returning `Result` since a C#
  logger may throw. The mappings, what they cover and what they were checked against are the
  port to C++'s ([Packages](porting-csharp-to-cpp.md#packages)), with these counterparts.

The agent writes the Rust and the bridge, turns each C# type into its façade, and maps every
public and protected member. Tests that reach what the library keeps `internal`, through its `InternalsVisibleTo`, are named in
the type's unit with what they use, and the façades keep those members `internal`, over the
bridge, until the tests are translated.
The mapping, in short:

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
  verification runs both builds. It lists for your review what still names a deleted project
  (a second solution, an excluded project) and, unless the C# stays, the CI that runs `dotnet`.

## The checks of each unit

- **I-6** compares each façade with the C# it replaced, member by member and signature by
  signature, as in a port to C++.
- **I-4** compares each translated test's checks with its C# case's.
- **`symbol_map`** wants an entry for every public and protected member of the unit's types,
  each naming what the Rust index has (`parking_rs::money::Money::new`; a trait impl's method
  under the implementing type, `parking_rs::money::Money::fmt` for `Display`).
- **`unsafe_in_bridge`** and **`lint_clean`** are the list in *What is lost*.

## Packages

The mappings of .NET packages and what they were checked against are the port to C++'s
([Packages](porting-csharp-to-cpp.md#packages), D-95); in Rust, K4os.Compression.LZ4 is liblz4
through the native module (its blocks byte for byte with K4os's, which `lz4_flex`'s aren't),
K4os.Hash.xxHash the crate `xxhash-rust` 0.8.21, and Microsoft.Extensions.Logging.Abstractions
the runtime crate's `logging` (Type units, above). A C library's development files must be in
the run's image: the port image has liblz4's and libxxhash's; for another image, build one
with them as for a native library.

## Async code

A type with `async` members ports (D-96). The first unit with async code places the tasks
runtime: the runtime crate's `tasks` module (kept at the retirement), the bridge's
`src/bridge/ccm_tasks.rs` and the façades' support `CodeCoreMorph.Tasks.cs`. No async crate is
added: the runtime is a small executor of CodeCoreMorph's own, in safe Rust.

- An `async Task<T>` member returns `codecoremorph_runtime::tasks::Task<T>`, its body in
  `tasks::spawn(async move { .. })`. As in C#, `spawn` runs the body on the caller's thread
  until its first `.await` of something not done, so argument checks fault the task; `Task<T>`
  is a `Future` of `Result<T>` that many may await, and `wait()` blocks for it (a test).
  `when_all`, `delay`, `TaskCompletionSource` and the token types are .NET's, and a sleep the
  token cuts short in synchronous code (`token.WaitHandle.WaitOne(t)`) is the token's `wait`.
- Rust asks more of async code than C#: the body is `'static`, so an async member takes
  `self: &Arc<Self>` and moves a clone into the body, and it holds no `MutexGuard` across an
  `.await`. A `catch (TransientException)` is `Err(e) if e.is_a("Relay.TransientException")`,
  which also matches derived types, as the C# `catch` does.
- Across the bridge it is the C++ port's C ABI: an async export completes its façade's C#
  task, a C# task is awaited as a native one, a token crosses with its origin (and back with a
  cancellation the Rust raises, D-97), and a C# exception reaches the Rust as an `Exception`
  of its .NET types, the façade throwing the same C# object when it comes back. That last
  holds in every port. A log entry carries its state as in C++ ([Packages](porting-csharp-to-cpp.md#packages)):
  an argument's `LogArg::log_value` is its typed value, and the port's own `TimeSpan`
  implements it (`DotnetValue::time_span(ticks)`).
- **Still refused**, by site: as for C++ (`async void`, `ValueTask`, `await foreach`, the task
  API beyond what the runtime covers, .NET's own tasks).

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

## The third fixture: hostkit

`fixtures/csharp/hostkit` is the port to C++'s ([hostkit](porting-csharp-to-cpp.md#the-third-fixture-hostkit)): a library that calls kernel32 on Windows and the C library elsewhere, each call behind a check of the platform (D-94). Its plan is the same 5 units. Ported whole through `codecoremorph run --host-agent` (5 of 5 units, each on its first attempt), the Rust uses std's counterparts of the C library's calls: `Instant` for the monotonic clock (counting from its first reading, since std keeps the clock's value to itself), `std::process::id`, and `io::Error::from_raw_os_error` for `strerror`'s text. No unsafe code is left. It replays from the recorded edits beside parking's.

## The fourth fixture: stash

`fixtures/csharp/stash` is the port to C++'s ([stash](porting-csharp-to-cpp.md#the-fourth-fixture-stash)): a library over K4os.Compression.LZ4, K4os.Hash.xxHash and Microsoft.Extensions.Logging.Abstractions (D-95). Its plan is the same 6 units. Ported whole through `codecoremorph run --host-agent` (6 of 6 units, each on its first attempt), the Rust calls liblz4 through its native module (the only unsafe code left), digests with `xxhash-rust` pinned in `Cargo.lock`, and logs through the runtime crate's `logging`, its translated tests' loggers implementing `Logger`. It replays from the recorded edits beside parking's.

## The fifth fixture: relay

`fixtures/csharp/relay` (D-96; [the C++ guide](porting-csharp-to-cpp.md#the-fifth-fixture-relay)
describes it) ported whole to Rust through `codecoremorph run --host-agent`, 7 of 7 units, each
on its first attempt: the C# tests pass through the façades at every commit, and the retired
crate passes its 14 tests alone, with unsafe code forbidden. It replays from
the recorded edits beside parking's.

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

## The second benchmark: EasyRetry

The M13 benchmark is the port to C++'s ([EasyRetry](porting-csharp-to-cpp.md#the-second-benchmark-easyretry))
with Rust as the target (`bench/lifts-rust-pin.json`): 657 lines of async C# 12 that log through
an `ILogger<T>`, 148 NUnit cases, the same plan of 22 units. Headless Claude Code (Opus 5.5)
ported it whole in 86 minutes, 1.75 of its estimate, every unit at its first attempt. The C#
tests passed through the façades at every commit; the final Cargo build passes 147 Rust tests,
146 of the 148 C# tests map to them, and the other 2 pass a null delegate, which a Rust closure
can't be. No `unsafe`, and two shared cells: the options' state and their list of exception
types, which C# lets their owner change while a retry reads them. As for C++, three of the six
types took two thirds of the agent's time.

Over 10,325 seeded lines, the C# façades over the Rust did what the C# did but in three ways: a
log entry reached the C# logger as its formatted text, without the template's named values; an
`OperationCanceledException` the Rust raised had no `CancellationToken`; and an
`ArgumentOutOfRangeException` had no actual value, so its message lacked the line that shows it,
since `Exception::argument_out_of_range` took none (the C++ port wrote an exception type of its
own that carries it). D-97 closed all three (`Exception::argument_out_of_range_value`,
`task_canceled_by`, a log entry's state), and the exception type of its own this port wrote to
log a C# exception as the same object: with that runtime and the few lines of the port's code
that use it, the façades match the C# on every seeded line, and the replanned estimate is 0.96
of this run's time.

## Limits

The first version refuses what a port to C++ refuses ([Limits](porting-csharp-to-cpp.md#limits)):
async code the tasks runtime doesn't take over ([Async code](#async-code)), `dynamic` the
index can't settle, reflection and attribute-driven behavior, expression trees, a type that
uses a .NET package's API CodeCoreMorph doesn't map or an operating system's own library where Linux reaches the call,
public generic types nothing instantiates, a ported constructor that calls a virtual member a C# class overrides, a class
with non-private instance fields, and types in more than one library project. A C# class
outside the port that derives from a ported class runs on a director, as for C++ (D-87): here
the hierarchy is a trait over the base's state whose virtual members return `Result` (a C#
override may throw), and each class's own implementations take the object they run on, so a
base call made for a C# subclass's object still reaches its other overrides; at the retirement
a test's subclass is a struct holding the ported class it derived from. `dynamic` is ported
where the index settles it, as for C++ (D-88): a dispatch on a hierarchy's runtime type
downcasts the trait object, values of unrelated types are an `enum` matched at each site,
and a binder error is `Exception::runtime_binder`. Also:

- A registry dependency only where a package mapping pins one (D-95): the crate's version and
  checksum are CodeCoreMorph's, fetched at configure; every build is offline.
- Strings are UTF-8: C#'s lengths and indices count UTF-16 units, the same for ASCII; the symbol
  map's notes say where a port depends on the difference.
- A call into the C library (`libc` through P/Invoke) has no header in Rust: the guidance doesn't say yet what to call instead, and std has no counterpart for some (a clock's raw value).
- Runs in Docker only for now.
