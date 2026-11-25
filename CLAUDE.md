# CLAUDE.md

CodeCoreMorph is an open-source Python tool that migrates codebases. LLM agents make the changes inside a compile → test → fix loop, and real toolchains decide what's correct. It does C++ standard upgrades (C++98/03 to C++23), C++ → Rust, C → Rust and Rust → C ports, and Java and C# version upgrades. The focus is legacy code to modern languages (D-67): C → Rust (Phase 15) and Rust → C (Phase 16) are done. C# → C++ (Phase 17) and C# → Rust (Phase 18) are done too (D-77). Phase 19 lifts six of the first versions' refusals (D-86; all six built, three of them in Docker only) and its benchmark (M13) is done. Phase 20, legacy Java → Rust and → C++ (D-91), which interrupted Phase 19, is done in Docker; next is the next legacy language (Fortran or batch COBOL, D-67), which is the user's call. Java → C# is in the Backlog.

## Read first

- [docs/SPEC.md](docs/SPEC.md) is the authoritative spec: requirement IDs (`FR-x.y`, `NFR-x.y`), decisions (`D-xx`), contracts and config.
- [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) is the phased plan, with checkboxes and per-phase exit criteria. Work goes phase by phase.
- [HighLevelRequirements.md](HighLevelRequirements.md) is the original PRD, kept for reference. Where it disagrees with SPEC.md, SPEC.md wins.
- [docs/porting-c-to-rust.md](docs/porting-c-to-rust.md) is the guide for the current C → Rust work.

## Current status

Phases 0–7, 9, 10, 12–18 and 20 are done (Windows, native and Docker; Phases 15–18 and 20 in Docker). Their details are in the plan and the decision log (D-xx), not here. Where the code lives:

- **Run graph:** `orchestrator/run.py`; all LangGraph calls are in `orchestrator/graph.py`.
- **Adapters:** `adapters/{cpp,rust,java,csharp,c}/`. Rule packs are registered by entry point (`cpp_to_rust`, `java`, `csharp`, `c_to_rust`, …). Rule IDs are global across packs.
- **Host agents (D-41, D-44):** Claude runs as Claude Code through the bundled `codecoremorph` skill (`codecoremorph skill install`) on the user's plan, with no API calls. `run --host-agent` parks at each unit that needs edits and at each gate; the agent drives `unit start|submit|abort`, `approve|reject` and `resume`.
- **Fixtures** replay from recorded host-agent edits and cassettes in `tests/cassettes/<lang>/`. Re-record only with `--record-cassettes`.
- **Dashboard:** `src/codecoremorph/dashboard/` (FastAPI) and `dashboard-ui/` (React). `hatch_build.py` puts `dashboard-ui/dist` into the wheel.

**Phase 8 (M2, v0.1.0 release) is on hold** until the Rust work is done (D-55, D-67). Open: PyPI registration and publishing, SPEC §16 Q6–Q8, image publishing and signing (CI), the clean-machine exit criteria on macOS and Linux. CI is deferred (Backlog, D-28), so Linux and macOS runs wait.

**Phase 15 (M9, D-67 to D-74): C → Rust, done in Docker.** C as a language (`adapters/c/`), Make as a build backend shared with C++ (`adapters/cpp/{make,capture,makefiles}.py`), Linux checkouts for Docker runs, translation units ordered by calls (`port_units: translation_unit`), the `c_to_rust` pack (`port_c_module`, `port_c_executable`, `port_c_tests`, `retire_c`), and the Rust side of a C port (`adapters/rust/cports.py`: Makefiles, and automake with libtool). Functions that take `...` or a `va_list` stay C until the retirement. `c-legacy` (a Makefile) and `c-autotools` (automake, libtool) each ported whole to pure Rust in one host-agent run (11 of 11 units) and replay in Docker. The M9 benchmark, bzip2 1.0.8, ported whole with headless Claude Code (10 of 10 units, D-71). `keep_source_api` for C keeps the public headers, the Make build and the bridges (D-72, `c-legacy-keep` replays in Docker); JUnit XML and the C side of I-3 and I-4 (D-73); C characterization tests are TAP programs in the Makefile (D-74), or in automake, `Makefile.in` and CMake builds (D-89). An evaluation on seven open-source C projects ported each whole, identical to its C on its own corpus, and fixed what it found (D-89): white-box tests, CMake C ports through Corrosion; the fixes that apply to the other conversions were carried to them (D-90). Open: native Windows for Make projects.

