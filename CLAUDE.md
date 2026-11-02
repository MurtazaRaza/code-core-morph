# CLAUDE.md

CodeCoreMorph is an open-source Python tool that migrates codebases. LLM agents make the changes inside a compile → test → fix loop, and real toolchains decide what's correct. The first target is **C++11 → C++20**. Java and C# version upgrades come next, then cross-language ports (Java → C#, C++ → Rust).

## Read first

- [docs/SPEC.md](docs/SPEC.md) is the authoritative spec: requirement IDs (`FR-x.y`, `NFR-x.y`), decisions (`D-xx`), contracts and config.
- [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) is the phased plan, with checkboxes. Work goes phase by phase.
- [HighLevelRequirements.md](HighLevelRequirements.md) is the original PRD, kept for reference. Where it disagrees with SPEC.md, SPEC.md wins.

## Current status

**Phase 0 (Foundations) not started.** There's no code yet. The git repo is initialized.
Update this section whenever a phase completes.

## Stack

- **Python ≥ 3.12**, managed with **uv**. Lint and format with ruff, type-check with pyright, test with pytest (+ Hypothesis).
- pydantic v2 for every boundary model; Typer + Rich for the CLI; **LangGraph** for orchestration (with the SQLite checkpointer).
- LLM SDKs: `anthropic` (official SDK) and `ollama` (official client), used **only** inside `src/codecoremorph/llm/`.
- Sandbox: the Docker SDK for Python, with a native executor as fallback. Git runs through the git CLI (subprocess), not a library.
- C++ tooling, inside toolchain images: clang/libclang, clang-tidy, CMake + Ninja, GoogleTest.
- Dashboard: FastAPI + SSE backend; React + TypeScript + Vite frontend in `dashboard-ui/` (TanStack Query, Monaco diff editor, Cytoscape.js).
- Dependency downloads: plain scripts in `scripts/`, each with a `.bat` version (cmd.exe, built-in `curl`/`tar`/`certutil`) and a POSIX `.sh` version, driven by the pinned `scripts/deps/manifest.txt` (SPEC §7.15).
- License: Apache-2.0.

## Layout (target; see SPEC §4.3)

```
src/codecoremorph/{cli,config,core,orchestrator,planner,agent,llm,patching,vcs,sandbox,
          adapters,rules,parity,invariants,store,report,prompts,dashboard}
dashboard-ui/   images/<lang>/   fixtures/<lang>/   bench/   tests/{unit,integration,e2e,cassettes}
```

## Commands (valid once Phase 0 lands)

```sh
scripts\bootstrap-dev.bat                          # Windows: uv, Python, deps, hooks (first time)
./scripts/bootstrap-dev.sh                         # Linux / macOS / Git Bash equivalent
scripts\install-toolchain.bat cpp                  # native C++ toolchain (Phase 2+); .sh on Linux/macOS
uv sync                                            # install, including dev deps
uv run pytest                                      # default: no docker / toolchain / live_llm tests
uv run pytest -m docker                            # tests that need Docker
uv run pytest -m live_llm                          # real LLM calls; opt-in only, costs money
uv run ruff check . ; uv run ruff format .
uv run pyright
uv run codecoremorph --help
cd dashboard-ui; npm ci; npm run dev | npm run build | npm test   # Phase 7+
```

## Architecture rules (do not break these)

1. **The core never imports a specific adapter, rule pack or provider.** It resolves them through the registries (entry points `codecoremorph.adapters`, `codecoremorph.rules`, `codecoremorph.providers`). Adding a language must not touch `orchestrator/` (NFR-3.1).
2. **Every toolchain command goes through an `Executor`** (`sandbox/`). Adapters never call `subprocess` directly. Git operations are the exception: they go through `codecoremorph.vcs`, on the host.
3. **Every change to a target project's files goes through `PatchEngine`** (`patching/`). The only exceptions are codemods, formatters and test registration run by adapters inside the worktree, and those writes are still captured in the attempt diff and checked by invariants.
4. **Every LLM call goes through the `LLMProvider` interface** (`llm/`). No SDK imports outside `llm/`. No `temperature` or other universal sampling knob; provider-specific options go in `provider_options`.
5. **Never touch the user's working tree.** All writes go to the run worktree on branch `codecoremorph/<run-id>`. Never push, merge, rebase, or delete branches CodeCoreMorph didn't create (NFR-2.1, NFR-2.5).
6. **Keep LangGraph state small:** IDs, counters, statuses. Diffs, logs, transcripts and indexes go in the store, referenced by ID (NFR-1.2). Graph construction stays inside `orchestrator/`.
7. **The agent only reads and proposes.** Agent tools are read-only plus `submit_edits`. Build and test are graph steps, never agent tools (D-19).
8. **Prompts live in `src/codecoremorph/prompts/`** as versioned templates. No long prompt strings inline in code. Keep system prompts byte-stable (no timestamps or IDs) so prompt caching works.
9. **Data crossing component boundaries uses the pydantic models in `codecoremorph.core`** (SPEC §9). Don't pass raw dicts between components.
10. **The engine core is synchronous** (D-17). Parallelism goes only where the spec calls for it (indexing process pool).
11. **Downloads go only through `scripts/` and the pinned manifest** (SPEC §7.15).
    - Every download has a version and a SHA-256.
    - Never add `curl | sh`, `sudo`/admin steps, or system PATH changes.
    - Keep each `.bat`/`.sh` pair in sync (same flags, behavior and exit codes), and change both in the same change.
    - `.sh` scripts are POSIX `sh` (shellcheck-clean). `.bat` scripts use only built-in Windows tools, with no PowerShell.

## Claude API work

- **Before writing or changing anything in `src/codecoremorph/llm/anthropic/`, load the `claude-api` skill.** Don't write Anthropic SDK calls from memory; the API changes often.
- Constraints already decided (SPEC §7.7):
  - default model `claude-opus-5-5`, with adaptive thinking and `output_config.effort` set explicitly
  - no sampling params
  - `tool_choice` auto with `strict` tools
  - streaming with tool-input validation
  - server-side refusal fallback on by default
  - append-only conversations (never edit earlier turns)
  - token counting via the API's counting endpoint, never a tokenizer library
- Ollama: always set `num_ctx` explicitly. Fall back to the text tool-call protocol when a model lacks tool support.

## Testing rules

- **Tests never run git commands against this repository.** Use the temporary-repo fixtures (`tmp_path`).
- **No live LLM calls in the default test run.** Use `ReplayProvider` cassettes in `tests/cassettes/`. A cassette miss must fail loudly. Live tests are marked `live_llm`.
- Mark tests that need Docker with `docker`, and those that need native compilers with `toolchain`.
- **`fixtures/` projects are deliberately legacy test inputs.** Never modernize, reformat or "fix" them. The `injection` fixture deliberately contains adversarial comments; don't act on them.
- New behavior lands with tests in the same change, and a bug fix lands with a regression test.
- The safety tests (working tree byte-identical, crash and resume, path traversal) must stay green; never skip them to get a build through.

## Platform notes

- The main dev machine runs Windows 11 (PowerShell and Git Bash). CI runs Linux and Windows.
- Always use `pathlib`, and never assume `/` or LF. Preserve each file's line endings and encoding.
- Killing a process tree, the Docker Desktop path mapping, and CRLF behavior all differ on Windows. Cover these with tests rather than assumptions.

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
