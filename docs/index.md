# CodeCoreMorph

CodeCoreMorph migrates codebases with LLM agents, and lets real toolchains decide what is
correct. It splits a migration into small work units, has deterministic tools do what they can,
gives the rest to an agent, and keeps a unit only when the project still builds, its tests
still pass and a set of safety checks holds. Each kept unit is one commit on a branch of its
own, for you to review.

It upgrades **C++** from C++98/03, 11, 14, 17 or 20 to any newer standard up to C++23, with
modern idioms (alias declarations, smart pointers, concepts, and more); ports **C++ to Rust**,
module by module, to a pure Cargo workspace; and upgrades **Java** from Java 8, 11, 17 or 21 up
to Java 25 (records, lambdas, switch expressions, and more); and upgrades **C#** from C# 7.3 up
to C# 14 (nullable reference types, records, pattern matching, and more). Java to C# comes
next.

## How a migration runs

1. **Plan.** CodeCoreMorph builds and tests your base commit in a separate git worktree (twice,
   to find flaky tests), indexes the code, groups files by dependency and lists ordered work
   units with an estimate of time and cost. You approve the plan.
2. **Characterize.** Code that no test reaches gets *characterization tests* first, which record
   what it does now.
3. **Migrate.** For each unit: codemods (clang-tidy for C++, OpenRewrite for Java, `dotnet format` for C#) make the mechanical changes; the
   agent does the rest, with read-only tools and a single way to propose edits; CodeCoreMorph
   applies them, builds, runs the tests and checks the invariants, and sends failures back to
   the agent. A unit that can't be made green is rolled back and reported.
4. **Verify.** A clean build and the full test suite run on the finished branch, and a report
   lists every unit, what needs review, and what the run cost.

Your working tree is never touched: everything happens in a worktree on the branch
`codecoremorph/<run-id>`.

## Where to start

- [Getting started](getting-started.md): install, write a manifest, plan and run a migration.
- [Migrating with Claude Code](claude-code.md): Claude Code as the agent, on your Claude plan.
- [Porting C++ to Rust](porting-to-rust.md), [Upgrading Java](upgrading-java.md) and
  [Upgrading C#](upgrading-csharp.md).
- [Concepts](concepts.md): units, rules, gates, invariants and the report.
- [Configuration reference](reference/configuration.md) and [CLI reference](reference/cli.md).
- [Security](security.md): what CodeCoreMorph protects against, and what it doesn't.

!!! note "Status"
    CodeCoreMorph is in early development. It migrates C++ projects built with CMake and tested
    with GoogleTest (or anything CTest runs), Java projects built with Maven and tested with
    JUnit, and C# projects built with MSBuild and tested with NUnit, xUnit or MSTest.
