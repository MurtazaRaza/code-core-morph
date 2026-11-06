# Changelog

All notable changes are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html): plugin API versions change only in a
major release.

## [0.1.0] - unreleased

The first release: C++ standard upgrades, from C++98/03, 11, 14, 17 or 20 to any newer standard
up to C++23, for CMake projects.

### Added

- `codecoremorph init` writes a commented starter manifest: it detects CMake, the current
  standard (from the build files, or guessed from the code), the source, test, vendored and
  public directories, the rules that apply and the configure options a run needs.
- `codecoremorph plan`: the base commit built and tested twice in its own worktree, the code
  indexed with libclang, the files grouped into ordered work units with time and cost
  estimates, in a deterministic `plan.json`.
- `codecoremorph run`, `status`, `approve`, `reject`, `resume`, `abort`, `report` and `clean`:
  whole migrations on the branch `codecoremorph/<run-id>`, with plan and unit gates, budgets,
  Ctrl-C parking, crash recovery and a final clean build.
- The C++ rule pack: the standard bump, `modernize_basics`, `replace_typedefs_with_using`,
  `convert_raw_pointers_to_smart_pointers` and `use_concepts_for_templates`, with clang-tidy
  codemods and guidance for the agent.
- Agents: Claude Code with the bundled skill (`codecoremorph skill install`, on your Claude plan),
  or the built-in loop with Anthropic or Ollama models, with routing, escalation and a usage
  ledger.
- Twelve safety invariants checked on every attempt, among them: edits stay in the worktree
  and the unit, no test removed or disabled, no assertion lost silently, public API and C ABI
  unchanged, no new suppressions, no new build-time commands.
- Characterization tests for code no test reaches, written and accepted before the plan.
- `codecoremorph dashboard`: a local web UI to follow runs, review plans and units side by
  side, explore the dependency graph and answer gates.
- Docker sandbox (non-root, no capabilities, resource limits, no network during builds and
  tests), or native toolchains installed without admin rights:
  `codecoremorph toolchain install cpp`, also from pre-downloaded archives with `--offline` and
  `codecoremorph toolchain download`.
- Versioned plugin APIs for adapters, rule packs and providers, and a plugin template
  (`templates/plugin/`).
- Documentation site, with generated configuration and CLI references.

- Java version upgrades for Maven projects (D-63, D-64), from Java 8, 11, 17 or 21 up to Java
  25: a Temurin JDK per standard (`codecoremorph toolchain install java`, or
  `scripts/build-image java`), builds and Surefire tests (JUnit 3, 4 and 5) on the build
  standard's JDK, a Spoon index, and the `java` rule pack: `bump_java_version` (OpenRewrite's
  migration recipe, then the agent) and `use_diamond_operator`, `use_lambdas`,
  `use_try_with_resources`, `use_var`, `use_switch_expressions` and `use_records`.
- An agent may submit no edits when what is left should stay as it is; the unit is verified as
  it is.
- C# language-version upgrades for MSBuild projects (D-65, D-66), from C# 7.3 up to C# 14: the
  .NET 10 SDK with the .NET 8 and 6 runtimes beside it (`codecoremorph toolchain install csharp`,
  or `scripts/build-image csharp`), `dotnet build` and `dotnet test` (NUnit, xUnit, MSTest) with
  each test project on its framework's runtime, a Roslyn index, and the `csharp` rule pack:
  `bump_csharp_version` (the language version and the target framework, then the agent) and
  `use_file_scoped_namespaces`, `use_pattern_matching`, `convert_switch_statements`,
  `use_using_declarations`, `use_target_typed_new`, `use_collection_expressions` (each through
  `dotnet format`'s fixers), `use_record_types` and `enable_nullable`.

### Security

- A security review against the threat model (D-52): path fuzzing, a prompt-injection fixture
  run with an agent that obeys it and with Claude Code, the sandbox checked from inside, and
  a redaction audit. See SECURITY.md.
