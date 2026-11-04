# Concepts

## The manifest

`migration_config.yaml` says what to migrate (the project's directories, what is vendored, what
is public API), from which standard to which, with which rules, and how carefully: budgets,
approvals, the sandbox. `codecoremorph init` writes one; the
[configuration reference](reference/configuration.md) lists every key.

## Rules

A rule is one kind of change, such as `replace_typedefs_with_using` or
`convert_raw_pointers_to_smart_pointers`. It can have:

- a **detector** that finds its sites, so files without any are skipped;
- **codemods**, deterministic tools that make the change where they can (clang-tidy fixes, for
  C++);
- **guidance** for the agent, for what the codemods can't do;
- **post-conditions** that say when the unit is done, such as "no typedef left".

`codecoremorph rules list` shows the installed rules. The **standard bump** (`bump_standard`)
is special: it runs first whenever the target is newer than the source, raises the standard in
the build files and fixes what the new standard breaks.

## The plan and its units

The planner indexes the project, builds a dependency graph of its files, groups files that must
change together (a header and its source, files in a dependency cycle) and orders the groups so
that dependencies come first. Each group, with the rules that apply to it, is a **work unit**.
An API-changing rule pulls in the files that use the changed API. The plan estimates each unit's
time and cost, and lists units skipped as too large or C-compatible.

## Attempts and the loop

A unit gets up to `guardrails.max_retry_budget` retries after its first attempt. In each
attempt the agent proposes edits; CodeCoreMorph applies them, builds, runs the tests, checks the
invariants and the rule's post-conditions, and sends any failure back as feedback. A unit that
succeeds is committed; one that runs out of attempts is rolled back, and the report says why.

## Invariants

Every attempt is checked before it is committed:

| | Invariant | On violation |
|---|---|---|
| I-1 | Edits stay in the worktree, outside `.git`, excluded and vendored paths | rejected |
| I-2 | Edits stay in the unit's files | feedback |
| I-3 | No test removed or disabled | attempt fails |
| I-4 | No test case loses an assertion | flagged, or needs approval |
| I-5 | Every test that passed before still passes | feedback |
| I-6 | Public API and C ABI unchanged, unless the rule changes API | attempt fails, or a gate |
| I-7 | No new warning suppressions | feedback |
| I-8 | Diff within `guardrails.max_unit_diff_lines` | attempt fails |
| I-9 | The rules' post-conditions | feedback or flag |
| I-10 | No new compiler warnings in the unit's files | flag or feedback |
| I-11 | Only the run's worktree and branch are touched | run stops |
| I-12 | No new commands that run or download at build time | attempt fails |

## Gates

The run stops for your decision at the **plan gate** (unless `approvals.plan: auto`), and at a
**unit gate** for units that change API or tests, as `approvals.unit` sets. Answer with
`codecoremorph approve` or `reject`, in the [dashboard](dashboard.md), or at the terminal
prompt of an interactive run.

## Characterization tests

Before the plan, CodeCoreMorph looks for functions no test reaches and has the test writer
write tests that pin down what they do now. Each test has to pass three times on the unchanged
code and use no clock, randomness, files or network. Accepted tests are committed first and
guard every later unit. `parity.characterization_tests` turns this off, or removes the tests at
the end.

## Where the work happens

CodeCoreMorph never touches your checkout. A run creates a git worktree on the branch
`codecoremorph/<run-id>`, builds in a directory of its own, and keeps its state in
`.codecoremorph/` at the repository root (added to `.git/info/exclude`). Builds and tests run in
a **sandbox**: a Docker container by default, or natively with a warning. A run that stops (Ctrl-C,
a crash, a gate) continues with `codecoremorph resume`.

## Budgets

`guardrails` caps what a run may spend: the cost of paid models (`max_run_cost_usd`), tokens,
time per unit and the diff size. When a budget runs out, the run stops cleanly, keeps what is
committed and writes its report.

## The report

`codecoremorph report` prints the run's report: every unit's outcome, the rules and their sites,
what needs review (changed assertions, API changes, new warnings, tests that were flaky or
failing before the run), the final verification, which providers saw code, and the usage by
role. It is also written as `report.md` and `report.json` in the run's directory, with
credentials redacted.
