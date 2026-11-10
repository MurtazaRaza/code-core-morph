# Porting C# to C++

CodeCoreMorph ports a C# library (C# 7.3 or newer, built with MSBuild, tested with NUnit, xUnit
or MSTest) to C++17, 20 or 23 one type at a time, then finishes the job: the programs become
C++ programs, the tests become GoogleTest tests, and the C# goes, leaving a CMake project. The
branch builds and passes its tests at every commit. It is for native deployment without the
.NET runtime, embedding the code in a C++ host, and platforms .NET doesn't reach. Or the C# API
stays, as a .NET library whose implementation is C++ (`keep_source_api`).

Each ported type keeps its C# API as a **façade**: its members keep their signatures, and their
bodies call the C++ through P/Invoke. So the rest of the library and the project's own C# tests
keep using the type unchanged, and they decide, unit by unit, whether the port is right.

## What is lost, and what replaces it

The garbage collector goes, and with it the runtime's checks: array bounds, null dereferences,
integer division by zero, and reflection. In their place, every unit's C++ must pass:

- the compiler with `-Wall -Wextra`, with no warning;
- clang-tidy's bug-finding checks (`bugprone-*`, `clang-analyzer-*`), with no finding;
- no owning raw pointer outside the bridge (ownership is `std::shared_ptr` where the C# shares an
  object, `std::unique_ptr` or a value where it has one owner), and no cast between pointer
  types outside it;
- every test under AddressSanitizer and UndefinedBehaviorSanitizer, from the first unit on. The
  C# tests run the C++ inside the .NET test host, with the shared ASan runtime preloaded, so an
  out-of-bounds access, a use after free or signed overflow aborts the test run with the
  sanitizer's report.

These are post-conditions (`lint_clean`), not advice. Arithmetic C# defines and C++ doesn't is
the port's to keep: unchecked overflow wraps in C#, so the C++ does it in unsigned types; a
`checked` context throws `ccm::OverflowException`.

## What you need

- A solution or project whose `dotnet build` and `dotnet test` pass. The types to port are in
  one library project; its programs (console projects) and test projects come along.
- Docker, and the image for ports from C#: `scripts/build-image csharp-port` (`.bat` on
  Windows). It holds the .NET SDK and runtimes, the Roslyn index helper, and the C++ toolchain:
  clang, clang-tidy, CMake, Ninja and GoogleTest. The restore step uses the network.

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
    language: cpp
    standard: cpp17
    rules: [port_csharp_type_to_cpp, port_csharp_program_to_cpp, port_csharp_tests_to_cpp,
            retire_csharp_to_cpp]
