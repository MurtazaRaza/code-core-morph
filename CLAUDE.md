# CLAUDE.md

CodeCoreMorph is an open-source Python tool that migrates codebases. LLM agents make the changes inside a compile → test → fix loop, and real toolchains decide what's correct. It does C++ standard upgrades (C++98/03 to C++23), C++ → Rust ports, and Java and C# version upgrades. The focus now is legacy code to modern languages (D-67): C → Rust (Phase 15, in progress), then Rust → C (Phase 16). Java → C# is in the Backlog.

## Read first

- [docs/SPEC.md](docs/SPEC.md) is the authoritative spec: requirement IDs (`FR-x.y`, `NFR-x.y`), decisions (`D-xx`), contracts and config.
- [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) is the phased plan, with checkboxes and per-phase exit criteria. Work goes phase by phase.
- [HighLevelRequirements.md](HighLevelRequirements.md) is the original PRD, kept for reference. Where it disagrees with SPEC.md, SPEC.md wins.
- [docs/porting-c-to-rust.md](docs/porting-c-to-rust.md) is the guide for the current C → Rust work.

## Current status

Phases 0–7, 9, 10, 12, 13 and 14 are done (Windows, native and Docker). Their details are in the plan and the decision log (D-xx), not here. Where the code lives:

- **Run graph:** `orchestrator/run.py`; all LangGraph calls are in `orchestrator/graph.py`.
- **Adapters:** `adapters/{cpp,rust,java,csharp,c}/`. Rule packs are registered by entry point (`cpp_to_rust`, `java`, `csharp`, `c_to_rust`, …). Rule IDs are global across packs.
- **Host agents (D-41, D-44):** Claude runs as Claude Code through the bundled `codecoremorph` skill (`codecoremorph skill install`) on the user's plan, with no API calls. `run --host-agent` parks at each unit that needs edits and at each gate; the agent drives `unit start|submit|abort`, `approve|reject` and `resume`.
- **Fixtures** replay from recorded host-agent edits and cassettes in `tests/cassettes/<lang>/`. Re-record only with `--record-cassettes`.
- **Dashboard:** `src/codecoremorph/dashboard/` (FastAPI) and `dashboard-ui/` (React). `hatch_build.py` puts `dashboard-ui/dist` into the wheel.

**Phase 8 (M2, v0.1.0 release) is on hold** until the Rust work is done (D-55, D-67). Open: PyPI registration and publishing, SPEC §16 Q6–Q8, image publishing and signing (CI), the clean-machine exit criteria on macOS and Linux. CI is deferred (Backlog, D-28), so Linux and macOS runs wait.

**Phase 15 (M9, D-67 to D-70): C → Rust, in progress.** Done: C as a language (`adapters/c/`), Make as a build backend shared with C++ (`adapters/cpp/{make,capture,makefiles}.py`), Linux checkouts for Docker runs, translation units ordered by calls (`port_units: translation_unit`), the `c_to_rust` pack (`port_c_module`, `port_c_executable`, `port_c_tests`, `retire_c`), and the Rust side of a C port (`adapters/rust/cports.py`: Makefiles, and automake with libtool). Functions that take `...` or a `va_list` stay C until the retirement. `c-legacy` (a Makefile) and `c-autotools` (automake, libtool) each ported whole to pure Rust in one host-agent run (11 of 11 units) and replay in Docker. Open: JUnit XML, the TAP side of I-3 and I-4, characterization tests for C, `keep_source_api` for C, the benchmark, a full rebuild of the images.

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
