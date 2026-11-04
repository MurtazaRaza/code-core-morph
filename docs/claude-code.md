# Migrating with Claude Code

CodeCoreMorph can run as a tool of [Claude Code](https://claude.com/claude-code). Claude makes
the edits on your own Claude plan, with no API key and no per-token bill, and CodeCoreMorph
applies them, builds, tests, feeds failures back and commits each verified unit to the branch
`codecoremorph/<run-id>`.

## Install the skill

```sh
codecoremorph skill install --project path/to/your/project   # or --user for every project
```

The skill records where this CodeCoreMorph is installed, so `codecoremorph` doesn't have to be on
the PATH Claude Code sees. `codecoremorph skill path` shows where it went.

## One unit

In Claude Code, in your project, ask for a change, for example: *"use codecoremorph to replace
the raw owning pointers in src/cache.cpp with smart pointers"*. The skill:

1. starts a unit with `codecoremorph unit start`;
2. reads the unit's context document: the rules and their guidance, the unit's files after the
   codemods, the declarations they use, and what is left to do;
3. writes its edits to the unit's edits file and runs `codecoremorph unit submit`, which applies,
   builds, tests and checks them;
4. repeats with the feedback until the unit passes, then tells you which branch to review.

Claude reads the project only through `codecoremorph unit read`, `grep`, `symbol`, `refs` and the
like, and writes only the edits file. Anything else it changes in the worktree is discarded.

## A whole migration

Ask for the whole thing (*"move this project to C++20"*) and the skill starts
`codecoremorph run --host-agent`:

1. The run plans the units and stops at the plan gate. The skill tells you what the plan holds
   and asks you, or approves it at once if you asked for everything.
2. The run takes the units in order on one branch. Whenever a unit needs edits the codemods
   couldn't make, the run parks; Claude writes them, and `codecoremorph unit submit` verifies
   them and moves on. A unit that would change an interface can stop for your approval too,
   depending on the manifest's `approvals`.
3. At the end the run builds the result from scratch, runs every test and writes its report.

If Claude Code stops in the middle, ask it to continue: `codecoremorph status` shows where the
run is, and the skill picks it up from there. Without the skill, the same steps work by hand:
`codecoremorph unit context`, then `unit submit` or `unit abort`, `approve` or `reject`, and
`resume`.

## What it costs

A host-agent run uses your Claude plan's limits. As a guide, migrating leveldb (29k lines) from
C++11 to C++20 took Claude Code 2 hours 11 minutes, with 24 of 27 units committed and every test
passing at the end; Claude Code reported the usage as worth $27 at API prices.
