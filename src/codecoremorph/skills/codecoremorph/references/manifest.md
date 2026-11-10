# Writing `migration_config.yaml`

CodeCoreMorph reads one manifest at the repository root. Start with `codecoremorph init`: it
detects the build system, the current standard, the source, test and vendored directories and
the applicable rules, and writes a commented manifest (`--stdout` prints it instead, `--target`
sets the target standard). Check what it says it guessed, above all a standard marked as a
guess, and confirm the target and the rules with the user.

A minimal C++ manifest, for reference:

```yaml
version: "1.0"
project:
  name: my-project
  source_dirs: ["src", "include"]    # directories with code to migrate
  test_dirs: ["tests"]               # directories with the project's tests
  public_api: ["include/**"]         # headers other projects include
migration:
  source: {language: cpp, standard: cpp11, build_system: cmake}
  target: {language: cpp, standard: cpp20, rules: []}
```

A Java manifest (Maven):

```yaml
version: "1.0"
project:
  name: my-service
  source_dirs: ["."]
  test_dirs: ["**/src/test"]         # every module's tests
migration:
  source: {language: java, standard: java8, build_system: maven}
  target: {language: java, standard: java21, rules: [use_diamond_operator, use_lambdas]}
```

A C# manifest (MSBuild):

```yaml
version: "1.0"
project:
  name: my-app
  source_dirs: ["."]
  test_dirs: ["tests"]
migration:
  source: {language: csharp, standard: csharp7.3, build_system: dotnet}
  target: {language: csharp, standard: csharp14, rules: [use_file_scoped_namespaces, enable_nullable]}
```

How to fill it in:

- **Standards:** C++ `cpp98`, `cpp03`, `cpp11`, `cpp14`, `cpp17`, `cpp20`, `cpp23`; Java `java8`, `java11`, `java17`, `java21`, `java25`. C# `csharp7.3`, `csharp8` … `csharp14`. Find the source standard in the build files (`CMAKE_CXX_STANDARD`, `set_property(... CXX_STANDARD ...)`, `-std=` flags; the POMs' `maven.compiler.source`/`release`, `java.version` or the compiler plugin's settings, where `1.8` is `java8`; the projects' `LangVersion`, else their `TargetFramework`'s default: `net6.0` is `csharp10`, `netstandard2.0` is `csharp7.3`). Confirm the target with the user.
- **Directories:** look at the repository layout. Leave out vendored code with `project.exclude: ["third_party/**"]`.
- **Rules:** `target.rules` lists the rules a full run applies, in order (`codecoremorph rules list`); the standard bump runs first automatically. A unit started with `unit start --rule` doesn't need its rule listed here.
- **A port to Rust:** `target: {language: rust, standard: rust2021, rules: [port_module]}` (editions `rust2015` to `rust2024`) ports every module of the project's library targets to a Cargo workspace under `rust/`, one crate per target, leaves first; each module's header stays as a C++ façade, so the project's C++ tests keep checking it. `rules: [port_c_abi_module]` ports only the C++ behind the project's C headers (functions declared `extern "C"`) to crates. Any C++98 or newer source works; no standard bump runs first. It needs the Rust toolchain: `codecoremorph toolchain install rust`, or the Rust image (`scripts/build-image rust`).
- **A whole port, to pure Rust:** `rules: [port_module, port_executable, port_tests, retire_cpp]` also turns each executable into a Rust program of the same name, translates the tests (paired in `rust/test-map.toml`) and retires the C++ and the CMake build, so a Cargo workspace is left. `migration.target.keep_source_api: true` keeps the public headers as a thin C++ library over the crates, with the C++ tests that use only them. A later run on a partly ported branch plans only what is left.
- **A port of C# to C++:** with `source: {language: csharp, standard: csharp7.3, build_system: dotnet}` (C# 7.3 or newer), `target: {language: cpp, standard: cpp17, rules: [port_csharp_type_to_cpp, port_csharp_program_to_cpp, port_csharp_tests_to_cpp, retire_csharp_to_cpp]}` (`cpp17`, `cpp20` or `cpp23`) ports a C# library type by type to C++ under `cpp/`, built by CMake, each type keeping its C# API as a façade over the C++, so the project's C# tests (NUnit, xUnit or MSTest) keep checking it. Then it ports the programs, translates the tests to GoogleTest (paired in `cpp/test-map.toml`) and retires the C#, which leaves a CMake project. `migration.target.keep_source_api: true` keeps the library as a .NET library over the C++, with its C# tests. It runs in Docker (`sandbox.mode: docker`), in the image for ports from C# (`scripts/build-image csharp-port`).
- **Build:** CMake for C++, Maven for Java, MSBuild for C#. `build.cmake.configure_args` passes extra configure flags, e.g. `["-DBUILD_TESTING=ON"]` when tests are off by default; `build.maven.configure_args` passes arguments to every Maven command, e.g. `["-Pci"]`. Java needs its toolchain: `codecoremorph toolchain install java`, or the Java image (`scripts/build-image java`). C# builds with the .NET SDK (`build_system: dotnet`); `build.dotnet.configure_args` passes arguments to every `dotnet` command, e.g. `["-p:Configuration=Release"]`, and it needs `codecoremorph toolchain install csharp` or the C# image (`scripts/build-image csharp`).
- **Sandbox:** `sandbox.mode: auto` uses Docker when it is available, otherwise native.
- **Retries:** `guardrails.max_retry_budget` (default 3) is the number of retries after a unit's first attempt; `guardrails.max_edit_retries` (default 2) is the number of extra tries for rejected edits within an attempt.

The `llm` section configures model providers for unattended runs; this workflow doesn't use it, so leave it out.

Check the result with `codecoremorph validate`. `codecoremorph schema` prints the full JSON Schema.