**Phase 16 (M10, D-75, D-76): Rust → C, done in Docker.** The Rust source index from rust-analyzer's SCIP output (`adapters/rust/{scip,source}.py`); the C side of the port (`adapters/c/rustports.py`: a top-level CMake build with sanitizers, the constant build script I-12 accepts, the façades' bindgen `sys`, translated TAP tests as CTest tests); the `rust_to_c` pack. The crate is the root package or a workspace member (D-76). `fixtures/rust/rpn` ported whole in one host-agent run (11 of 11 units); the round trip (`tests/integration/test_round_trip.py`: c-legacy → Rust → C, its original C tests passing through both test maps); the M10 benchmark, shlex 1.3.0 ported whole with headless Claude Code (5 of 5 units, byte for byte with the crate over 30,000 inputs). All replay or ran in Docker. Open: the planner's time estimate for ports from Rust (2.7 times low on shlex).

**Phase 17 (M11, D-77 to D-83): C# → C++, done in Docker.** The C# side of a port, built once for both targets: the C# index for ports (`PortSource` in `CsIndexHelper.cs`), the type units (`planner/csharp.py`, `port_units: csharp_type`) and the plan's table of what the source depends on (native libraries, the operating systems' own, .NET packages; D-82), and the façades over P/Invoke with their declarations generated from the bridge headers (`adapters/cpp/pinvoke.py`; what both targets share of the C# side in `adapters/csharp/ports.py` and `port_runtime/`). The C++ side: the image `images/csharp-port`, the runtime CodeCoreMorph ships (`adapters/cpp/csharp_runtime/`), the scaffolds (`adapters/cpp/csharpports.py`), the `csharp_to_cpp` pack, the checks on the C++ (D-80), I-6 by signature and I-4 by case (D-81). `fixtures/csharp/parking` (whole and with `keep_source_api`) and `order-book` replay in Docker (`tests/integration/test_csharp_to_cpp_e2e.py`). The M11 benchmark, libsodium-core 1.3.1 over libsodium, ported whole with headless Claude Code (68 of 68 units), and again after a unit's lint became its own files (3 h 35 min against 5 h 49, 0.98 of its estimate; D-83). Guide: `docs/porting-csharp-to-cpp.md`.

**Phase 18 (M12, D-84, D-85): C# → Rust, done in Docker.** Phase 17's C# side unchanged: the Rust bridge implements the same C ABI (`codecoremorph_bridge.h`), cbindgen writes its header and the C# declarations come from it. The Rust side: the runtime CodeCoreMorph ships (`adapters/rust/csharp_runtime/`: `codecoremorph_runtime` with .NET's `Decimal` and `Exception`, and each crate's `bridge/ccm.rs`), the scaffolds (`adapters/rust/csharpports.py`), native libraries in each crate's `src/native/` (bindgen at configure, the only unsafe code after the retirement), the `csharp_to_rust` pack, the report's shared cells, I-4 through a test's helpers (D-84 (11)). `parking` (whole and with `keep_source_api`) and `order-book` replay in Docker (`tests/integration/test_csharp_to_rust_e2e.py`). The M12 benchmark, libsodium-core to Rust with headless Claude Code (68 of 68 units, 3 h 17 min, 0.97 of its estimate; the same results as M11's C++ over 24,292 seeded calls). Guide: `docs/porting-csharp-to-rust.md`.

