# CodeCoreMorph: Implementation Plan

This plan turns [SPEC.md](SPEC.md) into an ordered set of phases. Each phase has a goal, its prerequisites, a task checklist, tests, and exit criteria. Requirement IDs (`FR-`, `NFR-`, `D-`) point back to the spec.

**Order of work (D-21, confirmed by D-47).** Language version upgrades come first: C++ in the MVP, then Java, then C#. Cross-language migrations follow: Java → C#, then C++ → Rust, from one module behind a C ABI (Phase 12) to a whole project ported (Phases 13 and 14).

## How to use this plan

- Phases run in order. Tasks inside a phase can be reordered.
- Tick a checkbox when its task has landed **with tests**.
- A phase is done when every exit criterion is met and the checks (ruff, pyright, pytest, shellcheck) pass. CI comes later (Backlog, D-28). Then update "Current status" in [CLAUDE.md](https://github.com/MurtazaRaza/code-core-morph/blob/master/CLAUDE.md).
- If a task shows the spec is wrong or incomplete, update SPEC.md (and add a `D-xx` row) in the same change.
- Items in **Backlog** at the end are deliberately unscheduled.

## Milestones

| Milestone | After phase | Outcome |
|---|---|---|
| **M1: MVP** | 5 | `codecoremorph run` migrates C++11 → C++20 end to end from the CLI, the branch stays green, a report is produced, and the benchmark is measured |
| **M2: v0.1.0 public release** | 8 | Characterization tests, dashboard, docs, packaging, security review |
| **M3: Java version upgrades** | 9 | Java adapter plus the Java 8 → 21 upgrade rule pack |
| **M4: C# version upgrades** | 10 | C# adapter plus the C# modernization rule pack |
| **M5: Cross-language** | 11 | Java → C# port with translated tests |
| **M6: Rust** | 12 | A C++ module behind a C ABI replaced by Rust |
| **M7: C++ → Rust, every module** | 13 | Every module of an old C++ project is Rust, behind its unchanged C++ interface; the original tests passed at every commit |
| **M8: C++ → Rust, finished** | 14 | Pure Rust: tests translated and mapped one to one, C++ retired, a real project ported and measured |

## Phase overview

| # | Phase | Main deliverables | Spec |
|---|---|---|---|
| 0 | Foundations | Repo, tooling, config schema, core models, store, CLI skeleton, dependency scripts and `bootstrap-dev` | FR-1.1, FR-1.5–1.10, §7.15, §8, §9 |
| 1 | Workspace safety and execution | git worktrees and rollback, PatchEngine, Docker and native executors | FR-3.2, FR-3.3, FR-6, §7.4, §7.8, §7.9 |
| 2 | C++ adapter and rules | C++ image, `install-toolchain cpp`, build, test, index, codemods, rule model, C++ rule pack v1 for C++98/03 → C++23, fixtures, benchmark pick | FR-1.2, FR-1.3, FR-1.11, FR-1.13, FR-2, FR-9, §7.1–7.3, §7.15, §11.1 |
| 3 | Graph, baseline and planner | Dependency DAG, baseline capture, work units, estimates, `codecoremorph plan` | FR-5, FR-7.1, FR-2.2, §7.5.1 |
| 4 | LLM providers and single-unit loop | Anthropic + Ollama providers, replay, ledger, agent loop, feedback, `codecoremorph debug unit` | FR-3, FR-4, FR-8, §7.6, §7.7 |
| 5 | Run orchestration (**M1**) | LangGraph run graph, gates, invariants, commit and rollback, resume, reports, full CLI | FR-4.4, FR-10, FR-11, FR-13, FR-14, §7.5, §7.10 |
| 6 | Characterization tests | Untested-code detection, test generation and acceptance, assertion guard | FR-7.2–7.6, §7.11 |
| 7 | Web dashboard | FastAPI + SSE API, React SPA, approvals in the UI | FR-12, §7.13 |
| 8 | Hardening and v0.1.0 (**M2**) | `codecoremorph init`, docs site, security review, performance, plugin API versioning, release | FR-1.4, FR-15.3, NFR-1, NFR-5, NFR-10 |
| 9 | Java adapter and upgrades (**M3**) | Java image, Spoon indexer, Maven, OpenRewrite, Java 8 → 21 rule pack | §11.2 |
| 10 | C# adapter and upgrades (**M4**) | .NET image, Roslyn indexer, dotnet build and test, C# modernization pack | §11.3 |
| 11 | Java → C# (**M5**) | Migration pairs, scaffolding, symbol map, skeleton-first strategy, test translation, multi-agent roles | §11.4 |
| 12 | Rust / C++ FFI → Rust (**M6**) | Rust adapter, C-ABI replacement strategy, Corrosion integration | §11.5 |
| 13 | C++ → Rust, module by module (**M7**) | Bridge, Cargo workspace scaffold, leaves-first port units behind C++ façades, `cpp_to_rust` mapping pack, `unsafe` and clippy checks | §11.6 |
| 14 | C++ → Rust, finished (**M8**) | Test translation and test-ID map, retirement of façades and the C++ build, report, real-project benchmark | §11.6 |

---

## Phase 0: Foundations

**Goal:** an empty but production-shaped project. Config can be validated, the core data contracts exist, and a contributor can set up a development environment with one script.

**Prerequisites:** none.

### Tasks
- [x] `git init`
- [x] Add `.gitignore` (Python, Node, `.codecoremorph/`), `.gitattributes` (`* text=auto`; keep fixture files byte-exact with `-text` where needed; `*.bat text eol=crlf`; `*.sh text eol=lf`), README stub, LICENSE (Apache-2.0)
- [x] `pyproject.toml` with uv; `src/codecoremorph/` layout per SPEC §4.3; dependency groups `dev` and `docs`; console script `codecoremorph`
- [x] Tooling: ruff (lint + format, line length 100), pyright (strict for the packages listed in NFR-9.1), pytest with markers `docker`, `toolchain`, `live_llm`, `slow`; pre-commit hooks
- [x] `codecoremorph.core`: domain models from SPEC §9 (config-independent ones first: `Diagnostic`, `BuildResult`, `TestCaseResult`, `TestReport`, `Edit`, `EditBatch`, `WorkUnit`, `Plan`, `Attempt`, `Event`, status enums), the error hierarchy (`codecoremorph.core.errors`), and run-ID generation
- [x] `codecoremorph.config`: the full manifest schema (SPEC §8) in pydantic, including the PRD aliases (`root_path`, `docker_sandbox`) and the `rollback_on_failure: false` rejection; a YAML loader that reports every error with its location; JSON Schema export
- [x] `codecoremorph.store`: SQLite schema v1 (`runs`, `units`, `attempts`, `llm_calls`, `approvals`, `events`, `schema_version`), WAL mode, a simple versioned-migration runner, the artifact-directory helper and the event writer
- [x] `codecoremorph.logging`: stdlib logging with a Rich console handler, a JSON file handler, and a secret-redaction filter (NFR-5.3)
- [x] CLI skeleton (Typer): `codecoremorph --version`, `codecoremorph validate`, `codecoremorph schema`, `codecoremorph doctor` (checks Python, git ≥ 2.30, Docker reachability; more checks join later)
- [x] `examples/cpp11-to-cpp20.yaml`, matching SPEC §8

**Dependency scripts (SPEC §7.15)**
- [x] `codecoremorph.core.paths`: `CODECOREMORPH_HOME` resolution per platform, with the environment override
- [x] `scripts/deps/manifest.txt` with the `dev` group (pinned uv for every supported platform)
- [x] `scripts/lib/common.bat` and `scripts/lib/common.sh`:
  - platform detection and manifest parsing
  - download with retries, SHA-256 check (`certutil` / `sha256sum` / `shasum`)
  - extract to a temp directory, then move into place; `.installed` markers
  - flags `--prefix`, `--force`, `--dry-run`; the §7.15 exit codes
- [x] `scripts/bootstrap-dev.bat` and `scripts/bootstrap-dev.sh`: uv (from PATH, or the pinned download) → `uv python install 3.12` → `uv sync` → `pre-commit install` → git and Docker checks
- [x] `scripts/deps/refresh-hashes.py` (maintainer tool: re-download URLs, rewrite hashes)

### Tests
- Config: valid example, each invalid-field class, aliases, error locations
- Store: migrations from empty, concurrent reader + writer (WAL), event round-trip
- Redaction filter catches API-key patterns
- Scripts (both `.bat` and `.sh`, using local `file://` URLs): a checksum mismatch deletes the file and exits 1; a second run is a no-op; `--dry-run` downloads nothing; a missing prerequisite exits 3 with instructions

### Exit criteria
- `uv run codecoremorph validate examples/cpp11-to-cpp20.yaml` succeeds; broken variants list all errors with their locations
- On a clean Windows machine, a fresh clone → `scripts\bootstrap-dev.bat` (cmd.exe) or `./scripts/bootstrap-dev.sh` (Git Bash) → `uv run codecoremorph --version` works, with no admin rights. Linux and macOS runs move to the CI item in the Backlog (D-28).

---

## Phase 1: Workspace safety and execution layer

**Goal:** CodeCoreMorph can safely create an isolated run workspace, apply multi-file edits atomically, run commands in Docker or natively, and roll back, without ever touching the user's working tree.

**Prerequisites:** Phase 0.

### Tasks
**VCS (`codecoremorph.vcs`)**
- [x] git CLI wrapper with typed errors; detect the repo root, the base commit (`--base`, default `HEAD`), and dirty state (warn only)
- [x] Repo lock (`.codecoremorph/lock`, stale-lock detection); add `.codecoremorph/` to `.git/info/exclude` (FR-6.6, NFR-2.1)
- [x] Create branch `codecoremorph/<run-id>` plus a worktree under the user cache dir; remove both on cleanup (FR-6.2)
- [x] `snapshot()` → SHA; `rollback(sha)` = `reset --hard` + `clean -fd` scoped to the worktree (FR-6.3)
- [x] `commit(message, trailers)` with the SPEC §7.9 format and a configurable author (FR-6.4)
- [x] `diff(since)` → unified diff text plus changed-file list (FR-3.2)
- [x] Build-dir and cache-dir allocation outside the worktree (FR-6.7)

**Patching (`codecoremorph.patching`)**
- [x] `Edit` / `EditBatch` models (replace, create, delete) and JSON Schema for `submit_edits`
- [x] Matcher: exact unique match → whitespace-tolerant unique match → rejection with the closest candidate and line numbers (SPEC §7.4)
- [x] Atomic multi-file apply: in-memory first, then temp file + rename per file; all or nothing (FR-3.3)
- [x] Preserve encoding (detect non-UTF-8 and BOM) and per-file line endings
- [x] Path safety: no `..`, no absolute paths, no `.git/`, no excluded paths, no symlink escape (I-1)

**Sandbox (`codecoremorph.sandbox`)**
- [x] `Executor` protocol and `ExecResult` (SPEC §7.8), with output caps and full logs on disk
- [x] `NativeExecutor`: scrubbed environment, timeouts, process-tree kill on Windows and POSIX, reduced-isolation warning; applies `<CODECOREMORPH_HOME>/toolchains/<lang>.env` when present (SPEC §7.15)
- [x] `DockerExecutor`: one container per run, labels, worktree bind mount, build and cache volumes, non-root user, dropped capabilities, resource limits, `set_network()` via network connect/disconnect, cleanup on exit and crash *(tested against a fake client; the `docker`-marked tests against a real daemon wait for a machine with Docker or for CI, D-28)*
- [x] Executor selection (`auto` / `docker` / `native`) with clear messages (FR-1.3); Windows path translation
- [x] `codecoremorph doctor`: Docker daemon details, plus a warning about Windows bind-mount performance

### Tests
- PatchEngine property tests (Hypothesis): applying, then reverting through the reverse diff, restores the original bytes; all-or-nothing under injected failures; CRLF, LF and BOM preserved
- VCS on temporary repos only: worktree lifecycle, rollback after arbitrary writes, commit trailers, lock contention
- **Safety test:** hash the user's working tree, including untracked and dirty files; run create → edit → commit → rollback → cleanup; assert the hash is unchanged (NFR-2.1)
- Executors: timeout kills child processes; output caps; Docker tests marked `docker` using a tiny public image

### Exit criteria
- In both executor modes, a scripted scenario (create worktree → apply a 3-file edit batch → run a command → roll back → commit → clean up) passes on Linux; native mode passes on Windows
- The safety test passes on Linux and Windows

---

## Phase 2: C++ adapter, toolchain image and rules

**Goal:** CodeCoreMorph can configure, build, test, index and codemod C++ CMake projects written in any standard from C++98/03 to C++20, and raise them to any newer standard up to C++23 (D-38), in Docker and natively, with structured results. Rules exist as data.

**Prerequisites:** Phase 1.

### Tasks
**Adapter framework**
- [x] `LanguageAdapter` ABC, `AdapterContext`, `AdapterCapabilities`, `ToolchainSpec`, `CodemodSpec`, `CodemodResult` (SPEC §7.1); entry-point registry; `codecoremorph adapters list` (FR-15.1)
- [x] Normalized index models and the SQLite-backed index store (SPEC §7.2)

**C++ toolchain image (`images/cpp/`)**
- [x] Dockerfile: Ubuntu 22.04 plus system packages (C/C++ headers, ccache, Python), then `scripts/install-toolchain.sh cpp --prefix /opt/codecoremorph` installs the pinned tools from the manifest, and pip installs the hash-pinned `libclang` wheel. Runs as a non-root user. *(built locally with `scripts/build-image cpp` as `codecoremorph-cpp:local`, 2.75 GB; tool versions match the manifest; publishing waits for CI)*
- [x] Provisioner: resolve the image (override, then the digest in `images/cpp/image.lock`, then a local build by image ID), pull it, start it with its toolchain environment (FR-1.2); native detection of tools and versions with an actionable report (FR-1.3)

**Native C++ toolchain scripts (SPEC §7.15)**
- [x] Decide the Windows native C++ toolchain (SPEC §16 Q9): llvm-mingw (D-32)
- [x] `cpp` rows in the manifest for every supported platform (`#!platforms` coverage lines; no macOS x64, D-36); LLVM pinned to 18.1.8 to match the libclang wheel (D-33)
- [x] `scripts/install-toolchain.bat` and `.sh` with `cpp` support:
  - download, verify and extract each tool (only the needed members of the LLVM archive)
  - build GoogleTest 1.12.1 with the downloaded toolchain
  - write `cpp.env` and `activate-cpp.{bat,sh}`
  - check prerequisites, exiting 3 with install steps
- [x] `scripts/pull-images.bat` and `.sh`; `scripts/build-image.bat` and `.sh`
- [x] `codecoremorph toolchain install|list|path`: thin wrapper that runs the matching script (FR-1.13); the scripts ship in the wheel

**Fixtures (`fixtures/cpp/`)**, intentionally legacy, CMake + GoogleTest, each 300–2,000 LOC:
- [x] C++11: `ownership`, `typedefs`, `templates`, `removed-features`, `c-compat`, `multi-module`
- [x] Other standards (D-38): `cpp98-legacy` (C++98; breaks under C++11) and `cpp17-legacy` (C++17; breaks under C++20 and C++23)
- [x] Each fixture builds and passes its tests under its own standard in the image *(and natively on Windows)*

**C++ adapter (`codecoremorph.adapters.cpp`)**
- [x] `configure`: CMake + Ninja, out of source, `CMAKE_EXPORT_COMPILE_COMMANDS=ON`, standard override
- [x] `build` + diagnostic parsers: clang/gcc (notes, include chains), linker, CMake; MSVC best-effort; paths normalized to the worktree
- [x] `run_tests`: CTest `--output-junit` plus GoogleTest XML per case; stable test IDs; timeouts
- [x] `index`: libclang helper running in the sandbox; parallel over compile commands; emits the normalized JSON; cache keyed by flags and validated by file hashes; marks `extern "C"`, `c_compatible` (headers included from `.c`) and `public_api`; skips excluded paths (FR-2.1, FR-2.3–2.5)
- [x] `run_codemods`: clang-tidy with the rule's checks and `--export-fixes`, header filter restricted to the unit's files; CodeCoreMorph applies the fixes (D-34); `cmake-standard` for the standard bump
- [x] Optional capabilities: `parse_check` (`-fsyntax-only`), `format` (clang-format only when the project has a `.clang-format` file), `detect_disabled_tests`, `query`
- [x] `codecoremorph debug index|build|test|codemod` commands

**Rules (`codecoremorph.rules`)**
- [x] Rule model and YAML loader (SPEC §7.3), entry-point registry, `rules_paths`, `codecoremorph rules list` (FR-9.1, FR-9.3, FR-9.4)
- [x] C++ rule pack v1 (SPEC §11.1): `bump_standard`, `replace_typedefs_with_using`, `convert_raw_pointers_to_smart_pointers`, `use_concepts_for_templates`, `modernize_basics`, with detectors, codemod mappings, guidance Markdown, examples and post-conditions; declares clang-tidy 18 as the minimum

**C++ standards (D-38)**
- [x] `AdapterCapabilities.standards` (oldest first); the C++ adapter supports cpp98, cpp03, cpp11, cpp14, cpp17, cpp20 and cpp23
- [x] Standard conditions on rules (`applies_to.standard_bump`, `min_target_standard`, `max_source_standard`), detector patterns and codemod passes (`standard`); `codecoremorph.rules.applicability`
- [x] `codecoremorph validate` checks standards and rules against the installed adapter and rule packs, with locations
- [x] C++98/03 builds: CMake's 98 for 03, GoogleTest 1.8.1 (manifest row `googletest-cxx98`, built as C++98 by `install-toolchain`) through a CMake initial-cache script
- [x] clang-tidy passes with a `standard` parse under it (`--extra-arg=-std=`), skip C units, and are skipped when the target is older
- [x] Rule pack: `bump_standard` covers every transition from C++98 to C++23 (patterns gated by target, passes under C++11 and C++17, guidance by transition); `modernize_basics` adds C++14/17/20 checks

**Benchmark (`bench/`)**
- [x] Evaluate leveldb, yaml-cpp and tinyxml2 against the SPEC §12 criteria; pick one and pin its commit (§16 Q3): leveldb 1.23 (D-37); `bench/fetch.py` and `bench/measure.py`
- [x] Measure NFR-1.1 (indexing) and record the result in `bench/results/`: in Docker on Windows (20 CPUs), leveldb's 28.7k lines index in 11.8 s cold and 3.2 s warm, a linear 41 s per 100k lines against the 60 s target. Graph building joins the measurement in Phase 3

CI tasks from this phase (build and publish the image, run `install-toolchain cpp` on every OS, the fixtures in the image) moved to the Backlog CI item (D-32).

### Tests
- Diagnostic parsers: golden tests over captured compiler, linker and CMake outputs, including MSVC samples
- Test-report parsing: golden JUnit and GoogleTest XML, including failures, skips and timeouts
- Adapter integration (marked `docker`): every fixture configures, builds, tests and indexes; native runs marked `toolchain`
- Codemod-only migration of `typedefs` to C++20 and to C++23 builds and passes its tests
- Each version fixture builds under its source standard and fails to build under its target; the bump's clang-tidy passes replace `auto_ptr` and `uncaught_exception` in C++98 code
- Applicability and `validate` checks for every supported upgrade, unknown standards, downgrades and rules the target can't use

### Exit criteria
- Every fixture: configure, build, test and index succeed in Docker on Linux CI, and natively where a toolchain exists *(done natively on Windows and in Docker on Windows; Docker on Linux waits for CI, D-28)*
- The `typedefs` fixture migrates with codemods alone and stays green *(done natively and in Docker)*
- The benchmark is chosen and pinned; the NFR-1.1 result is recorded, and any gap has a written plan *(done: 41 s per 100k lines, indexing only; Phase 3 re-measures with graph building)*
- `install-toolchain cpp` works without admin rights on Windows, Linux and macOS, and a fixture builds and passes natively with the installed tools; the image's tool versions match the manifest *(Windows and the image: done; Linux and macOS natively wait for CI, D-28)*

---

## Phase 3: Dependency graph, baseline and planner

**Goal:** `codecoremorph plan` produces a deterministic, explained migration plan for any fixture and the benchmark, without changing code.

**Prerequisites:** Phase 2.

### Tasks
- [x] File graph and symbol graph from the index; SCC condensation into a DAG of clusters (FR-2.1, D-14); reference edges never override includes and pairs (D-39)
- [x] Boundary and public-API classification; fan-in queries (FR-2.2)
- [x] Baseline step: configure, build, test × `baseline_runs`; flaky-test detection; stop if the baseline build fails; store the baseline (FR-7.1)
- [x] Header/source pairing; rule detectors → per-file site counts, with detector patterns filtered by the migration's standards (`rules.applicability`, D-38)
- [x] Run the rule with `applies_to.standard_bump` first, automatically, when the target standard is newer; skip rules that don't apply; `plan` and `run` repeat `validate`'s standard and rule checks (`orchestrator.session`)
- [x] Unit formation per SPEC §7.5.1: project units (standard bump plus predicted fallout), api_preserving cluster units, api_changing fan-in units, `too_large` flag (FR-5.1–5.3)
- [x] Ordering: standard bump first, then rules in manifest order, leaves-first, path tie-break (FR-5.2, FR-5.6)
- [x] Token estimator (character-based, with a margin); size check against the editor model's context from config or package data (`llm/models.json`), since live capability discovery arrives in Phase 4 (FR-5.4)
- [x] Cost and time estimate from the price table (FR-5.5)
- [x] Plan model, `plan.json`, Rich rendering, `--json` output
- [x] `codecoremorph plan`: validate → prepare workspace → provision → baseline → index → plan; no edits (FR-5.7; the baseline runs first, D-39)
- [x] Run worktrees get the base commit's submodules from the user's own checkouts, offline (needed for the benchmark, which vendors GoogleTest; D-39)
- [x] `bench/measure.py` times graph building too (NFR-1.1); `bench/plan.py` plans the benchmark and records the result

### Tests
- Graph: cycles condensed correctly, topological order stable (Hypothesis), fan-in correct (synthetic index)
- Planner determinism: same input gives the same `plan.json`, byte for byte (unit test with the index rows reordered; integration test across two runs)
- Unit sizing and `too_large` handling; `c_compatible` files skipped for C++-only rules
- Baseline flaky detection: unit tests with scripted results, and an integration test whose CTest test fails on every second run (a counter file in the build tree), since the sandboxes pass only allowlisted environment variables
- `codecoremorph plan` on all eight fixtures, natively and in Docker; a failing baseline build exits with code 3 and names the failing file

### Exit criteria
- [x] `codecoremorph plan` works on every fixture and the benchmark; the plan is deterministic; estimates and flags are shown (leveldb: `bench/results/20261102-leveldb-plan-windows-docker.json`)
- [x] A failing baseline build stops with an actionable message

---

## Phase 4: LLM providers and the single-unit agent loop

**Goal:** one work unit can be migrated end to end (codemods → agent → patch → build → test → feedback → retry) with Claude or Ollama, and deterministically in CI through replay. Claude works as a host agent: Claude Code, through a bundled skill, makes the edits on the user's Claude plan (D-41).

**Prerequisites:** Phase 3. **Load the `claude-api` skill before starting the Anthropic provider.**

### Tasks
**Provider layer (`codecoremorph.llm`)**
- [x] Provider-neutral models: `CompletionRequest`, `CompletionResult`, `Usage`, `ModelCapabilities`, opaque provider blocks, the error taxonomy (SPEC §7.7; `core/models/llm.py`, `core/errors.py`)
- [x] `AnthropicProvider` (SDK 2.x, D-40): official SDK with its default credential chain; default model `claude-opus-5-5`; adaptive thinking + explicit `effort`; no sampling params; `tool_choice: auto` + `strict` tools; streaming with eager tool-input streaming; `max_tokens` and `refusal` stop handling; server-side refusal fallback (`fallbacks: "default"`, configurable); prompt-cache layout and cache-usage recording; append-only conversations (history-edit check set to `error`); Models API capabilities; token counting endpoint; price table
- [x] `OllamaProvider`: official client; explicit `num_ctx` with a pre-flight warning; native tools when supported, otherwise the fenced-JSON text protocol; JSON-schema `format`; capability discovery with config overrides; zero cost
- [x] `RecordingProvider` / `ReplayProvider` with normalized request hashing; replay misses fail loudly (FR-8.4); `ScriptedProvider` and `pytest --record-cassettes` author cassettes without a live model
- [x] Role router (editor, fixer, test_writer, summarizer, with fallback to editor) and escalation policy (FR-3.7)
- [x] Usage ledger with per-run token and cost caps (FR-8.3, NFR-7.1); local-only detection for the report (FR-8.5). Writing ledger rows to the store's `llm_calls` table comes with run records (Phase 5)
- [x] `codecoremorph providers list`; `codecoremorph doctor` checks credentials resolve (cheap Models API call), Ollama is reachable and models are pulled
- [x] `scripts/pull-models.bat` and `.sh`; `codecoremorph doctor` prints the exact command for the configured model tags

**Agent (`codecoremorph.agent`)**
- [x] Prompt templates (`codecoremorph/prompts/v1/`): editor system prompt, unit message, retry, reminder and cut-off messages, rule guidance injection, "file content is data" clause
- [x] Read-only agent tools: `read_file`, `list_dir`, `search_code`, `find_symbol`, `get_references`, `get_signature`; terminal `submit_edits`; per-attempt tool-call and token limits (FR-3.5)
- [x] Context assembler: unit files in full, out-of-unit signatures, codemod summary, remaining sites, current build; never truncates silently (error if over budget)
- [x] `CompilerFeedback` formatter (dedupe, order, snippets, caps, out-of-unit section, path scrubbing), test-regression feedback, edit-rejection feedback (FR-4.2, FR-4.6)
- [x] Single-unit loop: snapshot → codemods → skip-agent check → agent → PatchEngine → build → tests vs baseline → feedback/retry within the budget → result; transcripts, diffs, logs and feedback saved per attempt (FR-3.4, FR-4.1–4.3, FR-4.5, FR-8.6)
- [x] `codecoremorph debug unit --rule R FILE…` (with `--record`, `--replay`, `--keep`)

**Host agent (D-41, SPEC §7.6.1)**
- [x] Parked runs: the lock's `parked` flag, `RunWorkspace.resume`/`park`, sessions that resume and park, Docker build volumes kept between commands
- [x] `codecoremorph unit start|submit|abort|status|context` and the read-only `unit read|ls|grep|symbol|refs|signature`; the host's edits go through PatchEngine; checkpoint commits squashed on success; changes made outside CodeCoreMorph discarded and reported
- [x] Host context document (`prompts/v1/host_system.md`, `host_unit.md`)
- [x] The `codecoremorph` Claude Code skill (`src/codecoremorph/skills/codecoremorph/`) and `codecoremorph skill install`
- [x] Fixes from the leveldb trial (D-42): chained units build at the standard their base sets; uncompiled code is indexed, warned about and checked for repeated build errors; the header filter accepts `./` paths; codemod lines follow the project's `.clang-format`; the bump leaves CMake comments alone; `unit start` honors skip lists and takes `--plan-unit`; `unit list`; commit messages name the codemods' files; baselines are reused by commit; `api_changing` units own their fan-in's sites with header/source partners and list `reference_files`; the skill explains the plan's scope, runs every pending unit when asked and reports excused tests and uncompiled files

**Evals**
- [x] Fixture eval set: 15 units across the C++ rules, with pass criteria (`bench/evals/cpp.yaml`)
- [x] `bench/evals.py` runs the set against live providers and records success rate, attempts, tokens and cost (manual; costs money with paid providers)
- [x] `bench/evals.py --agent claude-code` runs the set with headless Claude Code and the skill (manual; uses the Claude plan's usage, no API cost)

### Tests
- Provider unit tests against recorded-style responses (the real SDKs on mock transports): tool-call parsing, truncated tool input not run, refusal path, `max_tokens` path, mid-output fallback, no sampling params sent to Anthropic, `num_ctx` always sent to Ollama, text-protocol parsing, error mapping, missing credentials
- Ledger cap enforcement (overshoot ≤ one call)
- Feedback formatter golden tests (`tests/data/feedback/`)
- Agent loop with a scripted model and a fake adapter: tool use, build-failure retry, edit rejection, scope, budget rollback, refusal, no submission, cut-off replies
- Single-unit e2e with cassettes on the `typedefs`, `ownership` and `removed-features` fixtures, native and Docker (`tests/integration/test_agent_e2e.py`)
- Host agent: the flow with a fake adapter (`tests/unit/agent/test_host.py`); parked locks and workspaces; the `unit` commands end to end on the `ownership` fixture, native and Docker, with a rejection, a build failure fed back and a success, and an abort (`tests/integration/test_host_e2e.py`); the skill's commands exist in the CLI (`tests/unit/test_skill.py`)
- Trial regressions (D-42): a unit on the bump's branch builds at C++20 and reuses its baseline (`test_host_e2e.py`); clang-tidy fixes reach headers found through `include_directories(".")` and keep the project's pointer style (`test_cpp_fixtures.py`); the bump leaves comments alone and the build files' standard is detected (`test_cmake.py`); echoes of build errors in uncompiled files, skip lists, plan units, baseline reuse, planner ownership (`test_unverified.py`, `test_host_plan.py`, `test_baseline.py`, `test_planner.py`)

### Exit criteria
- [x] Single-unit migrations replay deterministically and pass (natively and in Docker; CI waits for the Backlog, D-28)
- [x] Live: the Claude eval run (Claude Code with the skill, D-41) is complete with its success rate recorded: 14 of 15 (93%) on 2026-11-02, native, in 17.5 minutes (`bench/results/20261102-evals-claude-code-default.json`). The one failure, `ownership-item`, was a deliberate abort: changing `Item::clone()` to return a `std::unique_ptr` needs its callers, which lie outside the case's files
- [ ] Live: an Ollama model completes at least one unit end to end, with its success rate recorded (not gated). `qwen3:4b` (32k context) passed only the codemods-only control, 1 of 4, with two request timeouts (`bench/results/20261102-evals-ollama-qwen3-4b.json`); a larger local model is next

---

## Phase 5: Run orchestration, the MVP (M1)

**Goal:** `codecoremorph run` migrates a whole C++11 project to C++20, unit by unit, on an always-green branch, with gates, resume, budgets and a report.

**Prerequisites:** Phase 4.

### Tasks
- [x] LangGraph run graph per SPEC §7.5 (D-43: the session's setup precedes the graph; a unit is one node plus `unit_gate`); small state; SQLite checkpointer; all LangGraph calls in `orchestrator/graph.py`
- [x] Gates via LangGraph interrupts plus the store's `approvals` table: plan gate (default required) and unit gate policies (FR-10.1, FR-10.3); CLI interactive approval and `--yes` (FR-11.4)
- [x] Host-agent runs (D-41, D-44): the run graph parks at each unit's agent step (an interrupt), and `codecoremorph unit submit`/`abort` resume it, so Claude Code can drive a whole `run` through the skill (`run --host-agent`); extend the skill from single units to runs
- [x] Invariants module I-1 to I-11 (SPEC §7.10; I-4 comes with Phase 6), including the test-removal and test-disable guard (FR-7.5, phase-5 part) and the boundary check by re-indexing (FR-2.2); in single host units too
- [x] Rule post-condition evaluation (FR-4.4): `detector_empty`, `builds_cleanly`, `codemod_clean`, `hook`
- [x] Commit with trailers on success; rollback on failure; failed units don't block later ones (§4.5)
- [x] Budgets: per-unit wall clock, per-run tokens and cost, graceful `budget_exhausted` (FR-14.1, FR-14.2)
- [x] Resume (`codecoremorph resume`): reset the worktree to the branch HEAD, restore the checkpoint (FR-14.3); units committed before a kill are recognized by their trailers
- [x] Abort: `codecoremorph abort` and Ctrl-C handling, with a second Ctrl-C forcing (FR-14.4); the first Ctrl-C parks the run (D-43)
- [x] Final verification: clean configure, full build, full tests (`LanguageAdapter.clean_build`)
- [x] Reports: Markdown + JSON per FR-13.1, with a review-items section (FR-10.4); `codecoremorph report`
- [x] CLI: `run`, `status` (`--json`), `approve`, `reject`, `resume`, `abort`, `report`, `clean`; exit codes per SPEC §7.14 (FR-11)
- [x] Plan-gate exclusions from the CLI (`codecoremorph approve RUN --exclude U-0012,...`) (FR-5.8)

### Tests
- E2E with cassettes: every fixture migrated C++11 → C++20; every commit on the branch builds and passes tests (checked by replaying the branch)
- **Crash consistency:** kill the engine at randomized points (between nodes, mid-build, mid-LLM call); resume; the branch is green and the run completes (NFR-2.4)
- Budget exhaustion mid-unit leaves a green branch and a report
- Gate flows: approve, reject, exclude, resume while waiting
- The working-tree safety test, repeated over a full run

Landed so far: the run graph with the fake adapter (`tests/unit/orchestrator/test_run_engine.py`: commits, plan and unit gates, exclusions, parking and resume, failures, abort, Ctrl-C, the token budget, a unit committed before a crash); the CLI's lifecycle (`test_lifecycle.py`: decisions, offline aborts, status, clean); crash consistency, with the engine killed in its own process at six points (baseline build, unit verification, mid model call, the agent's verification, after a commit before its checkpoint, final verification) and resumed (`test_crash.py`); invariants and post-conditions (`tests/unit/invariants/`, `test_loop_invariants.py`, `test_postconditions.py`); whole runs with real toolchains, native and Docker (`tests/integration/test_run_e2e.py`: `typedefs` without a model, with the user's checkout byte-identical afterwards; `ownership` to C++20 with the bump and one agent unit replayed from a cassette, the other units excluded at the plan gate); host-agent runs with the fake adapter (`test_host_run.py`: parking, submit, failed and rejected attempts, `unit abort`, gates, a failed submit, a lost unit end, an offline abort) and through the CLI with real toolchains (`tests/integration/test_host_run_e2e.py`); every fixture's designed migration as a whole run, all its units included, from the edits of a host-agent run of each fixture (`test_every_unit_of_a_fixture`, cassettes `run-all-<fixture>-<sandbox>.json`, D-45). The M1 benchmark driver is `bench/run.py`: headless Claude Code with the skill runs the whole leveldb migration through `run --host-agent`.

The M1 benchmark run (2026-11-03, Docker): 24 of 27 units committed, the 3 others given up with reasons; 2 h 11 min against the plan's 2 h 19 min; the final clean build passed all 258 tests (`bench/results/20261103-m1-leveldb-windows-docker-claude-code.json`, with `bench/work.py`'s plan-against-run section). It exposed a false `delete` detector match, now fixed with a regression test.

### Exit criteria (M1)
- `codecoremorph run examples/cpp11-to-cpp20.yaml` against every fixture completes with every unit committed, or with failures explained in the report *(done: `test_every_unit_of_a_fixture`, natively and in Docker)*
- A benchmark run with Claude (Claude Code with the skill) is complete; success rate and duration are recorded in `bench/results/`; provisional SPEC §13 targets are re-set from the data *(done: 89% of units, 2 h 11 min; SPEC §13 and §12)*
- All safety tests are green on Linux and Windows *(Windows: done; Linux waits for CI, D-28)*

---

## Phase 6: Characterization tests

**Goal:** untested code gets behavior-pinning tests before migration, and test edits during migration can't silently weaken them.

**Prerequisites:** Phase 5.

### Tasks
- [x] Untested-symbol detection from the symbol graph (direct reference or one call level from test files); `scope: all` option (SPEC §7.11)
- [x] `test_writer` prompts and a GoogleTest generation flow per unit, capped at `max_tests_per_unit`; host-agent jobs through `unit submit|abort` (D-46)
- [x] Determinism lint (no time, random, filesystem, network or sleep/thread-timing APIs) (FR-7.3)
- [x] Acceptance: compiles, passes 3 times on the base code; one regeneration attempt; drop and report rejects
- [x] `adapter.register_test_files` for CMake (generated `CMakeLists.txt` include)
- [x] `characterize` graph node: commit before migration units; extend the baseline (FR-7.4)
- [x] `adapter.extract_assertions` for GoogleTest; assertion guard I-4; approval routing for `test_changes` (FR-7.5); `guardrails.test_file_edits`
- [x] `keep: false` removal at the end of the run (FR-7.6); report section (counts, rejects, flagged edits)
- [x] `untested` fixture

### Tests
- **Mutation sanity check:** inject behavior changes into the `untested` fixture (off-by-one, swapped branches, changed constants); generated tests catch at least the threshold agreed in this phase (start at 60%, record the actual rate)
- Determinism lint unit tests; assertion-guard detection on edited tests

Landed so far: GoogleTest parsing, the determinism lint and CMake registration (`tests/unit/adapters/cpp/test_characterization.py`); selection and jobs (`tests/unit/parity/test_selection.py`); the step in the run graph with the fake adapter, by model and by host agent: regeneration, dropped cases, nothing kept, the lint, off, `keep: false`, a job given up (`tests/unit/orchestrator/test_characterize.py`); I-4 as a check and in the unit loop, with the policy, characterization tests and `test_file_edits: deny` (`test_assertion_guard.py`, `test_loop_invariants.py`). With real toolchains: the `untested` fixture's characterization tests from a host-agent run (`tests/cassettes/cpp/edits/untested.json`) build, pass three times and catch 8 of 8 mutations (`test_cpp_fixtures.py`), and its whole run (characterization, then the bump and the typedef unit) replays from a cassette (`test_every_unit_of_a_fixture[untested]`). The other fixtures' recorded runs keep characterization off.

### Exit criteria
- [x] On `untested`, characterization tests are generated, accepted and committed, the migration completes, and the mutation check meets its threshold: 10 tests from a host-agent run (one regenerated), 8 of 8 mutations caught (threshold 60%)
- [x] Assertion edits are always flagged and gated per policy

---

## Phase 7: Web dashboard

**Goal:** a local dashboard to watch runs live, review plans and diffs, and answer gates.

**Prerequisites:** Phase 5 (Phase 6 is not required).

### Tasks
**Backend (`codecoremorph.dashboard`)**
- [x] FastAPI app with the endpoints in SPEC §7.13; read-only access to run data; approvals and aborts written through the store
- [x] SSE event stream tailing the `events` table (≤ 2 s latency, NFR-1.5)
- [x] Security: bind to 127.0.0.1, per-launch token (query param exchanged for a cookie), Host allowlist, no CORS, `--host` warning (FR-12.7)
- [x] `codecoremorph dashboard [--port]` opens the browser
- [x] The planner keeps the file graph (`graph.json`) and the engine records its steps as events (D-48)
- [x] An interactive gate prompt gives way to a decision made in the dashboard (FR-11.4, D-48)

**Frontend (`dashboard-ui/`)**
- [x] Vite + React + TypeScript scaffold, routing, TanStack Query, SSE hook, light and dark themes
- [x] Runs list; run overview (status, step, progress, budget meters, current activity) (FR-12.1)
- [x] Plan review: units table with rules, flags and estimates; approve, reject, exclude (FR-12.2)
- [x] Dependency graph: Cytoscape.js, colored by unit status, search and filter (FR-12.3)
- [x] Unit detail: attempt timeline, Monaco side-by-side diff, diagnostics, test results, invariant results, transcript viewer; unit gate actions (FR-12.4)
- [x] Report view and download (FR-12.5)

**Packaging**
- [x] A hatch build hook puts the built UI into the wheel (`codecoremorph/dashboard/static`); the dev-mode Vite proxy works (`codecoremorph dashboard --dev`). Building the UI in CI waits for CI (D-28)
- [x] `bootstrap-dev` installs a pinned portable Node LTS (`dev` group in the manifest)

### Tests
- API tests (pytest + FastAPI's test client): auth, Host check, endpoints, SSE, and a live run answered over HTTP with its events within 2 s (`tests/unit/dashboard/`)
- Frontend: Vitest tests (diff parsing, routing, events, pages); one Playwright smoke test (start a run → approve the plan and the unit in the UI → see the run finish, `-m browser`)

### Exit criteria
- [x] On a live fixture run, the plan gate and a unit gate can be approved from the dashboard, and progress updates live (`tests/unit/dashboard/test_browser.py`, in Chrome on Windows)
- [x] An installed wheel serves the dashboard without Node present (checked on Windows: a fresh venv with the wheel, no Node on PATH, served the UI and the API)

---

## Phase 8: Hardening and v0.1.0 release (M2)

**Goal:** a public, installable, documented, reviewed release.

**Prerequisites:** Phases 6 and 7.

### Tasks
- [ ] `codecoremorph init`: detect CMake, C++ standard, source, test and include dirs; write a commented manifest (FR-1.4)
- [ ] Docs site (MkDocs Material): getting started, concepts, config reference generated from the JSON Schema, rule authoring, adapter authoring, provider authoring, security model, troubleshooting (Docker on Windows/WSL2), FAQ
- [ ] Security review against SPEC §10: path traversal fuzzing, a new `injection` fixture (adversarial comments) run end to end (the agent must not follow embedded instructions, and no out-of-scope edits), secret redaction audit, container hardening check, dashboard auth tests; write `SECURITY.md`
- [ ] Performance: NFR-1.1 and NFR-1.2 checks in a nightly CI job on the benchmark; memory profiling of a full benchmark run
- [ ] Plugin API versioning (`api_version` checks with clear errors) and a template repo for third-party plugins (FR-15.3)
- [ ] Optional image signing (cosign)
- [ ] Release engineering: semantic versioning, CHANGELOG, PyPI trusted publishing, image publishing with digests baked into the release, CONTRIBUTING, CODE_OF_CONDUCT, issue and PR templates
- [ ] Ship `scripts/` as package data so `codecoremorph toolchain install` works for PyPI installs (FR-1.13); `--offline DIR` installs (FR-1.12); a docs page on dependency setup, including machines without internet access
- [ ] Register `codecoremorph` on PyPI; resolve SPEC §16 Q6, Q7 and Q8

### Exit criteria (M2)
- On clean Windows, macOS and Linux machines: `uv tool install codecoremorph` → `codecoremorph init` → `codecoremorph run` on a fixture succeeds with Docker
- The security review is complete with no open high-severity findings
- v0.1.0 is tagged, published and documented

---

## Phase 9: Java adapter and Java version upgrades (M3)

**Goal:** the second language. Java projects can be indexed, built and tested, and upgraded across Java versions.

**Prerequisites:** Phase 8 (Phase 5 at minimum).

### Tasks
- [ ] `images/java/`: LTS JDK(s) needed for the source and target versions, Maven, Spoon-based indexer jar, OpenRewrite CLI or Maven plugin
- [ ] `java` rows in the manifest and `install-toolchain java` (Temurin JDKs, Maven, indexer jar)
- [ ] Spoon indexer helper emitting the normalized index (symbols, references, imports, `native` methods as JNI boundaries)
- [ ] Maven build driver and javac diagnostic parser; Surefire XML test parsing (JUnit 4/5); `fetch_only` network step for dependency resolution
- [ ] OpenRewrite codemod runner; `detect_disabled_tests` and `extract_assertions` for JUnit; `register_test_files`
- [ ] Java fixtures (Java 8, Maven, JUnit): legacy APIs, raw types, anonymous classes, `Date`/`Calendar`, removed or deprecated APIs
- [ ] Java version-upgrade rule pack (e.g. 8 → 21): `bump_java_version` (project), language-feature rules (diamond, lambdas, try-with-resources, `var` where safe, switch expressions, records where safe), each mapped to OpenRewrite recipes plus LLM guidance
- [ ] Characterization tests with JUnit 5
- [ ] Live evals on Java fixtures

### Exit criteria (M3)
- Java fixtures migrate 8 → 21 end to end, branch green; zero changes to the orchestrator graph were needed (NFR-3.1 check)

---

## Phase 10: C# adapter and C# version upgrades (M4)

**Goal:** the third language. C# projects can be indexed, built and tested, and modernized to a newer language version.

**Prerequisites:** Phase 9 (Phase 5 at minimum).

### Tasks
- [ ] `images/dotnet/`: pinned .NET SDK, Roslyn-based indexer tool
- [ ] `csharp` rows in the manifest and `install-toolchain csharp` (.NET SDK archive, Roslyn indexer)
- [ ] Roslyn indexer helper (symbols, references, `DllImport`/`LibraryImport` as P/Invoke boundaries)
- [ ] `dotnet build` driver plus MSBuild diagnostic parser; `dotnet test --logger trx` parsing (NUnit, xUnit, MSTest); `fetch_only` restore step
- [ ] Roslyn analyzer and `dotnet format` codemod runner; disabled-test and assertion extraction
- [ ] C# fixtures (older language-version style, SDK-style projects)
- [ ] C# modernization rule pack: `bump_lang_version` (project), nullable reference types, file-scoped namespaces, pattern matching, records where safe, collection expressions where applicable
- [ ] Live evals on C# fixtures

### Exit criteria (M4)
- C# fixtures migrate end to end, branch green; no orchestrator-graph changes needed

---

## Phase 11: Cross-language migration, Java → C# (M5)

**Goal:** port a Java project to C# with translated tests proving parity.

**Prerequisites:** Phases 9 and 10.

**Reuse (D-47):** build the migration pairs, the symbol map, test translation and the roles language-neutral. Phases 13 and 14 reuse them for C++ → Rust.

### Tasks
- [ ] Migration-pair support in config and orchestrator (source adapter plus target adapter); SPEC update for the relaxed green invariant (`pending_parity`)
- [ ] `scaffold_project` for C# (solution, projects, test project) driven by the Java module structure
- [ ] Symbol map store (Java FQN → C# name) and `java_to_csharp` mapping rule pack (collections, streams → LINQ, exceptions, generics, naming conventions)
- [ ] Skeleton-first stage: generate compiling C# skeletons for every type
- [ ] Body-filling stage: units leaves-first, each filling bodies and translating its tests (JUnit → NUnit/xUnit); parity by mapped test IDs
- [ ] Multi-agent roles as subgraphs: source analyst, target author, mapping steward, reviewer
- [ ] Cross-index boundary checks for mixed codebases (FR-2.2)
- [ ] Dashboard: two-sided view (Java source ↔ C# target) in unit detail
- [ ] Java → C# fixtures and evals

### Exit criteria (M5)
- A Java fixture is fully ported: the C# solution builds, every translated test passes, the mapping table is complete, and the report lists every deviation

---

## Phase 12: Rust target and C++ FFI → Rust (M6)

**Goal:** replace a C++ module behind a C ABI with a Rust implementation, verified by the existing C++ tests.

**Prerequisites:** Phase 5 (Phase 11 is not required).

### Tasks
- [ ] `images/rust/`: pinned toolchain, cargo-nextest, cbindgen, plus the C++ toolchain for mixed builds
- [ ] `rust` rows in the manifest and `install-toolchain rust` (pinned `rustup-init` and toolchain, cargo-nextest, cbindgen)
- [ ] Rust adapter: index (syn-based or rust-analyzer-based helper), `cargo build --message-format=json` diagnostics, nextest JUnit tests
- [ ] Mixed-build integration via Corrosion; `scaffold_project` for a crate inside the CMake tree
- [ ] `cpp_ffi_to_rust` strategy: choose a module with an `extern "C"` boundary, generate the crate, port its internals, keep the ABI identical (cbindgen header diff = empty), run the C++ tests through the ABI
- [ ] ABI invariant (I-6 over exported C signatures)
- [ ] Corrosion pinned in the manifest and installed under `CODECOREMORPH_HOME`, so configuring never downloads it (`FetchContent`)
- [ ] Cargo dependencies: `Cargo.lock` committed, crates fetched in the `fetch_only` network step into a `CARGO_HOME` under `CODECOREMORPH_HOME`, builds run `--locked --offline`
- [ ] Windows native: the Rust target that links with llvm-mingw (`x86_64-pc-windows-gnullvm`), so no Visual Studio is needed (D-32)
- [ ] Fixture: a C++ library with an `extern "C"` API and GoogleTest tests

### Tests
- Unit: Cargo JSON diagnostics, nextest JUnit parsing, the ABI invariant (cbindgen header diff)
- Integration (`toolchain`, `docker`): the fixture's mixed build, with its C++ tests passing before and after the module is replaced
- End to end: the fixture's run replays from recorded host-agent edits, natively and in Docker

### Exit criteria (M6)
- The fixture module is replaced by Rust; the original C++ tests pass unchanged; the exported ABI is identical

---

## Phase 13: C++ → Rust, module by module (M7)

**Goal:** port every module of an old C++ project (C++98 or newer) to Rust while the project's own C++ tests keep verifying it. Modules go leaves first; each ported module keeps its C++ interface as a façade over the Rust code, so every commit builds and passes the baseline tests (SPEC §11.6, D-47).

**Prerequisites:** Phases 11 (migration pairs, symbol map, roles) and 12 (Rust adapter, mixed builds, ABI invariant).

### Tasks
**Strategy**
- [ ] SPEC §11.6 settled with a `D-xx` row: stages, bridge, manifest keys, unit kinds
- [ ] Bridge spike on `port-legacy`: the `cxx` crate (C++ classes as opaque types, `std::string`, `std::vector`, `std::unique_ptr`, exceptions ↔ `Result`) against a plain C ABI with opaque handles and cbindgen headers (works from C++98). Pick the default and record the C++ standard it needs; an older source gets the standard bump (`bump_standard`) as the port's first unit
- [ ] Manifest: `migration.target: {language: rust, edition: …}` and a `migration.port` section (`strategy`, `bridge`, `keep_cpp_api`, rules); `validate` checks the pair against the installed adapters

**Scaffold and planning**
- [ ] `scaffold_project` for Rust inside a C++ tree: a Cargo workspace beside the CMake project, one crate per CMake library target, built from CMake through Corrosion; the scaffold is the port's first unit after any standard bump
- [ ] Port planning: `port_module` units from the file graph's clusters, leaves first. Each lists the C++ files it ports, the C++ callers outside it (they keep calling through the façade) and its part of the symbol map. Header-only templates and inline functions are planned with their last C++ user
- [ ] Estimates that grow with a unit's size (the M1 run's agent time grew with lines; the planner's flat per-unit estimate doesn't model it)

**Port units**
- [ ] A unit ports one cluster: the Rust module; bridge declarations for the API that C++ outside the unit still uses (callers and tests); and the C++ façade, whose header keeps its API while its sources forward through the bridge
- [ ] `cpp_to_rust` mapping rule pack (guidance plus checks):
  - ownership: `unique_ptr` → `Box`, `shared_ptr` → `Rc`/`Arc`, non-owning pointers → references or `Option<&T>`
  - strings, containers and iterators
  - exceptions and status codes → `Result`
  - class hierarchies → traits; templates → generics; operator overloads → `std::ops` and `std::cmp` traits; RAII → `Drop`
  - integer semantics: unsigned wraparound → `wrapping_*`; signed overflow (undefined in C++) panics in a debug build
  - `static` state → `OnceLock`; threads and locks → `std::sync`; macros → `const`, functions or `macro_rules!`; namespaces → modules; C strings, `printf` and iostreams
- [ ] Symbol-map entries (C++ USR → Rust path) for every symbol of a ported cluster, kept consistent by the mapping steward (Phase 11)
- [ ] Checks: no `unsafe` outside the bridge (error); no new `cargo clippy` warnings in ported code (as I-10); the façade headers' API unchanged (I-6); the exported C ABI unchanged (Phase 12)
- [ ] Host-agent support: the skill and the context documents of port units show the C++ source, the Rust target, the façade and the symbol map
- [ ] Dashboard: Phase 11's two-sided view for C++ ↔ Rust units

**Fixtures and evals**
- [ ] `port-legacy` (C++98 style, GoogleTest 1.8.1): a string utility, a value class with operators, a container template, a class hierarchy with virtual functions, exceptions, a status-code API, a small command-line program
- [ ] `port-modern` (C++11/14): `unique_ptr` and `shared_ptr` ownership, a callback API, `static` state, a thread with a mutex
- [ ] Live evals of port units on both fixtures (Claude Code with the skill; the built-in loop)

### Tests
- Unit: port planning (leaves first; templates move with their last C++ user), symbol-map consistency, the `unsafe` and clippy checks, the façade API check
- Integration (`toolchain`, `docker`): each fixture's mixed CMake + Cargo build after each recorded unit
- End to end: each fixture's port up to M7 replays from recorded host-agent edits (as in D-45), natively and in Docker
- Safety: working tree byte-identical, crash and resume, and path traversal on a port run

### Exit criteria (M7)
- Every module of `port-legacy` and `port-modern` is Rust; every commit on the branch built and passed the original C++ tests; `unsafe` appears only in the bridge
- Windows native and Docker (Linux waits for CI, D-28)

---

## Phase 14: C++ → Rust, finished (M8)

**Goal:** finish the port. The tests become Rust tests mapped one to one onto the originals, the façades and the C++ build go, and what's left is a Cargo workspace. Measured on a real old C++ project.

**Prerequisites:** Phase 13.

### Tasks
- [ ] Test-translation units: a C++ test file is translated once every project module it uses is Rust. GoogleTest suites become Rust tests (integration tests under `tests/` for the public API, `#[cfg(test)]` modules otherwise), fixtures become helper types, `ASSERT_*`/`EXPECT_*` become `assert!`/`assert_eq!` (noting where `EXPECT_*` continuing after a failure mattered). Both versions pass until retirement
- [ ] Test-ID map as parity (`gtest:Suite.Case` ↔ the Rust test's path): every baseline test maps to a passing Rust test, or the report says why not. The assertion guard (I-4) compares each translation's assertions with the original's
- [ ] Characterization tests (Phase 6) translate with the rest
- [ ] Retirement units: a façade and its bridge declarations go once no C++ calls them, the C++ tests once translated, then the CMake build; executables become `bin` targets. `keep_cpp_api: true` keeps the public headers, a thin façade library and its C++ tests for outside C++ consumers
- [ ] Final verification of a pure-Rust result: `cargo build` and `cargo nextest` from clean, `--locked --offline`
- [ ] Report: the symbol map, the test map, deviations the agent recorded (behavior it couldn't keep, such as overflow or error-message text), the `unsafe` count with locations, any C++ left and why
- [ ] Benchmark (M8): pick and pin a real old C++ project (C++98/03 or C++11, CMake, tests, 2–10k lines, permissive license); port it whole with Claude Code; record time, cost, units committed, tests mapped and `unsafe` in `bench/results/`; set SPEC §13 targets from it
- [ ] Docs: a guide to porting C++ to Rust (modernize first with the C++ upgrade, then port)

### Tests
- Unit: test-ID mapping and the cross-language assertion comparison; retirement order (a façade goes only when no C++ calls it)
- End to end: each fixture's whole port to pure Rust replays from recorded edits, natively and in Docker

### Exit criteria (M8)
- `port-legacy` (C++98) and `port-modern` end as pure Rust: no C++ left (or only the kept API with `keep_cpp_api`), every original test maps to a passing Rust test, and the branch was green at every commit
- The benchmark project is ported and measured, and SPEC §13 has its targets

---

## Backlog (unscheduled)

- **CI (GitHub Actions)**, deferred from Phase 0 (D-28):
  - lint, type-check and tests on `ubuntu-latest` and `windows-latest`, Python 3.12 and 3.13
  - shellcheck (POSIX mode) on every `.sh`, the manifest format check, and a check that `.sh` scripts are executable in git
  - `bootstrap-dev` on clean Windows, Linux and macOS runners
  - the `docker`-marked tests, and the Linux parts of every phase's exit criteria
  - `dashboard-ui`: `npm ci`, type check, Vitest and build before the wheel (`CODECOREMORPH_REQUIRE_DASHBOARD_UI=1`), then the `browser` test with Playwright's Chromium
  - build the toolchain images and publish them to the registry (GHCR assumed, §16 Q8), writing each digest to `images/<lang>/image.lock`
  - `install-toolchain cpp` on Windows, Linux and macOS when scripts or the manifest change, and nightly, then build and test one fixture natively
- Parallel unit execution across independent DAG branches (separate worktrees)
- Golden-I/O differential harness (deferred by D-08)
- Coverage-based untested-code detection (gcov/llvm-cov, JaCoCo, coverlet)
- LLM reviewer pass on each unit's diff before commit
- Message Batches API for bulk characterization-test generation (cost)
- Gradle and Bazel support; MSBuild-native C++ (vcxproj)
- Worktree-to-volume sync for faster Docker on Windows and macOS
- Starting runs from the dashboard (FR-12.6), if not done in Phase 7
- IDE extension and CI/PR bot (D-04)
