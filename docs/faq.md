# FAQ

**Does CodeCoreMorph change my code?**
Not in your checkout. A run works in a separate git worktree on a branch of its own,
`codecoremorph/<run-id>`, and never pushes, merges or rebases. You review and merge the branch.

**What does it send to the model provider?**
The unit's files, the declarations they use, build and test output, and the rules' guidance.
Nothing goes anywhere with a local-only setup (every role on Ollama); the report lists the
providers that received code. With Claude Code as the agent, the code goes to Claude through
Claude Code, as any other work in Claude Code does.

**Do I need an API key?**
No, if you use Claude Code with the skill: it runs on your Claude plan. A plain
`codecoremorph run` with the built-in agent needs a model provider: an Anthropic API key, or a
local Ollama model.

**How much does a migration cost?**
`codecoremorph plan` estimates each unit and the whole run. For leveldb (29k lines, C++11 to
C++20), Claude Code reported $27 of usage at API prices, about $0.95 per thousand lines.
`guardrails.max_run_cost_usd` caps what paid providers may spend.

**What if a unit can't be migrated?**
It is rolled back, and the report says why: the agent's reason when it gave up, or the checks
that kept failing. The other units go on.

**Can it break my tests to make them pass?**
Deleting or disabling a test fails the attempt; a test that loses an assertion is flagged, or
needs your approval; every test that passed before has to pass after.

**Which build systems and test frameworks work?**
CMake, with tests that CTest runs; GoogleTest's test IDs and assertions are understood in full.
Other build systems need a language adapter, which plugins can provide.

**Can I add my own rules?**
Yes: a directory of YAML rules and Markdown guidance, listed in `migration.rules_paths`, or a
rule pack installed as a plugin. See [Writing rules](extending/rules.md).

**Does it work offline?**
The native toolchain installs from pre-downloaded archives, and local models need no network.
See [Installing dependencies](dependencies.md).