**Phase 19 (M13, D-86 to D-88, D-93 to D-97): the first versions' refusals lifted, built in Docker, benchmark done.** Six lifts built: directors (D-87, for the user's review), so a C# class staying C# can derive from a ported class and override it (fixture `fixtures/csharp/fees`), and `dynamic` (D-88, accepted 2026-11-17), where the C# index tells which of the solution's types reach each dynamic site and what the runtime binder binds there for each, so the port makes it a closed sum (fixture `fixtures/csharp/ledger`). The third, after Phase 20, is a Rust crate's own procedural macros in a port to C (D-93, for the user's review): the index records each site (SCIP) with what rust-analyzer's language server expands it to, offline, the agent ports from the expansions, a façade keeps none of the crate's macros, and the macro package goes at the retirement (fixture `fixtures/rust/wire`, 8 of 8 units in a host run). Spikes in `E:/codecoremorph-trials/csharp-spike/{directors,dynamic}/` and `E:/codecoremorph-trials/rust-spike/proc-macros/`. The user's review (2026-11-14) accepted D-68, D-75, D-76 and D-78 to D-85, kept the shipped `Decimal`, and had the report count a committed port unit's sites as converted (`report/build.py`). It lifts six refusals before the next legacy language, each starting with a spike (SPEC §11.11); left: native Windows runs for the fourth lift (below). The fourth, an operating system's own library in a C# port (D-94, for the user's review), is built in Docker: a port is built for Linux, the C# index records each P/Invoke call with the platform checks around it, and the branches for another platform go (fixture `fixtures/csharp/hostkit`, 5 of 5 units to each target); a native Windows run, for calls with no counterpart, waits for the user's decision on llvm-mingw (its ASan fails on this Windows build). The fifth, a .NET package's API (D-95, for the user's review), is built in Docker: CodeCoreMorph ships per-package mappings (`adapters/csharp/packages.toml`: the versions and API covered, per target a C library, a crate pinned by checksum or a shipped runtime, each chosen by a probe of its results), the C# index attaches each package's counterpart, and the unit's message carries the mapping's notes; first K4os.Compression.LZ4 (liblz4), K4os.Hash.xxHash (libxxhash, `xxhash-rust`) and Microsoft.Extensions.Logging.Abstractions (`ccm::logging`, the runtime crate's `logging`) (fixture `fixtures/csharp/stash`, 6 of 6 units to each target). The sixth, `async` in a C# port (D-96, for the user's review), is built in Docker: CodeCoreMorph ships the task types (C++20 coroutines over `ccm::tasks` in `codecoremorph_tasks.hpp`, so a C++17 target refuses async code; the Rust runtime crate's `tasks`, a small safe executor), placed by the first unit with async code (`PortUnit.tasks`); the bridge carries tasks both ways, tokens with their origin, and a C# exception by its .NET types in every C# port (the façade throws the same object again); the C# index records async code as notices and refuses by site what the runtimes don't take over (`async void`, `ValueTask`, .NET's own I/O tasks) (fixture `fixtures/csharp/relay`, 7 of 7 units to each target, every C# port cassette re-recorded). Still refused: Rust `async`, C# classes with public instance fields, types in several library projects. The M13 benchmark, EasyRetry 2.2.0 (refused for its async code and its logging package), ported whole to both targets with headless Claude Code: C++20 22 of 22 units in 90 min (1.65 of its estimate, all 148 C# tests mapped), Rust 22 of 22 in 86 min (1.75, every unit at its first attempt, 146 mapped and 2 null delegates with notes). Over 10,325 seeded lines both matched the C# but for three gaps (a log entry's structured values, the token of a cancellation the native code raises, in the Rust an out-of-range argument's actual value), which D-97 closed with the guidance's two lines that cost attempts and the planner's estimate of a C# type (for the user's review): with its runtime both ports match on every line.

**Phase 20 (M14, D-91, D-92): Java → Rust and Java → C++, done in Docker.** Strangler ports behind Java façades over JNI. The target side is the C# ports' C ABI and scaffolds. The Java side is built once: the Spoon index for ports, `CcmInterop.java`, and `CcmNative.java` plus JNI glue (C or Rust) generated from the bridge headers. The spike (`E:/codecoremorph-trials/java-port-spike/`, `PORT-SPIKE.md`) ported `shop` by hand to both. D-92 awaits the user's review. The engine: the Java index for ports, the planner's Java cut, the generator and both glues, the scaffolds of both targets (`adapters/{cpp,rust}/javaports.py`, the Rust runtime crate `codecoremorph_java`), the Java adapter building the native code first, the `java_to_cpp` and `java_to_rust` packs, the image `images/java-port`, and the unit notes and skill. The fixture `fixtures/java/warehouse` (30 JUnit 4 tests, a Parameterized file among them) ported whole to both targets by host runs (13 of 13 units each, every unit at its first attempt) and replays in Docker (`tests/integration/test_java_to_{cpp,rust}_e2e.py`); a Parameterized method's cases map by the IDs the baseline ran (`PortUnit.data_driven`). With `keep_source_api` it ends as a Java library over the native code (no program, so no retirement). Phase 9's `legacy-ledger` plans whole as a port; `task-planner` is refused (two Maven modules). The M14 benchmark, java-string-similarity 2.0.0, ported whole to both with headless Claude Code (43 of 43 units each; C++ 2 h 33 min, 1.00 of its estimate; Rust 2 h 25 min, 0.97); over 116,024 seeded calls the Rust matches the Java on every call UTF-8 can carry, the C++ too but for strings outside the BMP (its shingles as UTF-8; the guidance fixed). Guides: `docs/porting-java-to-{cpp,rust}.md`. Open: the planner's unit notes never reach the agent (plan item, the user's call: the fix re-records every cassette).

