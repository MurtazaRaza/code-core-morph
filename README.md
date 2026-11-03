# CodeCoreMorph

CodeCoreMorph migrates codebases with LLM agents working inside a compile → test → fix loop.
Real toolchains decide what's correct: every change is built and tested in a sandbox, and
only verified changes are committed, to a dedicated branch you review and merge yourself.

The first target is **C++ standard upgrades**: from C++98/03, 11, 14, 17 or 20 to any newer
standard up to C++23. Java and C# version upgrades come next, then cross-language ports
(Java → C#, C++ → Rust).

> **Status:** early development (Phase 5 of the plan). CodeCoreMorph can plan a C++ migration
> (`codecoremorph plan`) and run a whole migration (`codecoremorph run`), with Claude Code or
> an API model as the agent, or migrate one work unit at a time.

## Developer setup

You need git ≥ 2.30. Docker is optional for now. No admin rights are required.

```sh
scripts\bootstrap-dev.bat        # Windows (cmd.exe or PowerShell)
./scripts/bootstrap-dev.sh       # Linux, macOS, Git Bash
```

The bootstrap script uses `uv` from your PATH if it's recent enough, or downloads a pinned,
checksum-verified copy into `CODECOREMORPH_HOME`. It then installs Python 3.12 with uv,
syncs the dependencies and installs the pre-commit hooks.

```sh
uv run codecoremorph --version
uv run codecoremorph validate examples/cpp11-to-cpp20.yaml
uv run pytest
```

To plan a migration of your own CMake project, install the C++ toolchain (or build the Docker
image with `scripts/build-image cpp`), write a manifest like the example, and run:

```sh
uv run codecoremorph toolchain install cpp      # native toolchain, no admin rights
uv run codecoremorph plan path/to/migration_config.yaml
```

The plan builds and tests your project's base commit in a separate worktree, indexes it, and
lists the ordered work units with their estimated cost. Your working tree is never touched.

`codecoremorph run` then migrates the whole project, unit by unit, onto the branch
`codecoremorph/<run-id>`. Each unit is one commit that builds and passes your tests; a unit
that can't be made green is rolled back and reported. The run asks you to approve the plan
first (`--yes` skips that), and writes a report at the end:

```sh
uv run codecoremorph run path/to/migration_config.yaml
uv run codecoremorph status            # from another terminal: progress, gates, usage
uv run codecoremorph approve           # answer a gate the run waits at (or `reject`)
uv run codecoremorph resume            # after Ctrl-C or a crash
uv run codecoremorph report            # the report of the latest run
```

The agent of a plain `run` is a model API (Anthropic or a local Ollama model, set in the
manifest's `llm` section). With `--host-agent`, Claude Code is the agent instead (next section).

## Migrating with Claude Code

CodeCoreMorph runs as a tool of [Claude Code](https://claude.com/claude-code): Claude makes the
edits on your own Claude plan, and CodeCoreMorph applies them, builds, tests, feeds failures
back and commits each verified unit to a `codecoremorph/<run-id>` branch. No API key is needed.

Install the skill into your project from this checkout:

```sh
uv run codecoremorph skill install --project path/to/your/project   # or --user for every project
```

The skill records where this CodeCoreMorph is, so `codecoremorph` doesn't have to be on your
PATH.

Then, in Claude Code in that project, ask for the migration, for example *"use codecoremorph
to replace the raw owning pointers in src/cache.cpp with smart pointers"*. The skill runs
`codecoremorph unit start`, reads the unit's context, writes the edits and runs
`codecoremorph unit submit` until the unit passes, then tells you which branch to review.

For a whole migration (*"move this project to C++20"*), the skill starts
`codecoremorph run --host-agent`. The run plans the units and stops for you to approve the
plan; the skill tells you what it holds and asks (or approves it at once if you asked for
everything). Then the run takes the units in order on one branch: whenever a unit needs edits
the codemods couldn't make, it stops, Claude writes them, and `codecoremorph unit submit`
verifies them and moves on. A unit that would change an interface can stop for your approval
too, depending on the manifest's `approvals`. At the end the run builds the result from
scratch, runs every test, and writes its report. The report names the tests that already
failed before the migration and the files your build never compiles (another platform's
sources), because neither was checked.

## Documentation

- [docs/SPEC.md](docs/SPEC.md): product and technical specification
- [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md): phased implementation plan

## License

Apache-2.0. See [LICENSE](LICENSE).
