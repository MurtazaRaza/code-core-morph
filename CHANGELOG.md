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

### Security

- A security review against the threat model (D-52): path fuzzing, a prompt-injection fixture
  run with an agent that obeys it and with Claude Code, the sandbox checked from inside, and
  a redaction audit. See SECURITY.md.
