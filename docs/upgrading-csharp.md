# Upgrading C#

CodeCoreMorph upgrades a C# project from one language version to a newer one, from C# 7.3 up
to C# 14, moves its projects to the .NET release that version belongs to, and modernizes its
code on the way: file-scoped namespaces, pattern matching, switch expressions, `using`
declarations, target-typed `new`, collection expressions, records and nullable reference
types. As for C++ and Java, every change is built and tested in the run's own worktree, and
only verified units are committed.

The standards are the language versions: `csharp7.3`, `csharp8`, `csharp9`, `csharp10`,
`csharp11`, `csharp12`, `csharp13` and `csharp14`. Each goes with the .NET release whose
default it is (C# 12 with .NET 8, C# 14 with .NET 10).
- **A project with a numeric `LangVersion`:** that number names its standard. Versions older
  than 7.3 build as `csharp7.3`.
- **Without one, or with `latest` or `preview`:** its target framework names it. `net6.0` is
  `csharp10`, and `netstandard2.0` is `csharp7.3`.

## What you need

- **A project that builds and passes its tests at the base commit:** MSBuild projects
  (`.csproj`, SDK-style), with a solution (`.sln` or `.slnx`) or a single project at the top.
  Without either, CodeCoreMorph builds a solution of its own with every project.
- **The C# toolchain.** Either one works:
  - Natively: `codecoremorph toolchain install csharp`. It installs the .NET 10 SDK, with
    `dotnet format` and Roslyn, plus the .NET 8 and .NET 6 runtimes beside it (about 1 GB).
  - In Docker: build the image once with `scripts/build-image csharp` (`.bat` on Windows).

## The manifest

`codecoremorph init` writes one for you. A C# 7.3 to C# 14 upgrade looks like this:

```yaml
version: "1.0"
project:
  name: order-book
  source_dirs: ["."]
  test_dirs: ["tests"]
migration:
  source: {language: csharp, standard: csharp7.3, build_system: dotnet}
  target:
    language: csharp
    standard: csharp14
    rules: [use_file_scoped_namespaces, use_pattern_matching, convert_switch_statements,
            use_using_declarations, use_target_typed_new, use_collection_expressions,
            use_record_types, enable_nullable]
```

`build.dotnet.configure_args` passes arguments to every `dotnet` command, such as a property:
`build: {dotnet: {configure_args: ["-p:Configuration=Release"]}}`.

## What a run does

1. **The bump comes first** (`bump_csharp_version`, automatic).
   - **Frameworks:** a `net` or `netcoreapp` framework older than the target's .NET becomes
     the target's (`net6.0` becomes `net10.0` for C# 14). `LangVersion` becomes the target's
     version.
   - **`netstandard` libraries** keep their framework and get the target's language version.
   - **What breaks** after the bump goes to the agent. `BinaryFormatter`, an error from .NET 8
     that throws at run time, is the common case: a deep copy made with it becomes a copy made
     by hand.
2. **The modernization rules** each run `dotnet format` with the rule's own fixes (IDE0161 for
   file-scoped namespaces, IDE0066 for switch expressions, and so on) on the files of one unit.
   - **Repeated passes:** `dotnet format` runs again while a pass still changes something,
     since one fix can expose the next.
   - **What the fixers leave** goes to the agent. When it should stay, the agent submits no
     edits and says why, and the unit is verified as it is. Examples: an `Array.Empty<T>()`
     returned through an interface, where `[]` would build another type; a `using` whose
     scope matters.
   - **`use_record_types`** has no fixer: the agent turns value classes into records,
     keeping their members.
   - **`enable_nullable`** adds `#nullable enable` file by file, with the annotations that
     leave each file without nullable warnings. Test files are left as they are.
3. **The final verification** builds the branch from nothing and runs every test.

A test project runs on its framework's runtime: the baseline of a `net6.0` project runs on .NET
6, so a behavior a later .NET changed shows only after the bump, where the agent sees it. A
framework without its runtime (`netcoreapp3.1`, `net5.0`, `net7.0`) runs on the next one up.

## Builds, packages and the network

`dotnet` writes `bin/` and `obj/` beside every project, which must never end up in a commit, so
CodeCoreMorph builds in a copy of the worktree in its cache.
- **Your own .NET setup is never read:** not your NuGet config, packages or .NET home. Each run
  keeps its packages in its own cache.
- **The project's NuGet config is used:** a `nuget.config` at the project's root, if there is
  one. Otherwise only nuget.org is.
- **A `global.json` SDK pin is dropped** in that copy: the toolchain's SDK builds every
  project.

Builds, tests and `dotnet format` run offline. Packages are restored in a separate step with the
network on (`sandbox.network: fetch_only`), and only when the project files change. MSBuild runs
targets and property functions while it reads the projects. So before the network goes on,
CodeCoreMorph refuses code-running constructs and package sources the project didn't have when
the run began: `Exec`, `UsingTask`, `DownloadFile`, targets, imports and other MSBuild SDKs.

A unit may not add any of those either, nor analyzers, restore sources, lines in `nuget.config`
or an MSBuild response file (invariant I-12).

## Tests

The TRX reports give each test the ID `dotnet:<class>#<test>`. Nested classes take a `+`
(`dotnet:Shop.Tests.CartTests+Totals#Rounds`). A parameterized case includes its arguments, as
its framework names them: `Describes(3,"x")` for NUnit, `Describes(n: 3, text: "x")` for xUnit.
NUnit, xUnit and MSTest all work.
- **Disabling a test (I-3):** `[Ignore]`, `[Explicit]`, `Skip =`, `Assert.Ignore`,
  `Assert.Inconclusive` and assumptions all count.
- **Assertions (I-4)** are compared before and after each unit: `Assert.…`,
  `CollectionAssert.…`, FluentAssertions' `Should()` and the like.

## Limits

- **SDK-style projects are the target.** An older project builds only if its references
  resolve without Visual Studio. .NET Framework tests run on Windows only.
- **Windows paths:** with long paths off, keep `CODECOREMORPH_HOME` short. NuGet's package
  folder and the build copy live under it, and their deepest files can pass Windows'
  260-character limit.
- **Characterization tests** for untested C# code aren't written yet: a C# run skips that
  step, and its report says so.
