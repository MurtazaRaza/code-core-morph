# CodeCoreMorph

CodeCoreMorph migrates codebases with LLM agents working inside a compile → test → fix loop.
Real toolchains decide what's correct: every change is built and tested in a sandbox, and
only verified changes are committed, to a dedicated branch you review and merge yourself.

The first target is **C++ standard upgrades**: from C++98/03, 11, 14, 17 or 20 to any newer
standard up to C++23. Java and C# version upgrades come next, then cross-language ports
(Java → C#, C++ → Rust).

> **Status:** early development (Phase 4 of the plan). CodeCoreMorph can plan a C++ migration
> (`codecoremorph plan`) and migrate one work unit at a time, with Claude Code as the agent.

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

## Documentation

- [docs/SPEC.md](docs/SPEC.md): product and technical specification
- [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md): phased implementation plan

## License

Apache-2.0. See [LICENSE](LICENSE).