sandbox: {mode: docker}
```

`migration.target.keep_source_api: true` keeps the library as a .NET library over the C++.

## The plan

`codecoremorph plan` indexes the C# with Roslyn (every type and member with its signature, the
generic instantiations in use, the exceptions each member throws, each test case by the ID the
test report gives it) and cuts:

1. **One unit per type, leaves first** by the references between types, so what a type uses is
   C++ before it. A reference cycle is one unit, and so is a class with virtual members together
   with the classes that derive from it, since C++ can't dispatch a virtual call to a C#
   override. Nested types go with their outer type; a partial type is one type; an interface is
   a unit of its own.
2. **One unit per program**, after the library types it uses.
3. **One unit per test file**, after the types its tests use; its C# stays, read-only, until
   the retirement.
4. **The retirement.**

A unit's note names what to know: the public members (which the symbol map must cover), each
generic's type arguments in use (one set of exports per argument), the static state, the
exceptions thrown, and what calls back into C# (delegates, events, the C# classes that
implement an interface, test doubles among them).

## Type units

The codemod makes the C++ build on the first unit and adds to it on each:

- `CMakeLists.txt` at the top (the `CODECOREMORPH_SANITIZE` option, which CodeCoreMorph's builds
  turn on) and `cpp/CMakeLists.txt`: the static library `<name>_cpp` of the ported code, which
  stays, and the shared library `<name>_native` of the bridge, which the façades load.
- The runtime CodeCoreMorph ships, which no unit edits: `ccm::Decimal` (.NET's `decimal`, bit for
  bit, scale included), the mirrors of .NET's exceptions, and the bridge's helpers.
- `cpp/src/<unit>.hpp` and `.cpp`, and `cpp/bridge/<unit>.h` and `.cpp`, empty: the C++ and its
  `extern "C"` exports go there.
- Beside the library's project file: `CodeCoreMorph.Interop.cs`, the façades' support (status
  checks, strings, the identity table that gives an object coming back from C++ its own C#
  façade), and `CodeCoreMorph.Native.g.cs`, the C# declarations of the exports, which
  CodeCoreMorph writes from the bridge headers at every configure.
- `cpp/symbol-map.toml`: where each C# member went, by its symbol ID.

The agent writes the C++ and the bridge, turns each C# type into its façade, and maps every
public and protected member. The mapping, in short:

| C# | C++ |
|---|---|
| a class | a class held by `std::shared_ptr` (a handle across the bridge, released by a `SafeHandle`) |
| a struct | a value type; it crosses the bridge as its fields |
| an interface | an abstract class; a C# implementation reaches it through a proxy |
| a generic type | a template, exported once per type argument in use |
| `decimal` | `ccm::Decimal` |
| `string` | UTF-8 `std::string`; null is handled at the bridge, as the C# handled it |
| an exception | the mirror of the .NET type, which the façade throws again in C# |
| a delegate, an event | `std::function`; the C# side's are calls back into C# |
| `List<T>`, `Dictionary<K,V>` | `std::vector`, `std::unordered_map` (in insertion order where the C# enumerates it) |
| `IDisposable` | `dispose()` and the destructor |
| LINQ | `<algorithm>` |

A project's own exception stays a C# class while the façades last (the C# tests catch it): its
C++ class lists its constructor's arguments, and `CcmExceptions.cs` makes the C# one from them.

## Programs, tests and the retirement

- A **program's unit** moves the program into the library as `<program>_main` (its arguments
  without the program's name, its exit code back). The C# `Main` becomes a façade that calls the
  export, and a CMake program of the same name calls it too.
- A **test file's unit** translates its cases to `cpp/tests/<file>.cpp`, a GoogleTest program
  that CTest runs, each case a test (a parameterized case a `TEST_P` instance or a test of its
  own), each checking at least as much as the original (I-4 compares the counts case by case).
  `cpp/test-map.toml` pairs each `dotnet:` ID with its `gtest:` IDs. A case C++ can't have gets
  an entry without `cpp` and a note: a test that passes a null string, where the C++ takes a
  `std::string`. Both suites run until the retirement.
- The **retirement** deletes the C# files, the projects, the solutions, the MSBuild files
  (`Directory.Build.*`, `global.json`, `nuget.config`), the façades' support and the bridge, so
  CMake builds and tests the C++ alone. With `keep_source_api` only the programs go, with the
  test projects that need them: the library, its façades, the bridge and its C# tests stay,
  and the final verification runs both builds.

## The checks of each unit

- **I-6** compares each façade with the C# it replaced, member by member and signature by
  signature: the library's public and protected members, as other assemblies see them (a
  property's private setter, or an internal interface a façade implements, is no part of it).
- **I-4** compares each translated test's checks with its C# case's.
- **`symbol_map`** wants an entry for every public and protected member of the unit's types,
  each naming what the C++ index has (`parking::Money::to_string`, `parking::operator+` for a
  friend operator).
- **`lint_clean`** is the list in *What is lost*; the retirement's covers all of the C++.

## Reviewing the result

The report lists every unit, the symbol map's notes (what the C++ keeps differently: a hash that
isn't .NET's, a static field that became a function), the test map, and the tests without a
counterpart, each with its note. The final verification is a clean CMake build whose tests, the
translated C# tests, pass under the sanitizers.

## The fixture: parking

`fixtures/csharp/parking` is a garage that charges for stays, in C# 7.3: a `netstandard2.0`
library, a `net6.0` console program and 60 NUnit tests. It has a value type over `decimal` with
equality and operators, an abstract class with two subclasses, an interface with two
implementations and a test double, a generic type used with `int` and `string`, a project
exception, an event, a delegate parameter, an indexer, LINQ, `IDisposable` and a static class of
constants. Its plan is 17 units: ten of types, the program, five test files and the retirement.
Ported whole through `codecoremorph run --host-agent` (17 of 17 units, 34 minutes, the
retirement by its codemod alone), it replays from the recorded edits in
`tests/integration/test_csharp_to_cpp_e2e.py`. The C# tests passed through the façades after
every type unit, the CMake program prints what the C# one did, and 58 of the 60 tests are
GoogleTest tests now; the other two passed a null string. With `keep_source_api` the same edits end as a .NET library over the C++: the retirement takes only the program, and the final verification runs the 60 C# tests through the façades and the C++ tests under the sanitizers.

## Limits

- One library project per port: types to port in several are refused.
- Refused, with the reason in the plan: `async` code, `dynamic`, reflection, serializers and
  dependency injection with their attributes, `unsafe` code, expression trees, a public generic
  type or method nothing in the project instantiates, a class with non-private instance fields
  (a façade can't forward a field), a class that C# outside the port derives from and overrides,
  and a type that uses a refused one.
- A façade's protected constructor is kept, but a C# class deriving from a ported class outside
  the library has no C++ to run.
- Strings are UTF-8 in C++: C#'s lengths and indices count UTF-16 units, and `ToUpperInvariant`
  and ordinal comparison agree with the C++ for ASCII; the symbol map notes where code depends on
  more.
- A parameterized method whose cases come from data (`[TestCaseSource]`, `[MemberData]`) is
  compared by the method, not case by case.
- Docker only, for now: a native Windows run hasn't been tried.
