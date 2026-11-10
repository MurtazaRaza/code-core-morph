# CodeCoreMorph: Implementation Plan

This plan turns [SPEC.md](SPEC.md) into an ordered set of phases. Each phase has a goal, its prerequisites, a task checklist, tests, and exit criteria. Requirement IDs (`FR-`, `NFR-`, `D-`) point back to the spec.

**Order of work (D-21, D-47, changed by D-55, D-67 and D-77).** Language version upgrades come first: C++ in the MVP. Then C++ → Rust, from one module behind a C ABI (Phase 12) to a whole project ported (Phases 13 and 14). The v0.1.0 release (Phase 8's publishing steps) waits, and Java (9) and C# (10) follow the Rust phases. Then legacy code to modern languages (D-67): C → Rust (15), then Rust → C (16). Then C# → C++ (17) and C# → Rust (18), the C# side built once for both (D-77). Java → C# (11) is in the Backlog. The phases keep their numbers, so the order of work is 12, 13, 14, 9, 10, 15, 16, 17, 18.

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
| **M5: Cross-language** | 11 | Java → C# port with translated tests (in the Backlog, D-67) |
| **M6: Rust** | 12 | A C++ module behind a C ABI replaced by Rust |
| **M7: C++ → Rust, every module** | 13 | Every module of an old C++ project is Rust, behind its unchanged C++ interface; the original tests passed at every commit |
| **M8: C++ → Rust, finished** | 14 | Pure Rust: tests translated and mapped one to one, C++ retired, a real project ported and measured |
| **M9: C → Rust** | 15 | An old C project built with Make or autotools ported to Rust module by module, its tests mapped one to one, a real C library ported and measured |
| **M10: Rust → C** | 16 | A Rust crate ported to C behind Rust façades, its tests translated, the C checked under sanitizers and clang-tidy |
| **M11: C# → C++** | 17 | A C# library ported to C++ behind C# façades over P/Invoke, its tests translated to GoogleTest, the C++ checked under sanitizers; or kept as a .NET library over the C++ |
| **M12: C# → Rust** | 18 | The same port to a Cargo workspace, on Phase 17's C# side; or kept as a .NET library over Rust |

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
| 11 | Java → C# (**M5**, in the Backlog by D-67) | Migration pairs, scaffolding, symbol map, skeleton-first strategy, test translation, multi-agent roles | §11.4 |
| 12 | Rust / C++ FFI → Rust (**M6**) | Rust adapter, C-ABI replacement strategy, Corrosion integration | §11.5 |
| 13 | C++ → Rust, module by module (**M7**) | Bridge, Cargo workspace scaffold, leaves-first port units behind C++ façades, `cpp_to_rust` mapping pack, `unsafe` and clippy checks | §11.6 |
| 14 | C++ → Rust, finished (**M8**) | Test translation and test-ID map, retirement of façades and the C++ build, report, real-project benchmark | §11.6 |
| 15 | C → Rust (**M9**) | C as a language, Make as a build backend (captured builds), C test formats, `c_to_rust` pack, bindgen's `sys` module, real-library benchmark | §11.7 |
| 16 | Rust → C (**M10**) | Rust source index, Rust façades over C, `rust_to_c` pack, sanitizer and clang-tidy checks | §11.8 |
| 17 | C# → C++ (**M11**) | C# as a port source, C# façades over a C ABI, the generated P/Invoke declarations, calls back into C#, `csharp_to_cpp` pack, GoogleTest translation, a .NET + C++ + Rust image | §11.9 |
| 18 | C# → Rust (**M12**) | Phase 17's C# side over Rust `cdylib`s, the ownership redesign, `csharp_to_rust` pack | §11.10 |

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
- [x] `codecoremorph init`: detect CMake, C++ standard, source, test and include dirs; write a commented manifest (FR-1.4, D-49)
- [x] Docs site (MkDocs Material): getting started, concepts, config reference generated from the JSON Schema, rule authoring, adapter authoring, provider authoring, security model, troubleshooting (Docker on Windows/WSL2), FAQ (`mkdocs.yml`, `docs/`, D-53); the configuration and CLI references are generated at build time, and `tests/unit/test_docs.py` builds the site strictly. Publishing it waits for the release
- [x] Security review against SPEC §10: path traversal fuzzing, a new `injection` fixture (adversarial comments) run end to end (the agent must not follow embedded instructions, and no out-of-scope edits), secret redaction audit, container hardening check, dashboard auth tests; write `SECURITY.md` (D-52: four findings fixed, among them invariant I-12)
- [x] Performance: memory profiling of a full benchmark run (`bench/memory.py`; the engine records its peak resident memory in the run's environment). NFR-1.1 and NFR-1.2 checks in a nightly CI job wait for CI (D-28)
- [x] Plugin API versioning (`api_version` checks with clear errors) and a template repo for third-party plugins (FR-15.3, D-50): `templates/plugin/`, which becomes its own template repository when the project is published
- [ ] Optional image signing (cosign): with image publishing, which waits for CI (D-28) and the registry (Q8)
- [ ] Release engineering: semantic versioning, CHANGELOG, PyPI trusted publishing, image publishing with digests baked into the release, CONTRIBUTING, CODE_OF_CONDUCT, issue and PR templates. Landed so far: `CHANGELOG.md`, `CONTRIBUTING.md` (with the release steps), `CODE_OF_CONDUCT.md`, the issue and PR templates, and `.github/workflows/release.yml` (UI, wheel and PyPI trusted publishing on a `v*` tag). Open: PyPI's trusted-publisher setup, and image publishing
- [x] Ship `scripts/` as package data so `codecoremorph toolchain install` works for PyPI installs (FR-1.13); `--offline DIR` installs (FR-1.12, D-51, with `codecoremorph toolchain download` to prepare them); a docs page on dependency setup, including machines without internet access (`docs/dependencies.md`)
- [ ] Register `codecoremorph` on PyPI; resolve SPEC §16 Q6, Q7 and Q8

**Deferred (D-55):** registering on PyPI, publishing, images and the v0.1.0 tag wait until the C++ → Rust phases are done; Phase 12 starts now.

### Exit criteria (M2)
- [ ] On clean Windows, macOS and Linux machines: `uv tool install codecoremorph` → `codecoremorph init` → `codecoremorph run` on a fixture succeeds with Docker. Windows checked with the built wheel (a fresh `uv tool install` on E:, `init` on the `typedefs` fixture, its manifest unchanged, then Claude Code through the skill: 3 of 3 units, 24 tests; it found D-54). From PyPI, and on macOS and Linux, after publishing and CI
- [x] The security review is complete with no open high-severity findings (D-52)
- [ ] v0.1.0 is tagged, published and documented

---

## Phase 9: Java adapter and Java version upgrades (M3)

**Goal:** the second language. Java projects can be indexed, built and tested, and upgraded across Java versions.

**Prerequisites:** Phase 8 (Phase 5 at minimum). Built after Phases 12 to 14 (D-55); the core is language-neutral, so adding Java must not touch `orchestrator/` (NFR-3.1).

### Tasks
**Strategy**
- [x] Spike on a small Java 8 Maven project with JUnit, on Windows and in Docker: one JDK per standard (the baseline on Java 8's class library, so APIs removed later break only after the bump), Maven's `target/` kept out of the worktree (`git add --all` would commit it), every build offline after a `fetch_only` dependency step (Surefire resolves its JUnit provider at test time), OpenRewrite's Maven plugin run offline with a recipe's changes limited to a unit's files, and the Spoon helper on the project's sources (D-63, on Windows: the unbumped project passed on JDK 8 and failed on JDK 21; `go-offline` missed `surefire-junit4`; `rewrite:runNoFork` ran `UpgradeToJava21` offline from a tools repository chained behind the project's, on JDK 8, in 11 s; Spoon indexed the sources at levels 8 and 25. Docker comes with the image. The spike is in `E:/codecoremorph-trials/java-spike`)
- [x] Design settled with a `D-xx` row, and SPEC §11.2 filled in: standards, toolchain, build, tests and test IDs, index, codemods, the rule pack, I-12 for Maven (D-63)

**Toolchain**
- [x] `java` rows in the manifest: a Temurin JDK for each standard, Maven, the Spoon jar (D-64: Temurin 8u504-b01, 11.0.32.1+1, 17.0.21+9, 21.0.13+9, 25.0.5+8, one release each that every platform has, and no JDK 8 for macOS on Apple silicon; Maven 3.9.16; Spoon 11.5.0)
- [x] `install-toolchain java` (`.bat` and `.sh`): the JDKs, Maven, Spoon, and a Maven repository with the plugins a build and OpenRewrite need (Maven verifies what it downloads, as rustup and cargo do for Rust); writes `java.env` (OpenRewrite's plugin 6.46.1, `rewrite-migrate-java` 3.42.1, `rewrite-static-analysis` 2.41.1 and `maven-dependency-plugin` 3.11.0 in `toolchains/java/m2`, 210 MB; `all` now installs Java too; no `--offline`, as for Rust)
- [x] `images/java/` and `scripts/build-image java`; `codecoremorph toolchain install|download java` (the image is 3 GB, five JDKs)

**Adapter**
- [x] `codecoremorph.adapters.java`: Maven builds (`mvn -B -o`) with javac and Maven diagnostics; the dependency step; Surefire XML for JUnit 4 and 5, with stable test IDs and test selection; `detect_project` and `project_standard` from the POMs; `builds`, `clean_build` (Maven from its launcher class on the standard's JDK, in a mirror of the worktree in the cache; `Executor.toolchain_env` gives the JDKs' paths; the Surefire providers by what the run's repository holds after `go-offline`)
- [x] Spoon index helper: types, members, references, imports as file dependencies, `native` methods as JNI boundaries, `project.public_api` files (`JavaIndexHelper.java`, compiled with the newest JDK's `javac` into the cache; only the files under a module's source roots, the rest listed as uncompiled)
- [x] Codemods: OpenRewrite recipes through its Maven plugin, applied to the unit's files only; the POM's Java version bump (D-64: `compile` first in the same run, for multi-module builds, and the network for the recipes' own lookups)
- [x] JUnit: `detect_disabled_tests` (`@Ignore`, `@Disabled`, assumptions), `extract_assertions` (I-4), the `test_cases` query
- [x] I-12 for Maven: new plugins that run code or download (exec, antrun and the like), build extensions, new repositories, annotation processors (and `.mvn/extensions.xml`, `jvm.config`, `maven.config`; the dependency step and OpenRewrite also refuse extensions the run's first configure didn't see, before the network goes on)

**Rules**
- [x] `java` rule pack (8 → 11, 17, 21): `bump_java_version` (project: OpenRewrite's migration recipe for the target, then the agent fixes what it leaves), `use_diamond_operator`, `use_lambdas`, `use_try_with_resources`, `use_var` (where safe), `use_switch_expressions`, `use_records` (where safe), each with OpenRewrite recipes where they exist, guidance, examples and post-conditions (D-64: `use_records` finds its classes with the adapter query `record_candidates`; an agent may submit no edits when what is left should stay)

**Fixtures and evals**
- [x] Java fixtures (Java 8, Maven, JUnit): legacy APIs, raw types, anonymous classes, `Date`/`Calendar`, APIs removed after Java 8; JUnit 4 in one and JUnit 5 in another, and a multi-module build (`fixtures/java/legacy-ledger`: `sun.misc.BASE64Encoder`, JAXB, raw types, anonymous classes, `try`/`finally`, `Calendar`, a switch statement, a value class, 23 JUnit 4 tests; `fixtures/java/task-planner`: two modules, `${java.version}`, `javax.annotation.PostConstruct`, 21 JUnit 5 tests with `@Nested` and parameterized ones)
- [x] Each fixture's 8 → 21 migration whole through `run --host-agent`, replayed from recorded edits natively and in Docker (7 of 7 units each, natively; `tests/integration/test_java_e2e.py`, `tests/cassettes/java/`)
- [ ] Characterization tests with JUnit 5 (registered in the POM)
- [ ] Live evals on Java fixtures

### Tests
- Unit: javac and Maven diagnostics, Surefire XML, the POMs' standard and its bump, JUnit scanning (cases, assertions, disabled tests), OpenRewrite's patch limited to a unit, I-12 on POMs
- Integration (`toolchain`, `docker`): a fixture's build, tests and index, and an OpenRewrite pass
- End to end: each fixture's migration replays from recorded host-agent edits, natively and in Docker
- NFR-3.1: nothing under `orchestrator/` changed

### Exit criteria (M3)
- Java fixtures migrate 8 → 21 end to end, branch green; zero changes to the orchestrator graph were needed (NFR-3.1 check)
- Met on Windows, natively and in Docker: `legacy-ledger` and `task-planner` migrated 8 → 21 whole through `codecoremorph run --host-agent` (every unit committed, every commit green, the final clean build and tests passing) and replay from the recorded edits; nothing under `orchestrator/`, `planner/` or `agent/` changed. Open: characterization tests with JUnit 5, live evals; Linux and macOS wait for CI (D-28)

---

## Phase 10: C# adapter and C# version upgrades (M4)

**Goal:** the third language. C# projects can be indexed, built and tested, and modernized to a newer language version.

**Prerequisites:** Phase 9 (Phase 5 at minimum). Like Java, adding C# must not touch `orchestrator/` (NFR-3.1).

### Tasks
**Strategy**
- [x] Spike on a small SDK-style project (a `net6.0` library at C# 7.3 and its NUnit tests) on Windows: one .NET SDK for every build (its Roslyn compiles every language version), the runtime of each test project's target framework beside it, `bin/` and `obj/` kept out of the worktree, every build offline after a `fetch_only` restore step, `dotnet format` limited to a unit's files, a Roslyn index helper with no download of its own (D-65, on Windows: SDK 10.0.403 with the 8.0.33 and 6.0.36 runtimes in one root; the tests passed on .NET 6 with `BinaryFormatter`, and retargeted to `net8.0` the build failed at `SYSLIB0011`; `netcoreapp3.1` tests ran on .NET 6 with `DOTNET_ROLL_FORWARD=Major` and not without; `dotnet format style --include FILE --diagnostics IDE0161 IDE0090` changed only that file and only those rules, given the styles in `.editorconfig`; the helper compiled in 0.6 s with the SDK's `csc` against the SDK's own Roslyn 5.9. The spike is in `E:/codecoremorph-trials/csharp-spike`)
- [x] Design settled with a `D-xx` row, and SPEC §11.3 filled in: standards, toolchain, build, tests and test IDs, index, codemods, the rule pack, I-12 for MSBuild (D-65)

**Toolchain**
- [x] `csharp` rows in the manifest: the .NET SDK and the older LTS runtimes, for every platform (D-66: SDK 10.0.403, runtimes 8.0.33 and 6.0.36, 21 rows; SHA-256 computed from the files, since Microsoft publishes SHA-512)
- [x] `install-toolchain csharp` (`.bat` and `.sh`): the SDK and runtimes in one `dotnet` root, NuGet's config with nuget.org alone, `csharp.env` (the runtimes copied into the SDK's `shared/`; `--offline` works; `all` installs C# too)
- [x] `images/csharp/` and `scripts/build-image csharp`; `codecoremorph toolchain install|download csharp` (the image is 1.29 GB)

**Adapter**
- [x] `codecoremorph.adapters.csharp`: MSBuild projects in a mirror of the worktree (`bin/` and `obj/` stay out of it), the entry point (the top solution or project, or a solution the adapter writes), `dotnet build` offline with MSBuild and compiler diagnostics, `detect_project` and `project_standard` from the project files, `builds`, `clean_build`; the dotnet environment isolated (its home, NuGet's packages and config, no build servers) (`shipping-rates` has no solution: the adapter's `codecoremorph.slnx` lists its projects)
- [x] The restore step (configure, `fetch_only`): `dotnet restore` with the network on, only when the MSBuild files changed, refused when they gain code-running constructs the run's first configure didn't see
- [x] Tests: `dotnet test --logger trx` (NUnit, xUnit, MSTest), stable test IDs, selection by `--filter`, each test project on its framework's runtime
- [x] Roslyn index helper: types, members, references, `using`s and type uses as file dependencies, `DllImport`/`LibraryImport` as P/Invoke boundaries, `project.public_api` files (`CsIndexHelper.cs`; the symbol kinds gain property, event and delegate)
- [x] Codemods: `dotnet format style` with the rule's diagnostics and styles, applied to the unit's files only; the project files' language version and target framework bump (D-66: `dotnet format` runs again while a pass changes the unit's files; the bump does `Directory.Build.props` first)
- [x] Tests' sources: `detect_disabled_tests` (`[Ignore]`, `Skip =`, `[Explicit]`, `Assert.Ignore`, `Assert.Inconclusive`), `extract_assertions` (I-4), the `test_cases` query
- [x] I-12 for MSBuild: `Exec`, `UsingTask`, `DownloadFile`, new targets and imports, other MSBuild SDKs, analyzers, package sources, response files

**Rules**
- [x] `csharp` rule pack: `bump_csharp_version` (project: the language version and target framework, then the agent fixes what breaks), `use_file_scoped_namespaces`, `use_pattern_matching`, `convert_switch_statements`, `use_using_declarations`, `use_target_typed_new`, `use_collection_expressions`, `use_record_types` (where safe), `enable_nullable`, each with `dotnet format` diagnostics where they exist, guidance, examples and post-conditions (D-66: rule IDs are global, so the records and switch rules are `use_record_types` and `convert_switch_statements`; `enable_nullable` finds no sites in test files)

**Fixtures and evals**
- [x] C# fixtures (SDK-style, old language-version style): a `net6.0` library at C# 7.3 with NUnit, and a `netstandard2.0` library, a program on it and xUnit tests on `netcoreapp3.1` (`fixtures/csharp/order-book`: `BinaryFormatter`, value classes, casts, switch statements, `using` blocks, null returns, a `DllImport`, 30 NUnit 3 tests; `fixtures/csharp/shipping-rates`: `LangVersion` in `Directory.Build.props`, no solution, 22 xUnit tests; the program targets `netcoreapp3.1` too, since a `netcoreapp3.1` test project can't reference a `net6.0` one)
- [x] Each fixture's migration whole through `run --host-agent`, replayed from recorded edits natively and in Docker (`order-book` to C# 14 in 10 of 10 units, `shipping-rates` to C# 12 in 9 of 9; `tests/integration/test_csharp_e2e.py`, `tests/cassettes/csharp/`)
- [ ] Live evals on C# fixtures

### Tests
- Unit: MSBuild and compiler diagnostics, TRX, the project files' standard and its bump, test scanning (cases, assertions, disabled tests), I-12 on MSBuild files
- Integration (`toolchain`, `docker`): a fixture's build, tests and index, and a `dotnet format` pass
- End to end: each fixture's migration replays from recorded host-agent edits, natively and in Docker
- NFR-3.1: nothing under `orchestrator/` changed

### Exit criteria (M4)
- C# fixtures migrate end to end, branch green; no orchestrator-graph changes needed
- Met on Windows, natively and in Docker: `order-book` (C# 7.3 on .NET 6 to C# 14 on .NET 10) and `shipping-rates` (C# 7.3 on .NET Standard 2.0 and .NET Core 3.1 to C# 12 on .NET 8) migrated whole through `codecoremorph run --host-agent` (every unit committed, every commit green, the final clean build and tests passing) and replay from the recorded edits; nothing under `orchestrator/`, `planner/` or `agent/` changed. Open: live evals, characterization tests for C#; Linux and macOS wait for CI (D-28)

---

## Phase 11: Cross-language migration, Java → C# (M5)

**Goal:** port a Java project to C# with translated tests proving parity.

**In the Backlog (D-67):** legacy code to modern languages comes first (Phases 15 and 16). When this phase returns, rewrite its tasks against what Phases 12–14 built; the first one left is the design question: a skeleton-first port with `pending_parity` (§11.4) or leaves first with no bridge between the two runtimes.

**Prerequisites:** Phases 9 and 10.

**Reuse (D-47, D-55):** the migration pairs, the symbol map, test translation and the mapping steward come from Phases 13 and 14, built language-neutral for C++ → Rust; this phase adds the Java and C# sides.

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
- [x] `images/rust/`: pinned toolchain, cargo-nextest, cbindgen, plus the C++ toolchain for mixed builds (D-57: the base of `images/cpp`, `install-toolchain.sh rust`, the libclang venv; `scripts/build-image rust`)
- [x] `rust` rows in the manifest and `install-toolchain rust` (pinned `rustup-init` and toolchain, cargo-nextest, cbindgen) (D-56; Rust 1.99.1, nextest 0.9.151, cbindgen 0.29.4 built by `cargo install --locked`; installs the C++ toolchain too and writes `rust.env`)
- [x] Rust adapter: `cargo build --message-format=json` diagnostics, nextest JUnit tests (its index lists the crates' files only; a symbol index, syn- or rust-analyzer-based, moves to Phase 13, whose port planning needs it)
- [x] Mixed-build integration via Corrosion; `scaffold_project` for a crate inside the CMake tree (the `rust-crate` codemod: a codemod, so it runs as the port unit's deterministic first pass, D-57)
- [x] `cpp_ffi_to_rust` strategy: choose a module with an `extern "C"` boundary, generate the crate, port its internals, keep the ABI identical (cbindgen header diff = empty), run the C++ tests through the ABI. Steps (D-56, D-57):
  - [x] Rule model: `kind: port` (one atomic unit per cluster with sites, plus the build files) and `extra_scope` (globs a unit may also create or edit: `rust/**`); planner `port_units` (one unit per header group; the headers are read-only reference files)
  - [x] Migration pairs: `target.language` may differ from the source (no standard bump; each side's standard checked against its own adapter); the session opens the target adapter on the same executor and worktree, with the target language's toolchain env (`rust.env`) and image; codemods go to the adapter that lists their tool; a failed build adds the target adapter's diagnostics
  - [x] Rust adapter (`codecoremorph.adapters.rust`): crate discovery, `cargo build --message-format=json` diagnostics, clippy, nextest JUnit tests, the `rust-crate` scaffold codemod (crate files, `Cargo.lock`, a marked Corrosion block in the top-level `CMakeLists.txt`), cbindgen header
  - [x] `cpp_to_rust` rule pack: `port_c_abi_module` (C++ adapter query `c_abi_definitions`, codemod `rust-crate`, guidance, post-condition `c_abi_kept`, `allow_suppressions` for the C names Rust keeps)
  - [x] The `c_abi_kept` post-condition: the kept header's declarations against the crate's cbindgen header, both parsed by the C++ adapter (libclang, canonical types)
  - [x] Host agent: the skill and the unit context for port units (the header, the C++ source, where the crate goes)
- [x] ABI invariant (I-6 over exported C signatures) (`c_abi_kept`, which every port rule checks; I-12 also refuses Cargo build scripts, build dependencies and procedural macros)
- [x] Corrosion pinned in the manifest and installed under `CODECOREMORPH_HOME`, so configuring never downloads it (`FetchContent`) (0.6.1, on `CMAKE_PREFIX_PATH` through `rust.env`)
- [x] Cargo dependencies: `Cargo.lock` committed, crates fetched in the `fetch_only` network step into a `CARGO_HOME` under `CODECOREMORPH_HOME`, builds run `--locked --offline` (the Rust adapter's configure; Corrosion imports `FROZEN`. No fixture has a dependency yet, so the fetch itself is untested against a registry; Phase 13's fixtures will need one)
- [x] Windows native: the Rust target that links with llvm-mingw (`x86_64-pc-windows-gnullvm`), so no Visual Studio is needed (D-32) (checked: Corrosion picks the target, and a C++ program links a Rust staticlib with llvm-mingw)
- [x] Fixture: a C++ library with an `extern "C"` API and GoogleTest tests (`fixtures/cpp/ffi-module`: a ring buffer and Adler-32 behind `textkit/ring.h`, C++ outside the module that stays C++; a hand port to Rust passed its tests unchanged on Windows)

### Tests
- Unit: Cargo JSON diagnostics, nextest JUnit parsing, the ABI invariant (cbindgen header diff)
- Integration (`toolchain`, `docker`): the fixture's mixed build, with its C++ tests passing before and after the module is replaced
- End to end: the fixture's run replays from recorded host-agent edits, natively and in Docker

### Exit criteria (M6)
- The fixture module is replaced by Rust; the original C++ tests pass unchanged; the exported ABI is identical (met on Windows, natively and in Docker: `ffi-module`'s run replays from recorded host-agent edits, `test_every_unit_of_a_fixture`, and the hand port passes through the real adapters, `tests/integration/adapters/test_rust_adapter.py`; Linux and macOS wait for CI, D-28)

---

## Phase 13: C++ → Rust, module by module (M7)

**Goal:** port every module of an old C++ project (C++98 or newer) to Rust while the project's own C++ tests keep verifying it. Modules go leaves first; each ported module keeps its C++ interface as a façade over the Rust code, so every commit builds and passes the baseline tests (SPEC §11.6, D-47).

**Prerequisites:** Phase 12 (Rust adapter, mixed builds, ABI invariant). Phase 11 comes later (D-55), so this phase builds the migration pairs, the symbol map and the mapping steward itself, language-neutral.

### Tasks
**Strategy**
- [x] SPEC §11.6 settled with a `D-xx` row: stages, bridge, manifest keys, unit kinds (D-58)
- [x] Bridge spike on `port-legacy`: the `cxx` crate against a plain C ABI with opaque handles and cbindgen headers. Pick the default and record the C++ standard it needs (D-58: the C ABI, from C++98, so no standard bump; `cxx` needs C++11, 15 crates with build scripts and a proc macro, and a `cxxbridge` pinned to the project's `cxx` version. Both ports of `strutil` and `Rational` passed the fixture's tests on Windows; the spike is in `E:/codecoremorph-trials/bridge-spike`)
- [x] Manifest: Phase 12 already ports with `migration.target: {language: rust, standard: rust2021, rules: [...]}`, `validate` checking each side against its adapter (D-57); add what this phase needs (D-58: nothing; one bridge, so no `bridge` key, and `keep_cpp_api` comes with Phase 14)
- [x] Migration pairs in config and orchestrator, language-neutral (a source adapter and a target adapter; Phase 11 adds Java → C#, D-55). Phase 12 built the pair (one executor and worktree, the target's toolchain, codemods by tool, the target's diagnostics, `kind: port`, D-57); left: the planner, units and invariants knowing which side a file is on (D-59: module units carry `WorkUnit.port`; the planner cuts by build target, the loop routes codemods and checks to the side that owns them, I-7 and I-8 know the new code's scope)
- [x] Rust symbol index, moved from Phase 12: port planning and the symbol map need the Rust side's symbols (D-58: a scanner in Python over the crates' sources, by item path) (`adapters/rust/symbols.py`, the Rust adapter's `index`)
- [x] CMake targets from CMake's file API (the C++ adapter, recorded with the index): which library target compiles each file, for the crate per target and to leave executables to Phase 14 (the codemodel query in the initial cache, D-59)

**Scaffold and planning**
- [x] `scaffold_project` for Rust inside a C++ tree: a Cargo workspace beside the CMake project, one crate per CMake library target, built from CMake through Corrosion (D-58: the `rust-module` codemod, run first in each port unit, with the string helpers, `cbindgen.toml` and the symbol-map file; the Rust adapter's configure regenerates the bridge headers)
- [x] Port planning: `port_module` units from the file graph's clusters, leaves first. Each lists the C++ files it ports, the C++ callers outside it (they keep calling through the façade) and its part of the symbol map. Header-only templates and inline functions are planned with their last C++ user (D-58: `port_units: module`, the callers as reference files, `WorkUnit.c_headers` for `c_abi_kept`; the detector `unported_definitions`)
- [x] Estimates that grow with a unit's size (the M1 run's agent time grew with lines; the planner's flat per-unit estimate doesn't model it) (D-59: 0.12 s per line per attempt, the M1 fit; a port unit twice the output and, provisionally, three times the time)

**Port units**
- [x] A unit ports one cluster: the Rust module; bridge declarations for the API that C++ outside the unit still uses (callers and tests); and the C++ façade, whose header keeps its API while its sources forward through the bridge (the context names the crate, module, bridge and callers; ports of both fixtures, D-59 (8))
- [x] `cpp_to_rust` mapping rule pack (guidance plus checks): (`port_module.yaml` and `guidance/port_module.md`: the bridge conventions and the mapping)
  - ownership: `unique_ptr` → `Box`, `shared_ptr` → `Rc`/`Arc`, non-owning pointers → references or `Option<&T>`
  - strings, containers and iterators
  - exceptions and status codes → `Result`
  - class hierarchies → traits; templates → generics; operator overloads → `std::ops` and `std::cmp` traits; RAII → `Drop`
  - integer semantics: unsigned wraparound → `wrapping_*`; signed overflow (undefined in C++) panics in a debug build
  - `static` state → `OnceLock`; threads and locks → `std::sync`; macros → `const`, functions or `macro_rules!`; namespaces → modules; C strings, `printf` and iostreams
- [x] Symbol map store (source symbol ID → target path, language-neutral) with entries (C++ USR → Rust path) for every symbol of a ported cluster, kept consistent by a mapping steward role that this phase adds (Phase 11 reuses both, D-55) (D-58: `rust/symbol-map.toml` in the project, by qualified name; the steward is the `symbol_map` post-condition) (`invariants/symbol_map.py`)
- [x] Checks: no `unsafe` outside the bridge (error); no new `cargo clippy` warnings in ported code (as I-10); the façade headers' API unchanged (I-6); the exported C ABI unchanged (Phase 12) (D-58: `unsafe_in_bridge`, `lint_clean`, I-6, `c_abi_kept` on `c_headers`)
- [x] Host-agent support: the skill and the context documents of port units show the C++ source, the Rust target, the façade and the symbol map (the skill and the unit context describe module ports; `unit start --plan-unit` carries the unit's port)
- [x] Dashboard: a two-sided view (C++ source ↔ Rust target) in unit detail, which Phase 11 reuses for Java ↔ C# (`/api/runs/{run}/units/{unit}/port`, the unit page's Port card)

**Fixtures and evals**
- [x] `port-legacy` (C++98 style, GoogleTest 1.8.1): a string utility, a value class with operators, a container template, a class hierarchy with virtual functions, exceptions, a status-code API, a small command-line program (TinyCalc, an expression calculator: `strutil`, `Rational`, the `Stack<T>` template inside the library, a lexer, the `Expr` hierarchy, which a test subclasses, a parser throwing `ParseError`, a C status-code API, and `tinycalc` with CTest end-to-end tests; 34 GoogleTest cases)
- [x] `port-modern` (C++11/14): `unique_ptr` and `shared_ptr` ownership, a callback API, `static` state, a thread with a mutex (a jobs library: ids behind a mutex, `unique_ptr` jobs in a priority queue, `std::function` handlers, a copy-on-write `shared_ptr` document, a worker thread, and `jobrun`)
- [ ] Live evals of port units on both fixtures (Claude Code with the skill; the built-in loop)

### Tests
- Unit: port planning (leaves first; templates move with their last C++ user), symbol-map consistency, the `unsafe` and clippy checks, the façade API check
- Integration (`toolchain`, `docker`): each fixture's mixed CMake + Cargo build after each recorded unit
- End to end: each fixture's port up to M7 replays from recorded host-agent edits (as in D-45), natively and in Docker
- Safety: working tree byte-identical, crash and resume, and path traversal on a port run

### Exit criteria (M7)
- Every module of `port-legacy` and `port-modern` is Rust; every commit on the branch built and passed the original C++ tests; `unsafe` appears only in the bridge
- Windows native and Docker (Linux waits for CI, D-28)
- Met on Windows, natively and in Docker: both fixtures ported whole through `codecoremorph run --host-agent` (six units each, every commit built and passed the original tests, the final verification passed), and both replay from the recorded edits (`test_every_unit_of_a_fixture`). The executables (`tinycalc`, `jobrun`) stay C++ until Phase 14 makes them `bin` targets. Open: the live evals with a fresh Claude Code

---

## Phase 14: C++ → Rust, finished (M8)

**Goal:** finish the port. The tests become Rust tests mapped one to one onto the originals, the façades and the C++ build go, and what's left is a Cargo workspace. Measured on a real old C++ project.

**Prerequisites:** Phase 13.

### Tasks
**Strategy**
- [x] Design settled with a `D-xx` row (D-60): three rules after `port_module` (`port_executable`, `port_tests`, `retire_cpp`), unit prerequisites, the test map and parity through it, I-4 across languages, one retirement unit, which side builds, `keep_source_api` (the plan's `keep_cpp_api`). A spike on the ported `port-legacy` branch showed a Rust `bin` named after the CMake target keeps the CTest entries that run it passing (Corrosion imports it as an executable of that name)

**Index and models**
- [x] The C++ adapter records CTest entries with the index (`ctest --show-only=json-v1`: name, the build target its command runs, the CMake file that adds it) and each build target's dependencies and artifacts (the file API); the query `test_cases` (a test file's GoogleTest cases); `LanguageAdapter.builds` (a top-level `CMakeLists.txt`) (D-61: `ctest` runs in the sandbox at every index; the index helper's target records grow)
- [x] Models: `PortUnits` `executable`, `tests`, `retirement`; detector type `files`; post-condition `test_map`; `WorkUnit.needs`; `PortUnit.tests`, `bin`, `dependencies`; `migration.target.keep_source_api`

**Units**
- [x] Executables as `bin` targets (`port_executable`, the `rust-bin` codemod): one unit per non-test executable, after the module units it needs; its CTest entries translated into the package's integration tests (the codemod takes the C++ target's commands out of CMake, so the program's CTest entries fail until `main.rs` does its job)
- [x] Test-translation units (`port_tests`, the `rust-tests` codemod): a C++ test file is translated once every project module it uses is Rust. GoogleTest suites become Rust tests (integration tests under `tests/` for the public API), fixtures become helper types, `ASSERT_*`/`EXPECT_*` become `assert!`/`assert_eq!` (noting where `EXPECT_*` continuing after a failure mattered). Both versions pass until retirement
- [x] Unit prerequisites: the run skips a unit whose `needs` didn't commit, with the reason
- [x] Test-ID map as parity (`gtest:Suite.Case` ↔ `nextest:<binary>::<path>`, `rust/test-map.toml`, the `test_map` post-condition): every baseline test maps to a passing Rust test, or the report says why not. Port builds run both sides' tests; I-3, I-5 and the final verification go through the map. The assertion guard (I-4) compares each translation's assertions with the original's (D-61: the IDs are nextest's, and an integration test checks the ones the adapter reads against nextest's report; I-4 counts checks, and flagged `EventsTest.AnEmptyHandlerIsRefused` in `port-modern`)
- [x] Characterization tests (Phase 6) translate with the rest (they are test files like the others; no fixture has them yet)
- [x] Retirement (`retire_cpp`, the `rust-retire` codemod): one unit, last, deleting the C++ and CMake files and each crate's bridge; then the Cargo workspace alone builds. `keep_source_api: true` keeps the public headers, a thin façade library and its C++ tests for outside C++ consumers
- [x] Final verification of a pure-Rust result: `cargo build` and `cargo nextest` from clean, `--locked --offline`
- [x] Report: the symbol map, the test map, deviations the agent recorded (behavior it couldn't keep, such as overflow or error-message text), the `unsafe` count with locations, any C++ left and why (`port.json`, written at the final verification, D-61)
- [x] Host-agent support: the skill and the unit context describe executable, test and retirement units

**Fixtures, benchmark and docs**
- [x] Both fixtures ported whole to pure Rust through `run --host-agent`, replayed from recorded edits natively and in Docker (14 units each, every unit committed; `run-all-port-{legacy,modern}-*` cassettes)
- [x] Benchmark (M8): pick and pin a real old C++ project (C++98/03 or C++11, CMake, tests, 2–10k lines, permissive license); port it whole with Claude Code; record time, cost, units committed, tests mapped and `unsafe` in `bench/results/`; set SPEC §13 targets from it (D-62: snappy 1.1.9, `bench/port-pin.json`; 12 of 12 units committed in 47 min, 58 of 62 tests mapped and the rest with notes, no `unsafe`, $6.96 API-equivalent; `bench/results/20261105-m8-snappy-windows-docker-claude-code.json`. Four earlier attempts found five defects, fixed with regression tests: a skipped unit for test helpers, characterization tests classified as code, lints costing attempts (clippy's fixes now run before the build), `c_abi_kept` and an anonymous enum's typedef, I-8 counting the C++ a port deletes)
- [x] Docs: a guide to porting C++ to Rust (modernize first with the C++ upgrade, then port) (`docs/porting-to-rust.md`)

### Tests
- Unit: test-ID mapping and the cross-language assertion comparison; retirement order (a façade goes only when no C++ calls it)
- End to end: each fixture's whole port to pure Rust replays from recorded edits, natively and in Docker

### Exit criteria (M8)
- `port-legacy` (C++98) and `port-modern` end as pure Rust: no C++ left (or only the kept API with `keep_source_api`), every original test maps to a passing Rust test, and the branch was green at every commit
- The benchmark project is ported and measured, and SPEC §13 has its targets
- The first criterion is met on Windows, natively and in Docker: both fixtures ported whole to pure Rust through `codecoremorph run --host-agent` (14 units each, every unit committed, every commit green, the final verification a clean Cargo build and nextest run), 37 and 15 tests mapped, no `unsafe` left; both replay from the recorded edits (`test_every_unit_of_a_fixture`). `keep_source_api` is covered by unit tests, not yet by a fixture run. The benchmark: snappy 1.1.9 ported whole to Rust with headless Claude Code in Docker (D-62), every unit committed and every commit green, its Cargo workspace building and testing alone, natively too; SPEC §13 has the port targets

---

## Phase 15: C → Rust (M9)

**Goal:** port an old C project (C89 or newer, built with Make, autotools or CMake) to Rust module by module, with the branch green at every commit. It ends as a Cargo workspace, or as a Rust library that keeps its C API (SPEC §11.7, D-67).

**Prerequisites:** Phase 14. Reuses the migration pairs, `kind: port`, the symbol and test maps, unit `needs`, retirement and `keep_source_api`, the Rust adapter, and the C++ adapter's libclang index and `c_abi_kept`.

### Tasks
**Strategy**
- [x] Spike on small, old C libraries built with Make: capture the build, port a module by hand, and record what the engine lacks (D-68: bzip2 1.0.8, zlib 1.2.11 and libyaml 0.2.5 captured in the `codecoremorph-rust:local` image; bzip2's `huffman.c` ported to a crate `bz2_rs`, folded into `libbz2.a`, and bzip2's own tests passed with byte-identical output. Not tried by hand: a program and a test file, whose mechanics are Phase 14's with the artifact paths from the capture. Settled: C is its own language; capture through wrapper scripts as `CC` and `AR`; the crate folded into the library's archive; `.trs`, TAP, JUnit XML or the exit status; Docker first; no c2rust. Found: CRLF `configure` scripts under `core.autocrlf`, and a shared internal header that puts the top-level file first when ordering by includes. The spike is in `E:/codecoremorph-trials/c-spike`)
- [x] Design settled with a `D-xx` row, and SPEC §11.7 filled in (D-68)

**Toolchain and adapter**
- [x] C as a language: standards `c89` … `c23`; an adapter sharing the C++ adapter's code; `detect_project` and `project_standard` for C projects (`adapters/c/`: `CAdapter` subclasses `CppAdapter`, entry point `c`; detection prefers CMake when a project has both, reads `-std=`, `-ansi` and `C_STANDARD`, else guesses from the code; no characterization-test registration, D-68. Open: `codecoremorph init`'s starter manifest for a C project is an upgrade; it proposes the port once the `c_to_rust` pack exists)
- [x] Make as a build backend shared by the C and C++ adapters (`migration.source.build_system: make`): configure (`./configure`, network off; again when its inputs change; `autoreconf -fi` first when only `configure.ac` is there, and again when a `Makefile.am`, `configure.ac` or m4 file changes, D-70), build, clean build, in a mirror of the worktree; compiler and archiver calls captured by wrapper scripts during the build and the tests, giving `compile_commands.json` (each file once: libtool compiles twice), the libraries, the programs and what they link, and the artifacts; I-12 for Makefiles, `configure`, `configure.ac`, `Makefile.am` and m4 (`adapters/mirror.py`, `adapters/cpp/{make,capture,makefiles}.py`, `helpers/capture.sh`; the compile commands name the worktree's files and search the mirror's directories after them, for configure's `config.h`; the wrappers reach configure through the environment, since zlib's refuses `CC=` arguments; a configure with no build yet builds once, so the index has commands; `build.make.test_targets`. Tests: the three spike builds' recorded calls, a scripted executor, and `c-legacy` in Docker. Open: Java and C# keep their own mirrors)
- [x] A run that builds in a Linux container checks its worktree out as on Linux, whatever the machine's `core.autocrlf` (D-68: `Git(config=LF_CHECKOUT)`, `Repository.add_worktree(line_endings=)`, the mode recorded in the worktree's git directory and read by every `Worktree`, submodule clones configured the same; the session chooses the executor before creating the worktree; native runs unchanged. Tests: `test_a_linux_checkout_keeps_the_committed_line_endings`, `test_a_reopened_worktree_keeps_its_line_endings`, `test_a_linux_container_gets_a_linux_checkout`)
- [x] The image: autoconf, automake and libtool from Ubuntu (a Makefile.am change needs them); bindgen 0.73.2 in the manifest, built from crates.io like cbindgen (both install scripts build it with `cargo install bindgen-cli --locked`; `rust.env` puts it on `PATH` and sets `LIBCLANG_PATH` to LLVM's `lib`, except on Windows, where llvm-mingw has no `libclang.dll`; the C++ and Rust images get the autotools. Both images were rebuilt from scratch on 2026-11-08 (`build-image --no-cache`, about 10 minutes, no stall), and the whole Docker suite passes on them, 100 tests)
- [x] Tests: the test target (`check`, else `test`, set in the manifest); results from automake's `.trs` (`make:<name>`, which runs that program), TAP and JUnit XML, else the exit status as one test; `detect_disabled_tests`, `extract_assertions` (I-4) and the `test_cases` query for C test files (the test target, `.trs`, TAP points as `tap:<program>/<description>` by the program make echoed last, with `# SKIP`, `# TODO`, short plans and `Bail out!`, and the target's exit status as `make:<target>` beside them; test entries name the program each test runs. D-73: JUnit XML from `build.make.junit_reports`, with a parser shared with Surefire (`adapters/junitxml.py`); a C test's checks by the usual C test libraries' assertions, a TAP point's being the call that prints it and a whole program's every call in its files, by the IDs of the last run; `#if 0`, TAP skips and to-dos and automake's status 77 as disable markers (`adapters/c/tests.py`))

**Rules and checks**
- [x] `c_to_rust` pack: module units, programs, tests and retirement (rule IDs are global, D-66), with the C mapping of §11.7 as guidance (D-69: `port_c_module`, `port_c_executable`, `port_c_tests` and `retire_c`, each with its guidance; `codecoremorph plan` on `c-legacy` gives 5 translation units, the program, 4 test files and the retirement, and the host-agent run committed all 11)
- [x] Module units are translation units, leaves first by the calls between them (the index's reference edges), cycles broken where they cost least; headers several units include stay until retirement (D-69: `port_units: translation_unit`; the call counts come from `IndexStore.reference_counts`; a unit's exports from `used_elsewhere`; in C, `c_abi_definitions` is every definition another file declares or uses. Tests: `tests/unit/planner/test_translation_units.py`, and `codecoremorph plan` on `c-legacy` in Docker, `tests/integration/test_c_plan.py`)
- [x] The crate: exports from a boundary file, the only unsafe code; a `sys` module bindgen writes at configure from the kept headers, with the ported functions blocklisted; a check that the crate defines no copy of a header's type; `c_abi_kept` on the exports (D-69: `bridge::sys`, from the build directory's compile database; cbindgen includes the kept headers; `lint_clean` reports a `#[repr(C)]` copy; `symbol_map` requires the exports. Tested by porting `c-legacy`'s `sbuf.c` by hand in Docker, `tests/integration/adapters/test_c_port.py`: all 40 TAP points and `make test` pass against the folded crate, clippy is quiet)
- [x] Scaffold codemods for Make builds: a marked block at the end of the `Makefile` (or `Makefile.am`) that builds the workspace offline and folds each crate's staticlib into its library's archive (an `ar` MRI script), with the crate's native libraries; a release profile with LTO and stripped debug info. Programs: a Rust `bin` copied to the C program's artifact path (D-69 for a Makefile or the `Makefile.in` configure writes it from: `adapters/rust/cports.py`; the archive rule starts from an empty archive; links of the library get `$(CODECOREMORPH_RUST_LIBS)`; a program's rule copies its Rust `bin`, whose prerequisite the block gives. D-70 for automake: the block at the end of the library's `Makefile.am`, the crate as one relocatable object in the library's `LIBADD`, through a libtool object for a libtool library, so it reaches both the `.so` and the `.a`; a program's `_SOURCES` emptied and its `_LINK` copying the `bin`; cargo's files get a recipe, so a rebuilt crate reaches the archive in the same make; autoreconf again when a `Makefile.am` changes. Tests: `test_cports.py`, and `strutil.c` ported by hand in Docker, `tests/integration/adapters/test_c_autotools_port.py`)
- [x] Characterization tests for a C → Rust port as Rust tests calling the C API through `sys` (D-74: C programs that print TAP over `codecoremorph_tap.h`, built and run by a marked block of the top-level Makefile, which a port translates with the project's own tests; Rust tests through `sys` would need a build script or flags outside Cargo before any crate exists, and would link a folded crate twice. `adapters/c/characterize.py`; in Docker on `c-legacy`. Open: automake and CMake C projects)
- [x] The test map: one source test may map to several target tests (a coarse `make:test`) (D-69: `rust = ["...", "..."]`; `TestPair.targets`; parity and I-3 need every target, I-4 adds up their checks; a target test still stands for one source test)
- [x] `unsafe` only in the boundary layer (`unsafe_in_bridge`), clippy clean, the symbol and test maps (every rule of the `c_to_rust` pack carries `unsafe_in_bridge` and `lint_clean`, the module rule `symbol_map`, the program, test and retirement rules `test_map`; D-69)
- [x] `keep_source_api`: the C headers as the public API of a Rust `staticlib` and `cdylib`, checked by `c_abi_kept` (D-72: the public headers, what they include, the C that still defines what they declare, the C tests of the kept API and the Make build stay, the crates folded into the library it makes rather than a new `cdylib`; `c_abi_kept` checks every crate's exports; `c-legacy-keep` replays in Docker)
- [x] Host-agent support: the skill and the unit context describe C ports (the skill's section 2; C notes for translation units, programs, tests and the retirement in the unit context)

**Fixtures, benchmark and docs**
- [x] `c-legacy` (C89, a plain Makefile): a string buffer, a hash table, a tokenizer with `goto` cleanup, a status-code API with `errno`, global configuration, a small program, TAP tests (`fixtures/c/c-legacy`: `wordfreq`, a word-frequency library and program; one internal header, `src/wfint.h`, declares every module, as bzip2's does; exported globals in `conf.c`; a `htab_each` callback with a `void *` context; four TAP programs with 40 points, and `cmp` checks of the program's output; `-ansi -pedantic -Wall` builds clean)
- [x] `c-autotools` (C99, autotools, `make check`): a struct-heavy public header, callbacks with a `void *` context, a union, a variadic logging function (`fixtures/c/c-autotools`: `kvconf` 0.3.1, a key = value configuration library and `kvdump`; automake with recursive `SUBDIRS`, a libtool library of five files with one internal header, `kv_value`'s tagged union, `kv_each`, `kv_scan` and the log sink with `void *` contexts, `kv_log`/`kv_vlog`; four test programs under automake's harness, 162 checks, and `dump.sh` comparing `kvdump`'s output and messages with files; `-std=c99 -Wall` builds clean)
- [x] Each fixture ported whole through `run --host-agent` and replayed from recorded edits (D-69 for `c-legacy`: run 20261106-125410-f568 in `E:/codecoremorph-trials/c-runs/legacy`, 11 of 11 units on their first attempt in 9 minutes, the final verification a clean Cargo build with 39 tests. The run found that a C `static` and a program's `main` must be on no boundary (I-6), and that a reused baseline left the plan without test units. D-70 for `c-autotools`: run 20261106-151553-2811 in `E:/codecoremorph-trials/c-runs/autotools`, 11 of 11 units on their first attempt in 12.5 minutes, the final verification a clean Cargo build with 27 tests; `kv_log` and `kv_vlog` stayed C until the retirement. Its first attempts found that a rebuilt crate reached a Make archive only at the next make, and that two C files defining `struct sink` were tied as one unit. `tests/cassettes/c/edits/<fixture>.json` and `run-all-<fixture>-docker.json`, replayed by `tests/integration/test_c_e2e.py`)
- [x] Benchmark (M9): a real old C library (C89 or C99, Make or autotools, tests, 2–10k lines, permissive license), pinned, ported whole with Claude Code and measured; SPEC §13 targets set from it (D-71: bzip2 1.0.8, `bench/c-port-pin.json`; 10 of 10 units committed in 87 min, migrating 1.10 of the estimate, `make check` mapped to six of the 38 Rust tests, no `unsafe`, $22.39 API-equivalent; `bench/results/20261106-m9-bzip2-windows-docker-claude-code.json`. The Rust `bzip2` writes byte-identical output to the C build's. Two earlier attempts and replays of their recorded edits found six defects, fixed with regression tests: I-6 on a program's definitions and on a header no C file includes any more, a new failing test committed, the skill's `-c` sentence, the harness without Edit, a retried unit's summary and notes. Left: the symbol map after the retirement, other platforms' build files)
- [x] Docs: a guide to porting C to Rust (`docs/porting-c-to-rust.md`, automake and what stays C included; its Limits name what is open)

### Tests
- Unit: compile and link capture (the spike's three projects as recorded logs), Make and autotools diagnostics, the test formats, translation-unit ordering on a shared internal header, the no-copied-types check, I-12 on Make and autotools files
- Integration (`docker`, and `toolchain` if native): a fixture's build, tests and index
- End to end: each fixture's port replays from recorded host-agent edits
- Safety: working tree byte-identical, crash and resume, and path traversal on a C port run; NFR-3.1, nothing under `orchestrator/` changed

### Exit criteria (M9)
- `c-legacy` and `c-autotools` end as pure Rust (or a Rust library with its C API kept): every original test maps to a passing Rust test, the branch was green at every commit, and `unsafe` appears only at the C boundary
- In Docker; native Windows for Make projects later (D-68)
- The benchmark project is ported and measured, and SPEC §13 has its targets
- Met in Docker on Windows 11 (2026-11-08): `c-legacy` and `c-autotools` ported whole to pure Rust (11 of 11 units each, every commit green, `unsafe` only in the bridges) and `c-legacy-keep` to a Rust library behind the kept C API (D-72), all three replayed from recorded edits; bzip2 1.0.8 ported and measured (D-71). Safety: the C e2e compares the user's checkout byte for byte; crash and resume and path traversal are language-neutral (`test_crash.py`, the `PatchEngine` fuzz), and the Make mirror's handling of a reverted file and of a sync cut short is pinned (`test_mirror.py`); nothing under `orchestrator/` knows C. Open: native Windows for Make projects (D-68), characterization for automake and CMake C projects, live evals with a fresh Claude Code

---

## Phase 16: Rust → C (M10)

**Goal:** port a Rust crate to C (C99 or C11), the mirror image of Phases 13 and 14. Modules go leaves first behind Rust façades, so the crate's own tests verify every unit; then the tests are translated to C and the Rust retired, leaving a CMake project (SPEC §11.8, D-67).

**Prerequisites:** Phase 15 (C as a language, its builds and its tests).

### Tasks
**Strategy**
- [x] Spike on a small crate: one module ported to C behind its Rust façade by hand, with bindgen and a build script linking the C. Settle how Cargo builds the C during the port (the `cc` crate, or CMake first and a build script that links), the Rust source index, the C test library for translated tests, and what the first version refuses (async, procedural macros, heavy generic code) (D-75: `stats`, a module of slices and an owned histogram, ported to C99 in `E:/codecoremorph-trials/rust-spike/rust-to-c`; CMake builds the C first and a build script that runs nothing links it, relinking when the archive changes; the crate's unit and integration tests pass through the façade, clippy is quiet, and the tests run the C under ASan and UBSan, catching a planted heap overflow. A second part tried the rest: the `stats` tests translated to a program over D-74's TAP helper, which gains a case filter, each case a CTest test named as its Rust test, clang-tidy quiet, a planted use-after-free failing its case; and rust-analyzer's SCIP output on two small crates, which gives items, references and trait implementations, but no type arguments at generic call sites)
- [x] Design settled with a `D-xx` row, and SPEC §11.8 filled in (D-75, built on 2026-11-08 while the user was away and asked for the remaining phases; **for the user's review**: the build at the top level from the first unit, the constant build script I-12 accepts, the `port_target`/`port_source` roles, the refusals)

**Index and adapter**
- [x] Rust as a source: an index of modules, items, `use` dependencies, trait implementations and the generic instantiations in use (D-75: `rust-analyzer scip` read by `adapters/rust/scip.py`, no protobuf dependency; references onto the item scanner's paths, targets, tests by file, `Cargo.toml`/`Cargo.lock` as build files, public items as `public_api` for I-6 (`adapters/rust/source.py`); instantiations aren't in SCIP, so a unit's note lists a generic item's call sites; rust-analyzer and `rust-src`, which it needs for the standard library's macros, are rustup components in both install scripts; `LanguageConfig.port_target` tells the source adapter it is one. Tests: `test_scip.py`, `test_source.py` on the spike's recorded indexes)
- [x] The C side as the target: a CMake project scaffold (library and tests), with its diagnostics and tests through Phase 15's C support (D-75: `adapters/c/rustports.py`, the `c-module`, `c-program`, `c-tests` and `c-retire` codemods of the C adapter; a top-level `CMakeLists.txt` with `CODECOREMORPH_SANITIZE`, `c/CMakeLists.txt` with a marked source list, each translated test file's cases CTest tests read from its `CCM_RUN` lines; the C adapter configures it with the sanitizers on and names the Rust image (`port_source`); the Rust adapter builds the library before `cargo` and links it with the sanitizers' runtime; Docker test `tests/integration/adapters/test_rust_to_c_port.py`)
- [x] bindgen in the manifest, for the façades' declarations (Phase 15's bindgen 0.73.2: the Rust adapter writes each façade's `src/<module>/sys.rs` from its header at configure)

**Rules and checks**
- [x] `rust_to_c` pack: module units (leaves first), binaries, tests and retirement, with the mapping of §11.8 as guidance (`port_rust_module` with `port_units: rust_module`, `port_rust_program`, `port_rust_tests`, `retire_rust`; the planner's Rust drafts take one library crate and refuse a procedural macro crate, `async` code and unused public generic items: `tests/unit/planner/test_rust_units.py`)
- [x] Post-conditions on every unit: the C builds without warnings (`-Wall -Wextra`), passes clang-tidy's bug-finding checks, and runs every test under AddressSanitizer and UndefinedBehaviorSanitizer; the façade's public Rust API is unchanged (as I-6); `unsafe` only in the façades (`lint_clean` on the C target: the warnings and clang-tidy's `bugprone-*`/`clang-analyzer-*`, and the sanitizer option still there; the sanitizers on in every build; I-6 on the source's public items, by name: the Rust index has no signatures. Open: `unsafe` only in the façades isn't checked, since the Rust is retired)
- [x] The symbol and test maps in the other direction (Rust path → C name, `nextest:` → the C test IDs) (`c/symbol-map.toml`, `c/test-map.toml`; a CTest name is its Rust test's ID without `nextest:`)
- [x] Host-agent support (the skill's section 2, and Rust → C notes for each unit kind in the unit context)

**Fixtures, benchmark and docs**
- [x] A Rust fixture with generics, traits, enums with data, `Result` errors, iterators, closures and a binary; ported whole and replayed from recorded edits (`fixtures/rust/rpn`, edition 2018, 12 tests; run 20261107-233556-ada5 in `E:/codecoremorph-trials/rust-runs/rpn`: 11 of 11 units, ten on their first attempt, the retirement by its codemod, 7.8 min, the final clean CMake build passing the 12 translated tests under the sanitizers; `tests/cassettes/rust/edits/rpn.json`, `tests/integration/test_rust_e2e.py`. The run found the retirement missing `Cargo.lock` from its files, and that a program façade must name the crate to link the C)
- [x] A round trip as an eval: `c-legacy` ported to Rust (Phase 15), then that Rust ported back to C, with the original C tests still passing (D-76: `tests/integration/test_round_trip.py` replays Phase 15's port, then the host run's edits on its branch, the `rust/` workspace of the library's crate and the program's, which the planner, the index, the scaffolds and the retirement now take; run 20261108-162549-1e92 in `E:/codecoremorph-trials/rust-runs/roundtrip`: 12 of 12 units, eleven on their first attempt and the retirement by its codemod, 18.7 min. 37 of c-legacy's 41 C tests had Rust tests, and each reaches C tests through both maps that pass in the final clean CMake build, `make:test` among them; the other 4 checked a NULL pointer or freeing. The run found that Phase 15's `#![forbid(unsafe_code)]` crate root stopped every façade, so a root that only declares the modules is in each module's unit, and that the module scaffold then took the root for the module)
- [x] Benchmark (M10): a small real crate, ported and measured (D-76: shlex 1.3.0, `bench/rust-port-pin.json`, with a generated `Cargo.lock` committed on top of the pin by `bench/fetch.py`; headless Claude Code ported it whole, run 20261108-164849-e827: 5 of 5 units, each agent unit on its first attempt, 32 min, $8.20 API-equivalent, the 10 translated tests passing under the sanitizers, and the C byte for byte with the original crate over 30,000 seeded inputs. Planning it found that an unused iterator-generic function refused its module and that the index and the retirement reached into the excluded `fuzz/`; fixed before the run. Open: the plan's time estimate, 2.7 times low)
- [x] Docs: a guide to porting Rust to C, saying what is lost (the borrow checker) and what replaces it (sanitizers, clang-tidy) (`docs/porting-rust-to-c.md`)

### Exit criteria (M10)
- The fixture ends as a C project built by CMake: every original Rust test maps to a passing C test under AddressSanitizer and UndefinedBehaviorSanitizer, and the branch was green at every commit
- Met in Docker on Windows 11 (2026-11-08) by `rpn`: all 12 Rust tests map to C tests that pass under both sanitizers, every unit's commit was verified green, and the run replays from recorded edits. Also met by the round trip's second leg (39 tests) and by the M10 benchmark, shlex (10 tests). The phase's work is done; D-75 and D-76 await the user's review

---

## Phase 17: C# → C++ (M11)

**Goal:** port a C# library and its programs (C# 7.3 or newer, MSBuild, NUnit, xUnit or MSTest) to C++ (C++17, 20 or 23, CMake, GoogleTest), type by type, with the branch green at every commit. Each ported type keeps its C# API as a **façade** that calls the C++ through P/Invoke, so the project's own C# tests verify every unit. Then the programs and tests are translated and the C# retired, leaving a CMake project, or the C# API stays as a .NET library over the C++ (`keep_source_api`) (SPEC §11.9, D-77).

**Prerequisites:** Phase 10 (the C# adapter: MSBuild in a mirror, `dotnet test` with TRX, the Roslyn index), Phases 13 and 14 (migration pairs, `kind: port`, the symbol and test maps, unit `needs`, retirement, `keep_source_api`), Phase 16 (a port whose source keeps façades and whose target builds first: CMake before the source's build, the sanitizers from the first unit, `LanguageConfig.port_source` and `port_target`, D-75).

**Build the C# side once (D-77).** Phase 18 ports C# to Rust with the same C# side. So everything about the source is independent of the target: the index for ports, the unit cut, the façade and bridge contract, the generator of the C# declarations, calls back into C#, the exception mapping, the source side of test translation, and the retirement of the C#. Keep what is C++ in the C++ adapter and the `csharp_to_cpp` pack, as Phases 13 and 14 built what Phase 11 would reuse (D-55).

**Why a façade, not a parallel port:** with a façade the original C# tests run the new C++ at every commit, which is the project's core promise (the toolchains and the project's own tests decide). A parallel port (the C++ grown beside untouched C#, judged only by translated tests) is the fallback if the spike shows the bridge can't carry the fixture; it needs a `D-xx` row and SPEC §4.5's invariant restated.

### Tasks
**Strategy**
- [x] Spike in Docker on a small C# library written for it, in `E:/codecoremorph-trials/csharp-spike/` (done 2026-11-09, `PORT-SPIKE.md`: five units, the 22 C# tests green through the façades under both sanitizers after each; D-78): a `decimal` value type with equality, a class that owns a `List<T>` and a `Dictionary<K,V>`, an interface with a C# implementation (a test double) passed into ported code, an exception the tests expect with `Assert.Throws`, a lambda callback. Port it to C++ by hand, one type at a time, each behind its C# façade, the C# tests passing at every step. Settle each point below and record what the engine lacks:
  1. **The bridge.** `extern "C"` exports from a shared library `<project>_native` (CMake `SHARED`, default visibility hidden but for the exports) over the C++ library. Who writes the C# declarations: CodeCoreMorph from the bridge header through the C++ adapter's libclang helper (proposed: the bridge allows few C types, and nothing is downloaded), or ClangSharp's P/Invoke generator (a NuGet tool, a manifest row). The declarations may use only what C# 7.3 and `netstandard2.0` allow: `DllImport`, no `LibraryImport`, no function pointers.
  2. **Who builds what.** CMake builds the native library before `dotnet build`, into the adapter's cache, never the worktree (as D-75: the source adapter, given `port_target`, builds the target's library first). How `dotnet test` finds the library: proposed, the loader's search path in the test command's environment (`LD_LIBRARY_PATH` in Linux containers), so no project file changes and I-12 has nothing new to accept. A change to the C++ must reach the next test run.
  3. **Objects across the bridge.** A handle is a `std::shared_ptr<T>` behind an opaque pointer, released by a `SafeHandle` subclass. Identity: an object that comes back from C++ must be the same C# façade where tests use `Assert.AreSame` or reference equality (a weak table from the C++ address to the façade, or another way). Value types cross by value; collections by copy or as handles.
  4. **Calls into C#.** A delegate passed to ported code; an interface implemented in C# passed to ported code (a C++ class implementing the abstract class over a table of function pointers and a `GCHandle`); an event raised by ported code with C# subscribers. On C# 7.3 a callback is `Marshal.GetFunctionPointerForDelegate`, with the delegate kept alive.
  5. **Exceptions.** Every export catches, returns a status and a message, and the façade throws the .NET exception the C# threw (`ArgumentException`, `InvalidOperationException`, `KeyNotFoundException`, the project's own types), so `Assert.Throws` keeps passing.
  6. **Values with no direct C++ equivalent.**
     - `decimal`: .NET keeps the scale (`1.10m + 1.0m` is `2.10m`, and prints so). Proposed: a header CodeCoreMorph ships (like `codecoremorph_tap.h`, D-74) implementing .NET's 96-bit decimal for what ports use, tested against .NET.
     - `string`: UTF-16 in C#; proposed, UTF-8 `std::string` in C++, converted at the bridge, with code that depends on UTF-16 positions noted in the symbol map.
     - `DateTime` and `TimeSpan`.
     - Culture-sensitive formatting and parsing.
     - `Dictionary` iteration order.
     - Arithmetic that C# defines and C++ doesn't: unchecked overflow wraps in C# and is undefined for signed C++ integers; division by zero, a bad index and a null dereference throw in C#.
  7. **Sanitizers inside the .NET test host.** ASan and UBSan in a library that `dotnet test` loads: the shared ASan runtime preloaded, and CoreCLR's own signal handling and allocations (`handle_segv`, `allow_user_segv_handler`, leak detection off). If that doesn't hold, the fallback is the sanitizers on the C++ tests only, with `_GLIBCXX_ASSERTIONS` in the library the C# tests load.
  8. **The image.** One image with the .NET SDK and runtimes and the C++ toolchain, and Rust for Phase 18. Proposed: a new image built by `install-toolchain.sh rust` (which installs C++ too) and `install-toolchain.sh csharp`, so the C#, C++ and Rust images stay as they are. The C++ adapter as the target of a C# port names it (`port_source`, as the C target of a Rust port names the Rust image, D-75).
  9. **The unit cut.** Proposed: one unit per type, leaves first by the index's references. A reference cycle is one unit. A class with virtual members is one unit with the project's classes that derive from it, since the C++ can't dispatch a virtual call to a C# override. An interface is a unit of its own, and C# implementations reach the C++ through point 4. Partial classes are one type. Nested types go with their outer type.
  10. **What the first version refuses.** Proposed: `async` code, `dynamic`, reflection and attribute-driven behavior (serializers, dependency injection), `unsafe` C#, expression trees, and public generic types or methods that nothing in the project instantiates (no instantiation to port, as D-75 for Rust). Platform P/Invoke a project already has stays in the façade.
- [x] Design settled with a `D-xx` row, and SPEC §11.9 filled in (D-78, for the user's review)

**Index and adapters**
- [x] C# as a port source (2026-11-09; `TypeEntry`, `GenericUse` and `TestMethod` in the index, `PortSource` in `CsIndexHelper.cs`; every exact case ID matched the TRX of NUnit 3.14, xUnit 2.4.2, MSTest 2.2.10 and 3.1.1, natively and in Docker; the helper now lets the framework's assemblies win over a `netstandard1.x` package's, without which xUnit 2's and MSTest 2's attributes didn't bind): the Roslyn helper also records what the planner and a unit's context need, when `LanguageConfig.port_target` is set:
  - type kinds and hierarchies (bases, interfaces, overrides, virtual and abstract members);
  - members with their signatures (public fields, properties, events, indexers, operators, constructors);
  - generic instantiations at their use sites (Roslyn's semantic model has them, unlike SCIP, D-75);
  - what the first version refuses (`async`, `dynamic`, reflection calls, `unsafe`, expression trees);
  - static mutable state and the exception types each member throws;
  - test files and their cases (NUnit, xUnit and MSTest attributes, parameterized cases with their TRX names).

  Tests: the helper on small projects, its records checked as the Rust source index's are (`test_source.py`).
- [x] The C++ side as the target (2026-11-09: the scaffolds `cpp-csharp-type`, `cpp-csharp-program`, `cpp-csharp-tests` and `cpp-csharp-retire`, the CMake files, the runtime and the sanitizers in `adapters/cpp/csharpports.py`; GoogleTest through CTest, in Docker): a top-level `CMakeLists.txt` and `cpp/`. It holds the ported C++ as a library, the shared bridge library over it, and the GoogleTest tests (GoogleTest from the toolchain, `find_package(GTest)`). It is configured with the sanitizers from the first unit (`CODECOREMORPH_SANITIZE`, D-75), with diagnostics, GoogleTest and CTest through the C++ adapter. The scaffold codemods are the C++ adapter's, as `c-module` and its kin are the C adapter's (`adapters/c/rustports.py`). Before the first unit there is nothing to configure.
- [x] The bridge declarations (2026-11-09: the generator, the C++ index helper's `--bridge-declarations` and `adapters/cpp/pinvoke.py`, run at configure; the casts between pointer types and the owning raw pointers outside the bridge are `lint_clean`'s, D-80): the agent writes each unit's exports in the unit's bridge files (`cpp/bridge/<type>.cpp` and a C header beside them). At configure, CodeCoreMorph writes the C# declarations from the bridge headers into one generated, internal file of the façade's project. It is committed, so the branch builds without CodeCoreMorph. The bridge is the only C++ with raw handles and casts between pointer types (a check in the spirit of `unsafe_in_bridge`).
- [x] The build order in a port (2026-11-09; `test_csharp_to_cpp_port.py` passes in Docker): the target's configure and build (CMake) run before the source's build and tests (`dotnet`), and the C# tests load the library just built. Tested in Docker with one type ported by hand (`tests/integration/adapters/test_csharp_to_cpp_port.py`): the C# tests pass through the façade under the sanitizers, and an overflow planted in the C++ fails a C# test with the sanitizer's report.
- [x] The image of the spike's point 8, with `scripts/build-image` and the image tests (`images/csharp-port`, built locally 2026-11-09; the LLVM install keeps `llvm-symbolizer`)

**Rules and checks**
- [x] The `csharp_to_cpp` pack (2026-11-09, D-79; the type rule plans the port index's test project from its real index through the C# adapter's `csharp_types` query; the program, tests and retirement rules have their guidance, and plan skipped units until their cuts and scaffolds below land; the type rule's `symbol_map` and `lint_clean` come with the post-conditions below): `port_csharp_type_to_cpp` (`port_units: csharp_type`), `port_csharp_program_to_cpp`, `port_csharp_tests_to_cpp` and `retire_csharp_to_cpp`, each with its guidance. Rule IDs are global (D-66), and Phase 18's pack has the same four for Rust. The guidance holds the mapping of SPEC §11.9.
- [x] The planner's C# drafts (2026-11-09, D-79; `planner/csharp.py`, target-independent, with `port_units: csharp_type`; the C# index records each project as a library, program or test project, and the cut of the port index's test project from its real index is checked in `test_csharp_port_index.py`, natively and in Docker): types leaves first by the spike's cut, each unit's note listing its public members, its generic instantiations, the C# that calls it (it keeps calling the façade), and the C# implementations that reach it through callbacks. It refuses what the first version doesn't take, with the reason. Tests: `tests/unit/planner/test_csharp_units.py` (cycles, hierarchies, interfaces with test doubles, generics, refusals).
- [x] The façade (2026-11-10, D-81: a port's index puts the library's members on its API boundary, so I-6 compares each signature, as other assemblies see it; public fields settled by D-78's refusals; checked in Docker on the hand-ported Money, a widened parameter failing): every public member keeps its signature, docs and attributes, and its body calls the bridge. I-6 checks it by signature, since the Roslyn index has signatures (Rust's had names only, D-75). Public fields are the open case: a façade can't forward a field, and a property in its place changes the symbol's kind (the spike decides, `D-xx`).
- [x] What the source depends on, and what the first version now ports (2026-11-10, D-82, the user's decisions): the plan lists each native library the P/Invokes call, each operating system's own library and each .NET package the types use, with what the port does and what has to happen first (a native library the toolchain image lacks, looked for in Docker); types that only declare native functions get no unit, and a unit that calls one links it (`find_library` in `cpp/CMakeLists.txt`); a .NET package or an OS's own API refuses what uses it; `unsafe` code and a serializer's round trip in memory (a deep copy) port, with notes; arrays out of the bridge; shared projects and checked arithmetic; `sandbox.image` takes a local image's ID. Tested on the planner, the index rows, the scaffold and the port index (native and Docker)
- [x] Post-conditions on the C++, every unit (2026-11-09, D-80; `lint_clean` on the C++ adapter, checked in Docker on the hand port, clean, and on planted defects; the fixes to D-75's lint it found, with the Rust → C tests and replays passing):
  - `-Wall -Wextra` with no warning, and clang-tidy's bug-finding checks (`lint_clean` on a C++ target, as for C, D-75);
  - the sanitizers on in every build;
  - no owning raw pointer outside the bridge (the C++ pack's raw-ownership detector over the unit's new C++);
  - the symbol map `cpp/symbol-map.toml` (the C# symbol ID, `csharp:Shop.Cart.Add(Shop.Item)`, to the C++ name);
  - I-8 counts the façade lines, not the new C++.
- [x] Programs (2026-11-09, D-79; the planner's cut, the scaffold `cpp-csharp-program` and a hand-ported program in Docker, `test_csharp_to_cpp_port.py`): a console project becomes a CMake executable of the same name. Until its tests are translated, the C# program is a façade that calls `<project>_<program>_main(argc, argv)` (as D-75's programs).
- [x] Tests (2026-11-09, D-79; the planner's cut over the port index's real index, the scaffold `cpp-csharp-tests`, and a hand translation run by CTest in Docker; I-4 compares a parameterized method's cases one by one, D-81 (2)): each C# test file becomes a GoogleTest file, each case a test. A parameterized method's cases become `TEST_P` instances or one test each, so every `dotnet:` ID (`dotnet:Shop.Tests.CartTests#Totals(3,"x")`) maps to `gtest:` IDs in `cpp/test-map.toml`. I-3 and I-4 apply across the two languages, and both suites run while the C# build lasts (D-60).
- [x] The retirement (2026-11-09, D-79; the scaffold `cpp-csharp-retire`, the solutions edited with `keep_source_api`; in Docker, CMake builds and tests the C++ alone after it; the final verification of a whole run comes with the fixture) deletes the C# projects, the solution, the MSBuild files only the C# needed, the generated declarations and the bridge. The C++ library stops being a shared bridge. The final verification is a clean CMake build and every translated test under the sanitizers. With `keep_source_api`, the C# public API stays as a .NET library over the native one, for .NET consumers: the façade projects, the bridge and the C# tests of the kept API stay, and both builds run in the final verification.
- [x] Host-agent support: the skill's section on C# ports, and C# notes for each unit kind in the unit context (`agent/context.py`) (2026-11-09, D-79 (7): the members and cases to map by ID, the façades and what stays; the skill's port section and its manifest reference)
- [x] The planner's estimates for ports from C# (2026-11-10, D-83, from the M11 run): the agent's time held (C# type units 34.9 min against 35.8 estimated, test files 19.3 against 37.4), and what the estimate missed was the tools. A unit's `lint_clean` ran clang-tidy over all of the port's C++ twice per unit, about 200 of the run's 346 migrating minutes: `LanguageAdapter.lint` now takes the unit's files, and the C++ and C adapters lint what covers them. A port's verification also builds and tests the target, which the source's baseline doesn't time: 20 s more per verification (`PORT_TARGET_VERIFY_S`, from M8 to M11). `bench/work.py` counts C# and Java lines

**Fixtures, benchmark and docs**
- [x] A C# fixture written for ports (2026-11-09, `fixtures/csharp/parking`, D-79 (8): 17 units ported whole in one host-agent run, replayed in Docker; 58 of its 60 tests map to GoogleTest tests, the two that pass a null string to none, with notes; and with `keep_source_api`, a second run ending as a .NET library over the C++, its 60 C# tests passing), `fixtures/csharp/<name>` (C# 7.3 to 10, on .NET 6 or `netstandard2.0`, NUnit), deliberately plain old C#:
  - value types with equality, and `decimal` money;
  - a class that owns a `List<T>` and a `Dictionary<K,V>`;
  - an interface with two implementations, and a test double in the tests;
  - an abstract base class with overrides;
  - a generic type used with two type arguments;
  - exceptions the tests expect;
  - properties and an indexer, a lambda callback and an event;
  - LINQ, `IDisposable`, and a static class of constants;
  - a console program;
  - parameterized tests.

  Ported whole through `run --host-agent` and replayed from recorded edits (`tests/cassettes/csharp/edits/<fixture>-cpp.json`, `tests/integration/test_csharp_to_cpp_e2e.py`).
- [x] `order-book` ported whole as a second, older input (2026-11-10, D-82, D-83 (5); Phase 10's fixture, unchanged: fixtures are never modernized): the user counted `Book`'s `BinaryFormatter` round trip as a deep copy, which ports; `Platform`'s Windows `DllImport`, which nothing calls, gets no unit and goes with the retirement. Headless Claude Code ported it in 11 of 11 units (run 20261110-150355-c041, 19 min); 29 of its 30 C# tests map to passing GoogleTest tests, the one that passes a null currency with a note. It replays in Docker from `tests/cassettes/csharp/edits/order-book-cpp.json` (`tests/integration/test_csharp_to_cpp_e2e.py`)
- [x] Benchmark (M11): a real C# library, pinned in `bench/csharp-port-pin.json`, ported whole with headless Claude Code and measured (2026-11-10, D-82, D-83). The user asked for a library with a native dependency, so the plan shows what ports and what doesn't: libsodium-core 1.3.1 (8.8k lines of C# 9 over 73 P/Invokes into libsodium, 230 NUnit tests passing), with the port image plus Ubuntu's `libsodium-dev` (`bench/images/libsodium/Dockerfile`), which its plan said the run needs. Run 20261110-084755-9539: 68 of 68 units, each agent unit on its first attempt, 257 GoogleTest tests passing under the sanitizers, 221 of the 230 C# tests mapped and 9 with notes, 5 h 49 min, $16.58 API-equivalent (`bench/results/20261110-m11-libsodium-core-windows-docker-claude-code.json`). Over 24,292 seeded calls the façades over the C++ return or throw what the C# did, except two where Ubuntu's libsodium build rejects keys NuGet's accepts. SPEC §12 and §13 have its results and targets. Phase 18 ports the same library, so the two targets compare
- [x] Docs (2026-11-10, with the fixture and its Limits): `docs/porting-csharp-to-cpp.md`, saying what is lost (the garbage collector, bounds and overflow checks, reflection) and what replaces it (ownership by smart pointers, sanitizers, clang-tidy), with its Limits

### Tests
- Unit: the Roslyn helper's new records; the unit cut (cycles, hierarchies, interfaces implemented in the tests, refusals); the declaration generator on a bridge header (every C type the bridge allows); the scaffolds and the retirement's file list; I-12 on the new CMake files; `dotnet:` to `gtest:` IDs, parameterized cases included
- Integration (`docker`): one type ported by hand, its C# tests passing through the façade under the sanitizers, a planted defect failing a C# test
- End to end: each fixture's port replays from recorded host-agent edits
- Safety: working tree byte-identical, crash and resume, and path traversal on a C# port run; nothing under `orchestrator/` changed (NFR-3.1)

### Exit criteria (M11)
- The port fixture and `order-book` end as C++ projects built by CMake: every original C# test maps to a passing GoogleTest test under AddressSanitizer and UndefinedBehaviorSanitizer, or to none with the note that says why C++ can't have it (a null string), and the branch was green at every commit, the C# tests passing through the façades until the retirement
- With `keep_source_api`, the port fixture ends as a .NET library over the C++, its C# tests of the public API passing
- The benchmark is ported and measured, and SPEC §13 has its targets
- In Docker; native Windows later
- Met in Docker on Windows 11 (2026-11-10): `parking` ported whole to C++ (17 of 17 units, every commit green, the C# tests passing through the façades until the retirement; 58 of its 60 C# tests map to GoogleTest tests passing under both sanitizers, the two that pass a null string to none, with notes), and with `keep_source_api` to a .NET library over the C++ whose 60 C# tests pass; `order-book` ported whole (11 of 11 units, 29 of its 30 C# tests mapped, the one that passes a null currency with a note). All three replay from recorded edits (`test_csharp_to_cpp_e2e.py`). The benchmark, libsodium-core 1.3.1, ported whole and measured (68 of 68 units, D-83), and SPEC §13 has its targets. Safety: the e2e compares the user's checkout byte for byte; crash and resume and path traversal are language-neutral (`test_crash.py`, the `PatchEngine` fuzz); the scaffolds' files pass I-12 at every stage (`test_the_scaffolds_add_no_build_time_command`); nothing under `orchestrator/` changed in the phase (NFR-3.1). Open: native Windows; a second M11 run to measure D-83's lint and estimate (the user's call); `async` and `dynamic` stay refused (D-82). D-78 to D-83 await the user's review

---

## Phase 18: C# → Rust (M12)

**Goal:** Phase 17's port with Rust as the target. Each ported type keeps its C# API as a façade over a Rust `cdylib` through P/Invoke, so the C# tests verify every unit. Then the programs and tests become Rust and the C# is retired, leaving a Cargo workspace, or the C# API stays over the Rust (`keep_source_api`): a .NET library whose implementation is Rust, the usual reason to move part of a .NET codebase (SPEC §11.10, D-77).

**Prerequisites:** Phase 17 (the C# side of a port), Phases 12–14 (the Rust target: the `rust/` workspace, cbindgen, `unsafe_in_bridge`, clippy with its fixes, nextest, the retirement to a Cargo workspace).

**Reuse:** Phase 17's C# index and unit cut, the façades, the declaration generator (fed by cbindgen's header), the callbacks into C#, the exception mapping, the source side of test translation, the retirement of the C#, the image, the port fixture, `order-book` and the benchmark. What is new is Rust's side, and the redesign of a garbage-collected object graph into owned Rust.

### Tasks
**Strategy**
- [ ] Spike: the Phase 17 spike's library ported to Rust by hand behind the same façades, in `E:/codecoremorph-trials/csharp-spike/`. Settle:
  1. **The bridge.** `#[no_mangle] pub extern "C"` exports in each crate's `bridge` module (Phase 13's layout), the only unsafe code; cbindgen's header is the declaration generator's input. Every export catches panics (`std::panic::catch_unwind`) and returns a status, since a panic can't unwind into .NET. Handles are `Arc<T>` behind an opaque pointer, released by the façade's `SafeHandle`.
  2. **Ownership.** How C#'s shared, mutable objects become Rust. Proposed order for the guidance: values and owned trees; a graph with back references as indices into a `Vec`; `Rc<RefCell<T>>`, or `Arc<Mutex<T>>` where the C# shares across threads, only where sharing is the design. The report counts each unit's shared cells (not gated).
  3. **Mapping.** Interfaces and class hierarchies → traits (`dyn Trait`), or enums where the set of types is closed; C# implementations of a ported interface reach Rust through a proxy that implements the trait over function pointers and a `GCHandle`. Exceptions → `Result` inside, a status at the boundary, the .NET type rethrown by the façade. `null` → `Option`; LINQ → iterators; delegates and events → closures; properties → methods; `IDisposable` → `Drop`; strings → `String`.
  4. **`decimal`.** `rust_decimal`, whose semantics follow .NET's, added by the scaffold and pinned in `Cargo.lock`. Phase 12's guidance asks ports for no new dependencies, so this needs a decision. The alternative is a module CodeCoreMorph ships, as Phase 17's header.
  5. **Who builds what.** Cargo builds each `cdylib`, offline and `--locked`, before `dotnet build`, and the C# tests load it.
- [ ] Design settled with a `D-xx` row, and SPEC §11.10 filled in

**Adapters, rules and checks**
- [ ] The Rust side as the target of a C# port: the `rust/` workspace, one crate `<project>_rs` per C# project with ported types, each a `cdylib` and `rlib` during the port, with its `bridge` module and cbindgen header; the Rust adapter builds before the C# build
- [ ] The `csharp_to_rust` pack: `port_csharp_type_to_rust`, `port_csharp_program_to_rust`, `port_csharp_tests_to_rust` and `retire_csharp_to_rust`, with the mapping of SPEC §11.10 and the ownership order as guidance
- [ ] Checks: `unsafe_in_bridge`; `lint_clean` (clippy, its machine-applicable fixes applied first, D-62); the symbol map `rust/symbol-map.toml` (C# symbol ID → Rust path); I-6 on the façades by signature; each unit's count of `Rc<RefCell<_>>` and `Arc<Mutex<_>>` in the report
- [ ] Tests: C# test files translated to Rust integration tests (`rust/<crate>/tests/`), `rust/test-map.toml` mapping `dotnet:` to `nextest:` IDs; I-3 and I-4 across the languages; both suites while the C# lasts
- [ ] The retirement deletes the C#, the bridges, the `cdylib` crate type, the cbindgen headers and the generated declarations, and forbids unsafe code in every crate (`#![forbid(unsafe_code)]`). The final verification is a clean Cargo build, `--locked` and offline. With `keep_source_api`, the C# façade library over the `cdylib` stays for .NET consumers, with the bridge and the C# tests of the kept API.
- [ ] Host-agent support (the skill and the unit context) and the planner's estimates for ports from C# to Rust

**Fixtures, benchmark and docs**
- [ ] Phase 17's port fixture and `order-book` ported whole to Rust, replayed from recorded edits (`tests/cassettes/csharp/edits/<fixture>-rust.json`)
- [ ] Benchmark (M12): Phase 17's library ported to Rust and measured; SPEC §13 compares the two targets
- [ ] Docs: `docs/porting-csharp-to-rust.md`, on the ownership redesign, what the report counts, and `keep_source_api` as a .NET library over Rust

### Tests
- Unit: the Rust scaffolds of a C# port, the panic-catching exports' shape, the shared-cell count, the retirement's file list
- Integration (`docker`): one type ported by hand, its C# tests passing through the façade and the `cdylib`
- End to end: both fixtures' ports replay from recorded edits
- Safety: as Phase 17

### Exit criteria (M12)
- The port fixture and `order-book` end as Cargo workspaces: every original C# test maps to a passing Rust test, `unsafe` appeared only in the bridges and none is left after the retirement, and the branch was green at every commit
- With `keep_source_api`, the port fixture ends as a .NET library over the Rust
- The benchmark is ported and measured; in Docker

---

## Backlog (unscheduled)

- **Java → C#** (Phase 11, moved here by D-67)
- **The next legacy language after Phase 18** (D-67, D-77), chosen then: Fortran → Rust or C++ (gfortran or flang, with `ISO_C_BINDING` as the bridge), or batch COBOL → Java or C# (GnuCOBOL, which can call C; CICS, DB2 and JCL can't run in a container)

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