Other open items: Ollama model that completes an agent unit (`qwen3:4b` didn't); characterization tests for Java and C#; Gradle; live evals with a fresh Claude Code for ports, Java and C#.

Update this section whenever a phase completes, and keep it short: put the details in the plan and SPEC.

## Stack

- **Python ≥ 3.12**, managed with **uv**. Lint and format with ruff, type-check with pyright, test with pytest (+ Hypothesis).
- pydantic v2 for every boundary model; Typer + Rich for the CLI; **LangGraph** for orchestration (with the SQLite checkpointer).
- LLM SDKs: `anthropic` (official SDK) and `ollama` (official client), used **only** inside `src/codecoremorph/llm/`.
- Sandbox: the Docker SDK for Python, with a native executor as fallback. Git runs through the git CLI (subprocess), not a library.
- Language tooling lives in the toolchain images (clang/libclang, clang-tidy, CMake + Ninja, GoogleTest, Rust, JDKs + Maven, .NET) or in native toolchains under `CODECOREMORPH_HOME`.
- Dashboard: FastAPI + SSE backend; React + TypeScript + Vite frontend in `dashboard-ui/` (TanStack Query, Monaco diff editor, Cytoscape.js).
- License: Apache-2.0.

## Layout (see SPEC §4.3)

```
src/codecoremorph/{cli,config,core,orchestrator,planner,agent,llm,patching,vcs,sandbox,
          adapters,rules,parity,invariants,store,report,prompts,dashboard}
dashboard-ui/   images/<lang>/   fixtures/<lang>/   bench/   tests/{unit,integration,e2e,cassettes}
```

## Commands

```sh
scripts\bootstrap-dev.bat                  # Windows: uv, Python, deps, hooks (first time); .sh elsewhere
scripts\install-toolchain.bat <lang>       # native toolchain: cpp | rust | java | csharp (rust also installs cpp)
scripts\build-image.bat <lang>             # the codecoremorph-<lang>:local image
uv sync                                    # install, including dev deps
uv run pytest                              # default: no docker / toolchain / live_llm / browser tests
uv run pytest -m toolchain                 # native toolchains under CODECOREMORPH_HOME
uv run pytest -m docker                    # local images (or the published ones)
uv run pytest -m live_llm                  # real LLM calls; opt-in only, costs money
uv run pytest -m browser                   # dashboard smoke test in Chrome (npm run build first)
uv run ruff check . ; uv run ruff format .
uv run pyright
uv run codecoremorph --help
uv run codecoremorph skill install --project PATH   # the Claude Code skill (--user: every project)
uv run codecoremorph dashboard [--dev]     # --dev: behind `npm run dev` (Vite proxy)
uv run mkdocs build --strict               # the docs site
cd dashboard-ui; npm ci; npm run dev | npm run build | npm test | npm run typecheck
```

Benchmarks and checks are in `bench/` (`run.py`, `injection.py`, `memory.py`).

## Architecture rules (do not break these)

1. **The core never imports a specific adapter, rule pack or provider.** It resolves them through the registries (entry points `codecoremorph.adapters`, `codecoremorph.rules`, `codecoremorph.providers`). Adding a language must not touch `orchestrator/` (NFR-3.1).
2. **Every toolchain command goes through an `Executor`** (`sandbox/`). Adapters never call `subprocess` directly. Git operations are the exception: they go through `codecoremorph.vcs`, on the host.
3. **Every change to a target project's files goes through `PatchEngine`** (`patching/`). The only exceptions are codemods, formatters and test registration run by adapters inside the worktree, and those writes are still captured in the attempt diff and checked by invariants.
4. **Every LLM call goes through the `LLMProvider` interface** (`llm/`). No SDK imports outside `llm/`. No `temperature` or other universal sampling knob; provider-specific options go in `provider_options`.
5. **Never touch the user's working tree.** All writes go to the run worktree on branch `codecoremorph/<run-id>`. Never push, merge, rebase, or delete branches CodeCoreMorph didn't create (NFR-2.1, NFR-2.5).
6. **Keep LangGraph state small:** IDs, counters, statuses. Diffs, logs, transcripts and indexes go in the store, referenced by ID (NFR-1.2). Graph construction stays inside `orchestrator/`.
7. **The agent only reads and proposes.** Agent tools are read-only plus `submit_edits`. Build and test are graph steps, never agent tools (D-19). A host agent gets the same contract through `codecoremorph unit`: it reads through `unit read|grep|…` and writes only the edits file; CodeCoreMorph discards anything else it finds in the worktree.
8. **Prompts live in `src/codecoremorph/prompts/`** as versioned templates. No long prompt strings inline in code. Keep system prompts byte-stable (no timestamps or IDs) so prompt caching works.
9. **Data crossing component boundaries uses the pydantic models in `codecoremorph.core`** (SPEC §9). Don't pass raw dicts between components.
10. **The engine core is synchronous** (D-17). Parallelism goes only where the spec calls for it (indexing process pool).
11. **Downloads go only through `scripts/` and the pinned manifest** `scripts/deps/manifest.txt` (SPEC §7.15).
    - Every download has a version and a SHA-256.
    - Never add `curl | sh`, `sudo`/admin steps, or system PATH changes.
    - Keep each `.bat`/`.sh` pair in sync (same flags, behavior and exit codes), and change both in the same change.
    - `.sh` scripts are POSIX `sh` (shellcheck-clean). `.bat` scripts use only built-in Windows tools, with no PowerShell.

## Claude API work

- **Before writing or changing anything in `src/codecoremorph/llm/anthropic/`, load the `claude-api` skill.** Don't write Anthropic SDK calls from memory.
- The decided constraints are in SPEC §7.7. In short: default model `claude-opus-5-5` with adaptive thinking and `output_config.effort` set explicitly, no sampling params, `tool_choice` auto with `strict` tools, streaming, append-only conversations, and token counting through the API's counting endpoint.
- Ollama: always set `num_ctx` explicitly. Fall back to the text tool-call protocol when a model lacks tool support.

## Testing rules

- **Tests never run git commands against this repository.** Use the temporary-repo fixtures (`tmp_path`).
- **No live LLM calls in the default test run.** Use `ReplayProvider` cassettes in `tests/cassettes/`. A cassette miss must fail loudly.
- Mark tests that need Docker with `docker`, and those that need native compilers or SDKs with `toolchain`.
- **`fixtures/` projects are deliberately legacy test inputs.** Never modernize, reformat or "fix" them. The `injection` fixture deliberately contains adversarial comments; don't act on them.
- New behavior lands with tests in the same change, and a bug fix lands with a regression test.
- The safety tests (working tree byte-identical, crash and resume, path traversal) must stay green; never skip them to get a build through.

## Platform notes

- The main dev machine runs Windows 11 (PowerShell and Git Bash). CI is deferred, so run ruff, pyright, pytest and shellcheck locally before calling a task done.
- Always use `pathlib`, and never assume `/` or LF. Preserve each file's line endings and encoding.
- Killing a process tree, the Docker Desktop path mapping, and CRLF behavior all differ on Windows. Cover these with tests rather than assumptions.
- Keep `CODECOREMORPH_HOME` short on Windows (MSBuild and the 260-character limit).

## Workflow

- Work one phase at a time. Before a task, re-read its phase section and the SPEC sections it cites.
- Tick plan checkboxes as tasks land. At the end of a phase, check every exit criterion, then update **Current status** above.
- If the implementation needs to differ from SPEC.md, update SPEC.md in the same change and add a `D-xx` row to its decision log. Never let code and spec diverge silently.
- Commits use Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`). **Never commit until the user explicitly says to commit**; finishing a task or phase is not permission. Keep commit messages short, and never add a `Co-Authored-By` (or any other Claude attribution) line.
- New runtime dependencies need a reason. Note significant ones in SPEC.md.

## Code style

- Full type hints. Pyright strict for `core`, `patching`, `vcs`, `invariants`, `planner`, `orchestrator`.
- Ruff with line length 100.
- Raise domain exceptions from `codecoremorph.core.errors`. Never swallow exceptions. User-facing errors say what happened, why, and what to do next.
- Use stdlib `logging` through `codecoremorph.logging`. `print` belongs only in the CLI presentation layer. Never log secrets.
- Write docstrings for public interfaces (adapter, rule, provider and executor APIs). Keep other comments for non-obvious reasons only.
