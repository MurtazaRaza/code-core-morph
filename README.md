# CodeCoreMorph

CodeCoreMorph migrates codebases with LLM agents working inside a compile → test → fix loop.
Real toolchains decide what's correct: every change is built and tested in a sandbox, and
only verified changes are committed, to a dedicated branch you review and merge yourself.

It upgrades **C++** (C++98/03 up to C++23), **Java** (8 up to 25) and **C#** (7.3 up to 14),
and ports **C++ to Rust**. The focus is legacy code moving to modern languages: **C → Rust**
comes next, then Rust → C.

> **Status:** early development. CodeCoreMorph can plan a C++ migration
> (`codecoremorph plan`) and run a whole migration (`codecoremorph run`), with Claude Code or
> an API model as the agent, or migrate one work unit at a time. Code that no test covers gets
> characterization tests first. A local web dashboard follows runs live and answers their gates.
> It can also port a C++ project to Rust: module by module, each header kept as a C++ façade so
> the project's own C++ tests check every step, then the programs, then the tests (each paired
> with its Rust translation), and finally the C++ itself, leaving a Cargo workspace
> (Phases 12 to 14).

## Developer setup

You need git ≥ 2.30. Docker is optional for now. No admin rights are required.

```sh
scripts\bootstrap-dev.bat        # Windows (cmd.exe or PowerShell)
./scripts/bootstrap-dev.sh       # Linux, macOS, Git Bash
```

The bootstrap script uses `uv` from your PATH if it's recent enough, or downloads a pinned,
checksum-verified copy into `CODECOREMORPH_HOME`. It then installs Python 3.12 with uv,
syncs the dependencies and installs the pre-commit hooks. It does the same for Node.js, which
builds the dashboard's web UI (`dashboard-ui/`); the bootstrap prints where it put it.

```sh
uv run codecoremorph --version
uv run codecoremorph validate examples/cpp11-to-cpp20.yaml
uv run pytest
```

To plan a migration of your own CMake project, install the C++ toolchain (or build the Docker
image with `scripts/build-image cpp`), write a manifest, and run:

```sh
uv run codecoremorph toolchain install cpp      # native toolchain, no admin rights
uv run codecoremorph init path/to/project       # writes path/to/project/migration_config.yaml
uv run codecoremorph plan path/to/project/migration_config.yaml
```

`init` detects the C++ standard, the source, test and vendored directories and the rules that
apply, and writes them with comments; read it through before the first run, above all a
standard it marks as a guess.

The plan builds and tests your project's base commit in a separate worktree, indexes it, and
lists the ordered work units with their estimated cost. Your working tree is never touched.

`codecoremorph run` then migrates the whole project, unit by unit, onto the branch
`codecoremorph/<run-id>`. Each unit is one commit that builds and passes your tests; a unit
that can't be made green is rolled back and reported. Before the plan, functions no test reaches
get *characterization tests*, which record what they do now; they are committed first and then
guard every unit (turn this off with `parity.characterization_tests.enabled: false`, or remove
the tests at the end with `keep: false`). The run asks you to approve the plan first (`--yes`
skips that), and writes a report at the end:

```sh
uv run codecoremorph run path/to/migration_config.yaml
uv run codecoremorph status            # from another terminal: progress, gates, usage
uv run codecoremorph approve           # answer a gate the run waits at (or `reject`)
uv run codecoremorph resume            # after Ctrl-C or a crash
uv run codecoremorph report            # the report of the latest run
```

The agent of a plain `run` is a model API (Anthropic or a local Ollama model, set in the
manifest's `llm` section). With `--host-agent`, Claude Code is the agent instead (see
[Migrating with Claude Code](#migrating-with-claude-code)).

### The dashboard

```sh
uv run codecoremorph dashboard -c path/to/migration_config.yaml
```

This opens a local page in your browser: it listens on 127.0.0.1 only, and its link holds a
token that is new at every launch. There you can follow runs live, review the plan and approve
it (leaving units out if you like) or reject it, read each unit's diff side by side with its
compiler messages, tests, safety checks and the agent's transcript, keep or roll back a unit
waiting at its gate, and read or download the report. A gate that `codecoremorph run` is asking
about in a terminal can be answered from the dashboard as well; whichever answer comes first
wins.

Installed packages include the web UI. In a source checkout, build it once:
`cd dashboard-ui; npm ci; npm run build` (with the Node.js `bootstrap-dev` installed).

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

The documentation site is built from `docs/` (`uv run mkdocs serve` to read it locally):
getting started, concepts, guides to Claude Code, the dashboard and dependencies,
troubleshooting, the configuration and CLI references, and how to write rules, adapters and
providers (`templates/plugin/` is a plugin to copy).

- [docs/SPEC.md](docs/SPEC.md): product and technical specification
- [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md): phased implementation plan
- [SECURITY.md](SECURITY.md): the security model, and how to report a vulnerability
- [CONTRIBUTING.md](CONTRIBUTING.md) and [CHANGELOG.md](CHANGELOG.md)

## License

Apache-2.0. See [LICENSE](LICENSE).
