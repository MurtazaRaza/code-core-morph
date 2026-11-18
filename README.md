# CodeCoreMorph

CodeCoreMorph migrates codebases with LLM agents working inside a compile → test → fix loop.
Real toolchains decide what's correct: every change is built and tested in a sandbox, and
only verified changes are committed, to a branch of their own that you review and merge
yourself. Your working tree is never touched.

> **Status:** early development, not yet published to PyPI: install it from a source checkout
> (below). Each migration in the table has run end to end on the test projects in `fixtures/`.
> The C++ upgrade and every port have also run on a real open-source project, and the guides
> give the numbers.

## What it migrates

| # | Migration | From | To | Your project's build and tests | Builds run |
|---|---|---|---|---|---|
| 1 | [Upgrade C++](#1-upgrade-c-to-a-newer-standard) | C++98/03, 11, 14, 17, 20 | a newer standard, up to C++23 | CMake; tests CTest runs (GoogleTest) | Docker or native |
| 2 | [Upgrade Java](#2-upgrade-java) | Java 8, 11, 17, 21 | a newer LTS, up to Java 25 | Maven; JUnit 3, 4 or 5 | Docker or native |
| 3 | [Upgrade C#](#3-upgrade-c) | C# 7.3 to 13 | a newer version, up to C# 14 | MSBuild (SDK-style); NUnit, xUnit or MSTest | Docker or native |
| 4 | [Port C++ to Rust](#4-port-c-to-rust) | C++98 or newer | a Cargo workspace | CMake; CTest | Docker or native |
| 5 | [Port C to Rust](#5-port-c-to-rust) | C89 or newer | a Cargo workspace | Make, `./configure`, automake and libtool, or CMake | Docker |
| 6 | [Port Rust to C](#6-port-rust-to-c) | a crate, edition 2015 to 2024 | C99 or C11 with CMake | Cargo | Docker |
| 7 | [Port C# to C++](#7-port-c-to-c) | C# 7.3 or newer | C++17, 20 or 23 with CMake | MSBuild; NUnit, xUnit or MSTest | Docker |
| 8 | [Port C# to Rust](#8-port-c-to-rust) | C# 7.3 or newer | a Cargo workspace | MSBuild; NUnit, xUnit or MSTest | Docker |

## How a migration runs

1. **Plan.** CodeCoreMorph builds and tests your base commit in a separate git worktree (twice,
   to find flaky tests), indexes the code and splits the work into small, ordered **units**,
   with an estimate of time and cost. You approve the plan.
2. **Characterize.** In C and C++ projects, code that no test reaches first gets
   *characterization tests*, which record what it does now.
3. **Migrate.** For each unit, deterministic tools (clang-tidy, OpenRewrite, `dotnet format`,
   the port scaffolds) make the mechanical changes, and the agent does the rest. CodeCoreMorph
   applies the edits, builds, runs the tests and checks a set of invariants, and sends any
   failure back to the agent. A unit that passes is one commit; one that can't be made green is
   rolled back and reported.
4. **Verify.** The finished branch is built from scratch, every test runs, and a report lists
   each unit, what needs your review, and what the run cost.

Everything happens in a worktree on the branch `codecoremorph/<run-id>`. A port keeps the
project building and passing its tests at every commit: ported code sits behind a **façade**
with the old API, so the project's own tests check each step until they are translated too.

## Install

### What you need

- **git** 2.30 or newer.
- **Docker** (Docker Desktop on Windows and macOS), running. Ports 5 to 8 need it. For the
  others it's optional, since CodeCoreMorph can install a native toolchain instead.
- **An agent** to write the edits. Pick one:
  - **Claude Code** (recommended) with the CodeCoreMorph skill. It runs on your Claude plan,
    so you need no API key and pay nothing per token.
  - **The Anthropic API**, with an `ANTHROPIC_API_KEY`. You pay per token.
  - A local **Ollama** model. This is experimental: no local model has been shown to complete
    an agent unit yet.
- No admin rights. Nothing is installed system-wide.

### Steps

1. **Clone and bootstrap.**

   Windows (PowerShell or cmd.exe):

   ```bat
   git clone https://github.com/MurtazaRaza/code-core-morph.git C:\src\code-core-morph
   cd C:\src\code-core-morph
   scripts\bootstrap-dev.bat
   ```

   Linux, macOS or Git Bash:

   ```sh
   git clone https://github.com/MurtazaRaza/code-core-morph.git ~/src/code-core-morph
   cd ~/src/code-core-morph
   ./scripts/bootstrap-dev.sh
   ```

   The bootstrap uses `uv` from your PATH if it's recent enough, or downloads a pinned,
   checksum-verified copy. It then installs Python 3.12, the dependencies (into `.venv`) and
   Node.js for the dashboard. If it says it didn't add `uv` to PATH, add the directory it printed
   to PATH yourself.

2. **Make `codecoremorph` callable from your projects.** You run its commands from your project's
   root, where the manifest lives. The launcher is the checkout's
   `.venv\Scripts\codecoremorph.exe` (`.venv/bin/codecoremorph` on Linux and macOS). Give it a
   name in your shell:

   ```powershell
   # PowerShell (add the line to $PROFILE to keep it)
   Set-Alias codecoremorph C:\src\code-core-morph\.venv\Scripts\codecoremorph.exe
   ```

   ```sh
   # bash or zsh (add the line to ~/.bashrc or ~/.zshrc to keep it)
   alias codecoremorph="$HOME/src/code-core-morph/.venv/bin/codecoremorph"
   ```

   In Git Bash, point the alias at `.venv/Scripts/codecoremorph.exe`. If you'd rather not use an
   alias, `uv run --project C:\src\code-core-morph codecoremorph <command>` works from any
   directory. The rest of this README writes `codecoremorph`.

3. **Check the machine.**

   ```sh
   codecoremorph --version
   codecoremorph doctor
   ```

   `doctor` checks Python, git and Docker, and says what to install for anything missing.

4. **Get the toolchain for your migration.** Each migration's section below gives the exact
   command. Either build its Docker image once from the checkout, or install it natively:

   | Migration | Docker image (from the checkout) | Or native |
   |---|---|---|
   | 1. Upgrade C++ | `scripts\build-image.bat cpp` | `codecoremorph toolchain install cpp` |
   | 2. Upgrade Java | `scripts\build-image.bat java` | `codecoremorph toolchain install java` |
   | 3. Upgrade C# | `scripts\build-image.bat csharp` | `codecoremorph toolchain install csharp` |
   | 4. C++ → Rust | `scripts\build-image.bat rust` | `codecoremorph toolchain install rust` |
   | 5. C → Rust, 6. Rust → C | `scripts\build-image.bat rust` | none (Docker only) |
   | 7. C# → C++, 8. C# → Rust | `scripts\build-image.bat csharp-port` | none (Docker only) |

   On Linux and macOS, use `./scripts/build-image.sh <name>`. The images are tagged
   `codecoremorph-<name>:local`, and a run finds them by that tag. Native toolchains are pinned
   and checksum-verified, and go under `CODECOREMORPH_HOME`, which defaults to
   `%LOCALAPPDATA%\codecoremorph` on Windows, `~/Library/Caches/codecoremorph` on macOS and
   `~/.cache/codecoremorph` on Linux. To install on a machine without internet access, see
   [Installing dependencies](docs/dependencies.md).

## Run a migration

Every migration takes the same steps. The sections that follow give what differs for each one:
the toolchain, the manifest, and what to ask for.

1. **Commit your work.** A run starts from the commit `migration.base` names (`HEAD` by
   default), so uncommitted changes aren't part of it. The project must build and pass its tests
   at that commit.

2. **Go to the project's root**, the top directory of its git repository:

   ```sh
   cd path/to/my-project
   ```

3. **Write the manifest**, `migration_config.yaml`:

   ```sh
   codecoremorph init
   ```

   `init` reads the files git tracks. It detects the language, the build system, the current
   standard, the source, test and vendored directories and the rules that apply, and writes
   them with a comment on every setting. Read the file through, above all a standard it marks
   as a guess. `init` writes an upgrade. For a port, change `migration.target` as the port's
   section shows. `init` doesn't read Rust crates yet; for a Rust → C port, write the manifest
   by hand ([section 6](#6-port-rust-to-c)).

4. **Check it:**

   ```sh
   codecoremorph validate
   codecoremorph doctor migration_config.yaml
   ```

5. **Preview the plan.** This step is optional and changes no code:

   ```sh
   codecoremorph plan
   ```

   The plan lists the ordered units with their estimates. If the base commit doesn't build or
   has no passing test, it stops and says why.

6. **Run it, with Claude Code or with a model API.**

   **With Claude Code** (recommended). Install the skill into the project:

   ```sh
   codecoremorph skill install          # into ./.claude/skills; --user: for every project
   ```

   Start a new Claude Code session in the project (`claude` in a terminal, or the IDE
   extension) and ask for the migration. Each section below gives the request, for example
   *"Use codecoremorph to move this project to C++20. Do all the units."* Claude starts
   `codecoremorph run --host-agent`, shows you the plan and asks you to approve it (or approves
   at once if you asked for everything). Then it writes each unit's edits, and CodeCoreMorph
   verifies and commits them. If Claude Code stops in the middle (a usage limit, a closed
   session), ask it to continue: the run picks up where it stopped.

   **With the Anthropic API.** Set the key, then run:

   ```powershell
   $env:ANTHROPIC_API_KEY = "sk-ant-..."   # PowerShell; sh: export ANTHROPIC_API_KEY=...
   codecoremorph run
   ```

   The model is the manifest's `llm.roles.editor` (`claude-opus-5-5` as `init` writes it), and
   `guardrails.max_run_cost_usd` caps the spend (50 by default). The run shows the plan and asks
   you to approve it; `--yes` skips that. Ctrl-C parks the run, and `codecoremorph resume`
   continues it.

7. **Follow the run**, from another terminal or the dashboard:

   ```sh
   codecoremorph status                  # progress, the gate it waits at, usage
   codecoremorph approve                 # approve the plan (--exclude U-0003,U-0007 skips units)
   codecoremorph reject                  # reject the plan, which ends the run
   codecoremorph approve --unit U-0004   # keep a unit waiting at its gate (reject --unit: drop it)
   codecoremorph resume                  # continue a parked or crashed run
   codecoremorph abort                   # end the run; the units committed so far stay
   codecoremorph dashboard               # all of the above in a browser
   ```

   A unit stops at its own gate only when the manifest's `approvals.unit` asks for it
   (`api_changes`, `test_changes` or `all`).

8. **Review the branch and merge it yourself.** `codecoremorph status` shows the run's ID, and
   the report names its branch.

   ```sh
   codecoremorph report                                 # every unit, what needs review, the cost
   git log --oneline HEAD..codecoremorph/<run-id>       # one commit per unit
   git diff HEAD...codecoremorph/<run-id>               # the whole change
   git merge codecoremorph/<run-id>                     # when you're satisfied
   codecoremorph clean                                  # remove the run's worktree and caches
   codecoremorph clean --branches                       # also delete its branch, after merging
   ```

   The report names the tests that already failed before the migration and the files your build
   never compiles (another platform's sources), because neither was checked. A run writes its
   records under `.codecoremorph/` in the repository, which it adds to `.git/info/exclude`, so it
   never shows up in `git status`.

## The migrations, one by one

### 1. Upgrade C++ to a newer standard

From C++98/03, 11, 14, 17 or 20 to any newer standard up to C++23. The standards are `cpp98`,
`cpp03`, `cpp11`, `cpp14`, `cpp17`, `cpp20` and `cpp23`.

**Your project:** CMake, with tests CTest runs (GoogleTest works out of the box).

1. Get the toolchain: `codecoremorph toolchain install cpp`, or `scripts\build-image.bat cpp`.
2. In the project, write the manifest with the standard you want:

   ```sh
   codecoremorph init --target cpp20
   ```

3. Check `migration.source.standard`. When your CMake files set no standard, `init` guesses
   one and says so at the top of the file. Then keep the rules you want in
   `migration.target.rules`, in the order to apply them:

   | Rule | What it does |
   |---|---|
   | `modernize_basics` | clang-tidy's mechanical fixes: `nullptr`, `override`, `= default`, `= delete`, range-based `for`, C++ headers instead of C headers, and more with newer targets |
   | `replace_typedefs_with_using` | `typedef` becomes `using X = Y` |
   | `use_concepts_for_templates` | SFINAE (`std::enable_if`) becomes `requires` clauses and named concepts (C++20 or newer) |
   | `convert_raw_pointers_to_smart_pointers` | owning raw pointers become `std::unique_ptr` or `std::shared_ptr`, call sites included; this changes interfaces |

   The standard bump (`bump_standard`) always runs first: it raises the standard in the CMake
   files and fixes what no longer compiles.
4. Do steps 4 to 8 of [Run a migration](#run-a-migration). Ask Claude Code: *"Use codecoremorph
   to move this project to C++20."*

Code no test reaches gets GoogleTest characterization tests before the first unit. To turn that
off, set `parity.characterization_tests.enabled: false`. To delete the tests at the end, set
`keep: false`. CMake options a build needs go in `build.cmake.configure_args`.

### 2. Upgrade Java

From Java 8, 11, 17 or 21 to a newer LTS release up to Java 25. The standards are `java8`,
`java11`, `java17`, `java21` and `java25`.

**Your project:** Maven, with a top-level `pom.xml`, and JUnit 3, 4 or 5 tests that pass. Gradle
isn't supported yet.

1. Get the toolchain: `codecoremorph toolchain install java` (a Temurin JDK for each standard,
   Maven and OpenRewrite, about 1 GB), or `scripts\build-image.bat java`. On Apple-silicon Macs,
   Java 8 projects need Docker.
2. In the project:

   ```sh
   codecoremorph init --target java21
   ```

3. Keep the rules you want: `use_diamond_operator`, `use_lambdas`, `use_switch_expressions`,
   `use_try_with_resources`, `use_var`, `use_records`. The upgrade itself (`bump_java_version`)
   runs first. OpenRewrite's migration recipe (`UpgradeToJava21`) runs, and the POMs' Java
   level is raised. Then the agent fixes what breaks on the new JDK, such as removed Java EE
   modules and JDK internals. Arguments for every Maven command go in
   `build: {maven: {configure_args: ["-Pci"]}}`.
4. Do steps 4 to 8 of [Run a migration](#run-a-migration). Ask Claude Code: *"Use codecoremorph
   to upgrade this project from Java 8 to Java 21."*

Characterization tests aren't written for Java yet. The run skips that step, and the report
says so. Details: [docs/upgrading-java.md](docs/upgrading-java.md).

### 3. Upgrade C#

From C# 7.3 up to C# 14. The standards are `csharp7.3`, `csharp8`, `csharp9`, `csharp10`,
`csharp11`, `csharp12`, `csharp13` and `csharp14`. The projects move to the .NET release of the
target version: .NET 8 for C# 12, .NET 10 for C# 14. `netstandard` libraries keep their
framework.

**Your project:** SDK-style `.csproj` projects, with a solution (`.sln` or `.slnx`) or a single
project at the top, and NUnit, xUnit or MSTest tests that pass.

1. Get the toolchain: `codecoremorph toolchain install csharp` (the .NET 10 SDK and the .NET 8
   and .NET 6 runtimes, about 1 GB), or `scripts\build-image.bat csharp`. On Windows, keep
   `CODECOREMORPH_HOME` short, so that MSBuild's and NuGet's deepest paths stay under 260
   characters. Set it before you install, and in every session that runs CodeCoreMorph:
   `$env:CODECOREMORPH_HOME = "C:\ccm"`.
2. In the project:

   ```sh
   codecoremorph init --target csharp14
   ```

3. Keep the rules you want: `use_file_scoped_namespaces`, `use_pattern_matching`,
   `convert_switch_statements`, `use_using_declarations`, `use_target_typed_new`,
   `use_collection_expressions`, `use_record_types`, `enable_nullable`. The upgrade itself
   (`bump_csharp_version`) runs first and moves `LangVersion` and the target frameworks. Then the
   agent fixes what breaks, `BinaryFormatter` being the usual case. Arguments for every `dotnet`
   command go in `build: {dotnet: {configure_args: ["-p:Configuration=Release"]}}`.
4. Do steps 4 to 8 of [Run a migration](#run-a-migration). Ask Claude Code: *"Use codecoremorph
   to upgrade this solution to C# 14."*

Characterization tests aren't written for C# yet. Details:
[docs/upgrading-csharp.md](docs/upgrading-csharp.md).

### 4. Port C++ to Rust

The port goes module by module, leaves first. Each module's header stays as a C++ façade over
the Rust, so the rest of the C++ and the project's own C++ tests don't change. Then the programs
become Rust programs and the tests become Rust tests, each paired with its original in
`rust/test-map.toml`. Last, the C++ and its CMake build are retired, leaving a Cargo workspace.

**Your project:** CMake, any C++98 or newer, with tests that build and pass.

1. Get the toolchain: `codecoremorph toolchain install rust` (it installs the C++ toolchain too),
   or `scripts\build-image.bat rust`.
2. In the project, run `codecoremorph init`. Then replace the `target` that `init` wrote under
   `migration:` with a Rust one, keeping its `source` line:

   ```yaml
   migration:
     base: HEAD
     source: {language: cpp, standard: cpp11, build_system: cmake}   # as init wrote it
     target:
       language: rust
       standard: rust2021                                            # rust2015 to rust2024
       rules: [port_module, port_executable, port_tests, retire_cpp]
   ```

3. Choose how far to go:
   - The rules run in order, and you can stop after any of them. `rules: [port_module]` ports
     the libraries and leaves the programs, the tests and the C++ façades in place. A later run
     on the branch plans only what is left.
   - To keep a C++ API for outside users, add `keep_source_api: true` under `target`. The public
     headers then stay, as a thin C++ library over the Rust crates.
   - To port only the C++ behind a C header (functions declared `extern "C"`) to a crate that
     exports the same C interface, use `rules: [port_c_abi_module]`.
4. Do steps 4 to 8 of [Run a migration](#run-a-migration). Ask Claude Code: *"Use codecoremorph
   to port this project to Rust."*

**You get** a `rust/` workspace, one crate per CMake library target. `rust/symbol-map.toml`
records where each C++ name went, and the report lists the behavior the port kept differently
and any `unsafe` code. Details: [docs/porting-to-rust.md](docs/porting-to-rust.md).

### 5. Port C to Rust

The port goes one source file at a time, in the order of the calls between files. A ported
file's functions are exported from Rust under their C names, the headers stay exactly as they
are, and the build folds the Rust crate into the library's archive. So the C that still calls
those functions, the C programs and the C tests link against the Rust unchanged. Then come the
programs, the tests and the retirement of the C, leaving a Cargo workspace.

**Your project:** C89 or newer, built with a plain Makefile, `./configure` and Make, automake and
libtool, or CMake. `make` builds a static library, and `make check` or `make test` passes.
Tests that print TAP (`ok 1 - ...`) count one test per point. Otherwise the test target's exit
status counts as one test.

1. Start Docker and build the image once: `scripts\build-image.bat rust`.
2. In the project, run `codecoremorph init`. It writes a C upgrade with no rules. Replace its
   `target` with a Rust one, and set the sandbox to Docker:

   ```yaml
   migration:
     base: HEAD
     source: {language: c, standard: c89, build_system: make}      # as init wrote it
     target:
       language: rust
       standard: rust2021
       rules: [port_c_module, port_c_executable, port_c_tests, retire_c]

   sandbox:
     mode: docker
   ```

3. Adjust the build if you need to, under `build.make`:
   - `test_targets: [runtests]` when the test target is neither `check` nor `test`;
   - `build_targets: [libfoo.a, foo]` when the default target does more than build (bzip2's
     also runs its tests);
   - `configure_args` for `./configure`;
   - `junit_reports: ["test-results/*.xml"]` when the tests write JUnit XML.

   Add `keep_source_api: true` under `target` to keep the public headers and the Make build with
   Rust inside. `rules: [port_c_module]` ports the library only.
4. Do steps 4 to 8 of [Run a migration](#run-a-migration). Ask Claude Code: *"Use codecoremorph
   to port this C library to Rust."*

Old C has few tests. Before the first unit, the code no test covers gets characterization tests:
small TAP programs in `tests/codecoremorph_characterization/`, built by a marked block in your
Makefile. A function that takes `...` or a `va_list` stays C until the retirement, because
stable Rust can't define one. Details: [docs/porting-c-to-rust.md](docs/porting-c-to-rust.md).

### 6. Port Rust to C

The port goes module by module, leaves first. Each ported module keeps its Rust API as a façade
over the C, so the rest of the crate and its Rust tests check every step. Then come the programs,
the tests (as TAP programs that CTest runs) and the retirement of the Rust, leaving a CMake
project in `c/`. The borrow checker goes, and three checks replace it on every unit: the C
compiles with `-Wall -Wextra` and no warning, clang-tidy's bug-finding checks find nothing, and
every test passes under AddressSanitizer and UndefinedBehaviorSanitizer.

**Your project:** one library crate, at the repository's root or as a member of a workspace below
it, whose `cargo build` and `cargo test` pass, with its `Cargo.lock` committed. Procedural-macro
crates and `async` code are refused.

1. Start Docker and build the image once: `scripts\build-image.bat rust`.
2. `init` doesn't read Rust crates yet, so write `migration_config.yaml` at the repository's root
   by hand:

   ```yaml
   version: "1.0"
   project:
     name: my-crate
     source_dirs: [src]
     test_dirs: [tests]
   migration:
     source: {language: rust, standard: rust2018}   # the crate's edition: rust2015 to rust2024
     target:
       language: c
       standard: c99                                # or c11
       rules: [port_rust_module, port_rust_program, port_rust_tests, retire_rust]
   sandbox: {mode: docker}
   ```

   For a plain `codecoremorph run` with the API, also add the model:

   ```yaml
   llm:
     roles:
       editor: {provider: anthropic, model: claude-opus-5-5, effort: high}
   ```

3. Do steps 4 to 8 of [Run a migration](#run-a-migration). Ask Claude Code: *"Use codecoremorph
   to port this crate to C99."*

**You get** the C under `c/`, with the CMake build at the repository's root. `c/symbol-map.toml`
records where each Rust item went, and `c/test-map.toml` pairs each Rust test with its C test.
Details: [docs/porting-rust-to-c.md](docs/porting-rust-to-c.md).

### 7. Port C# to C++

The port goes type by type, leaves first. Each ported type keeps its C# API as a façade that
calls the C++ through P/Invoke, so the rest of the library and your C# tests decide, unit by
unit, whether the port is right. Then the programs become C++ programs and the tests become
GoogleTest tests. Last, the C# is retired, leaving a CMake project. Or, with `keep_source_api`,
the library stays a .NET library whose implementation is C++.

**Your project:** a solution or project whose `dotnet build` and `dotnet test` pass. The types to
port are in one library project; its console programs and test projects come along.

1. Start Docker and build the image once: `scripts\build-image.bat csharp-port`.
2. In the project, run `codecoremorph init`. Replace its `target` with a C++ one, and set the
   sandbox to Docker:

   ```yaml
   migration:
     base: HEAD
     source: {language: csharp, standard: csharp7.3, build_system: dotnet}   # as init wrote it
     target:
       language: cpp
       standard: cpp17                                                      # cpp17, cpp20 or cpp23
       rules: [port_csharp_type_to_cpp, port_csharp_program_to_cpp, port_csharp_tests_to_cpp,
               retire_csharp_to_cpp]

   sandbox:
     mode: docker
   ```

   Add `keep_source_api: true` under `target` to keep the .NET library over the C++.
3. Run `codecoremorph plan` and read it. It lists what the library depends on outside its own
   code (native libraries its P/Invokes call, the operating system's own libraries, .NET
   packages) and what the port does about each. It also names what it refuses to port, with the
   reason, such as `async` code, reflection and serializers, a class with non-private instance
   fields, a type that uses a .NET package's API, and a type that uses a refused one. The full
   list is in the guide's Limits section.
4. If the plan says the image lacks a native library's header, build an image with the library's
   development files. Put this `Dockerfile` in an empty directory outside the project
   (libsodium as the example):

   ```dockerfile
   FROM codecoremorph-csharp-port:local
   USER root
   RUN apt-get update && apt-get install -y --no-install-recommends libsodium-dev \
       && rm -rf /var/lib/apt/lists/*
   USER 1000:1000
   ```

   ```sh
   docker build -t my-port-image .
   docker image inspect --format '{{.Id}}' my-port-image    # prints sha256:...
   ```

   Then pin that ID in the manifest: `sandbox: {mode: docker, image: "sha256:..."}`.
5. Do steps 4 to 8 of [Run a migration](#run-a-migration). Ask Claude Code: *"Use codecoremorph
   to port this library to C++17."*

**You get** the C++ under `cpp/`, with a top-level CMake build. Every test runs under the
sanitizers. `cpp/symbol-map.toml` records where each C# member went, and `cpp/test-map.toml`
pairs each C# test case with its GoogleTest tests. Details:
[docs/porting-csharp-to-cpp.md](docs/porting-csharp-to-cpp.md).

### 8. Port C# to Rust

The same port as [C# to C++](#7-port-c-to-c), with Rust as the target. The C# side, its façades
and the plan are identical. The Rust uses no unsafe code outside each crate's bridge (and its
`native` module, for a native library), and clippy stays quiet.

**Your project:** as for C# to C++.

1. Start Docker and build the image once: `scripts\build-image.bat csharp-port`.
2. In the project, run `codecoremorph init`. Replace its `target` with a Rust one, and set the
   sandbox to Docker:

   ```yaml
   migration:
     base: HEAD
     source: {language: csharp, standard: csharp7.3, build_system: dotnet}   # as init wrote it
     target:
       language: rust
       standard: rust2021                                                   # rust2018 to rust2024
       rules: [port_csharp_type_to_rust, port_csharp_program_to_rust, port_csharp_tests_to_rust,
               retire_csharp_to_rust]

   sandbox:
     mode: docker
   ```

   Add `keep_source_api: true` under `target` to keep the .NET library over the Rust.
3. Read `codecoremorph plan` and, for a native library, build an image as in steps 3 and 4 of
   [C# to C++](#7-port-c-to-c).
4. Do steps 4 to 8 of [Run a migration](#run-a-migration). Ask Claude Code: *"Use codecoremorph
   to port this library to Rust."*

**You get** a `rust/` Cargo workspace: `codecoremorph_runtime` (.NET's `decimal` and exceptions)
and your library's crate, `rust/<name>_rs`. The report counts the shared cells (`Mutex`,
`RwLock`, `RefCell`, `Cell`) for you to review. Details:
[docs/porting-csharp-to-rust.md](docs/porting-csharp-to-rust.md).

## Migrate a few files, one unit at a time

For a few rules on a few files, or to take a plan's units one by one, use single units instead
of a whole run.

**With Claude Code**, name the rule and the files: *"Use codecoremorph to replace the typedefs in
src/geo.h with using declarations."*

**By hand, or with another coding agent:**

1. Pick the unit. Either take one from the plan:

   ```sh
   codecoremorph plan
   codecoremorph unit list                     # the units in order, with their status
   codecoremorph unit start --plan-unit U-0002
   ```

   or name the rules and the files yourself:

   ```sh
   codecoremorph unit start --rule replace_typedefs_with_using src/geo.h include/geo.h
   ```

   A project rule such as `bump_standard` needs no files. `codecoremorph rules list` shows every
   rule.
2. `unit start` builds and tests the base, runs the codemods and checks what is left. If the
   codemods finished the unit, it is already committed. Otherwise the output names a context
   document (`context.md`) and an edits file under `.codecoremorph/runs/<run-id>/`.
3. Read the context document. To look at the code as the unit sees it, use
   `codecoremorph unit read PATH`, `unit grep REGEX`, `unit ls DIR`, `unit symbol NAME`,
   `unit refs ID` and `unit signature ID`. The project directory still shows the base commit.
4. Write the edits file, in the JSON format the context document shows (`edits`, `summary`,
   `review_notes`), and submit it:

   ```sh
   codecoremorph unit submit
   ```

   It applies the edits, builds, tests and checks them. A green unit is committed to its branch.
   On a failure it prints the feedback; write the additional edits and submit again.
   `codecoremorph unit abort` gives the unit up and leaves nothing behind.
5. Start the next unit on top of the last one, so the branch holds both:

   ```sh
   codecoremorph unit start --plan-unit U-0003 --base codecoremorph/<previous-run-id>
   ```

   `codecoremorph unit list` names the last successful branch.

## The dashboard

```sh
codecoremorph dashboard
```

This opens a local page in your browser. It listens on 127.0.0.1 only, and its link holds a
token that is new at every launch. There you can follow runs live, review the plan and approve
it (leaving units out if you like) or reject it, read each unit's diff beside its compiler
messages, tests, safety checks and the agent's transcript, keep or roll back a unit waiting at
its gate, and read or download the report. A gate can be answered from the dashboard or the
terminal; whichever answer comes first wins.

In a source checkout, build the web UI once: `cd dashboard-ui; npm ci; npm run build`, with the
Node.js that `bootstrap-dev` installed.

## Commands

| Command | What it does |
|---|---|
| `init [--target STD] [--language L]` | Write `migration_config.yaml` from what the repository holds |
| `validate [CONFIG]` | Check a manifest: schema, standards, rules |
| `doctor [CONFIG]` | Check Python, git, Docker, toolchains and the manifest's models |
| `plan [CONFIG]` | Baseline, index and units, with no code changed |
| `run [CONFIG] [--host-agent] [--yes] [--sandbox docker\|native]` | Migrate the whole project |
| `status`, `approve`, `reject`, `resume`, `abort` | Follow and steer a run |
| `report [--format md\|json]` | The latest run's report |
| `clean [--branches] [--all]` | Remove finished runs' worktrees and caches (and branches) |
| `unit start\|submit\|abort\|list\|status\|context` | Single units |
| `unit read\|ls\|grep\|symbol\|refs\|signature` | Read the unit's worktree and index |
| `skill install [--user\|--project PATH]` | Install the Claude Code skill |
| `toolchain install cpp\|rust\|java\|csharp\|all` | Install a native toolchain (`list`, `path`, `download` too) |
| `rules list`, `adapters list`, `providers list` | What is installed |
| `dashboard` | The web UI |

Every command takes `--help`. Commands about a run take `-c CONFIG` when the manifest isn't
`./migration_config.yaml`.

## Troubleshooting

| Problem | What to do |
|---|---|
| The plan stops: the baseline doesn't build, or has no passing tests (exit code 3) | Fix the project at the base commit first. CodeCoreMorph only migrates a project that builds and passes its tests. |
| Docker isn't running | Start Docker Desktop. For migrations 1 to 4 you can run natively instead: `--sandbox native`, or `sandbox.mode: native`. |
| Another run is active | `codecoremorph status`. Finish a waiting unit with `unit submit` or `unit abort`, or end the run with `codecoremorph abort`. |
| A unit is `skipped` or `too_large` | The planner left it out on purpose; the plan and the report give the reason. |
| MSBuild or NuGet fails on a long path (Windows) | Set a short `CODECOREMORPH_HOME`, such as `C:\ccm`, and install the toolchain again. |
| Builds in Docker are slow on Windows | Docker Desktop's mounts from Windows folders are slow; keep large repositories inside WSL2, or use native mode where it's supported. |

More in [docs/troubleshooting.md](docs/troubleshooting.md).

## Developer setup

To work on CodeCoreMorph itself, after `bootstrap-dev`:

```sh
uv sync                              # install, including dev dependencies
uv run pytest                        # default: no Docker, toolchain, live-LLM or browser tests
uv run pytest -m toolchain           # with native toolchains under CODECOREMORPH_HOME
uv run pytest -m docker              # with the local images
uv run ruff check . ; uv run ruff format .
uv run pyright
uv run mkdocs serve                  # the documentation site, locally
```

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Documentation

The documentation site is built from `docs/` (`uv run mkdocs serve`). It covers getting
started, concepts, the guides above, Claude Code, the dashboard, dependencies, troubleshooting,
the configuration and CLI references, and how to write rules, adapters and providers
(`templates/plugin/` is a plugin to copy).

- [docs/SPEC.md](docs/SPEC.md): the product and technical specification
- [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md): the phased implementation plan
- [SECURITY.md](SECURITY.md): the security model, and how to report a vulnerability
- [CONTRIBUTING.md](CONTRIBUTING.md) and [CHANGELOG.md](CHANGELOG.md)

## License

Apache-2.0. See [LICENSE](LICENSE).
