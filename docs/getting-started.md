# Getting started

## What you need

- **git** 2.30 or newer, and a project in a git repository with at least one commit.
- **A CMake project** with tests that CTest runs (GoogleTest works out of the box).
- **Docker** (Docker Desktop on Windows and macOS), or a native toolchain that CodeCoreMorph
  installs for you without admin rights. Docker is the default: builds and tests then run in a
  locked-down container.
- **An agent:** Claude Code with the CodeCoreMorph skill (on your Claude plan, no API key), or a
  model API: Anthropic (`ANTHROPIC_API_KEY`) or a local Ollama model.

## Install

```sh
uv tool install codecoremorph
codecoremorph --version
```

Any Python 3.12 environment works too (`pip install codecoremorph`). Then check your machine:

```sh
codecoremorph doctor
```

`doctor` checks git, Docker, the native toolchains and, given a manifest, the models it uses,
and says what to install for anything missing.

## Get the toolchain

With Docker, a run pulls the pinned toolchain image the first time it needs it. Until the images
are published, build the C++ image once from a source checkout instead:
`scripts/build-image cpp`. Without Docker:

```sh
codecoremorph toolchain install cpp
```

This downloads pinned, checksum-verified LLVM, CMake, Ninja and GoogleTest under
`CODECOREMORPH_HOME` (see [Installing dependencies](dependencies.md); machines without internet
access are covered there too).

## Write the manifest

In your repository:

```sh
codecoremorph init
```

`init` reads the files git tracks and writes `migration_config.yaml`: the current C++ standard
(from your CMake files, or guessed from the code when they set none), the source, test and
vendored directories, the public headers, the rules that apply, and CMake options a run needs.
Every setting has a comment. Read it through, above all a standard marked as a guess, and pick
the target with `--target cpp23` if C++20 isn't what you want.

```sh
codecoremorph validate
```

## Plan

```sh
codecoremorph plan
```

The plan builds and tests your base commit in a separate worktree, indexes it and lists the
work units with estimates. It changes no code. If the base doesn't build or has no passing
tests, it stops and says why.

## Run

```sh
codecoremorph run
```

The run shows the plan and asks for your approval (`--yes` skips that). Then it works through
the units. From another terminal, or the [dashboard](dashboard.md):

```sh
codecoremorph status            # progress, gates, usage
codecoremorph approve           # answer a gate the run waits at (or reject)
codecoremorph resume            # after Ctrl-C or a crash
codecoremorph report            # the report of the latest run
```

With Claude Code as the agent, ask Claude to do the migration instead: see
[Migrating with Claude Code](claude-code.md).

## Review and merge

The result is the branch `codecoremorph/<run-id>`: one commit per unit, each of which builds and
passes your tests. Read the report (`codecoremorph report`), then review the branch like any
other pull request; the report lists what needs a closer look, such as tests whose assertions
changed or API that moved. Merge it yourself. `codecoremorph clean` removes the run's worktree
and, with `--branches`, its branch.
