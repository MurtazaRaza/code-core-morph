# codecoremorph-example

A starting point for a CodeCoreMorph plugin. It has one of each kind:

| Kind | Entry point group | Here | Status |
|---|---|---|---|
| Rule pack | `codecoremorph.rules` | `src/codecoremorph_example/rules/` | Complete: one rule that replaces `sprintf` with `snprintf` |
| Language adapter | `codecoremorph.adapters` | `src/codecoremorph_example/adapter.py` | Skeleton |
| LLM provider | `codecoremorph.providers` | `src/codecoremorph_example/provider.py` | Skeleton |

Most plugins are rule packs: rules for a language CodeCoreMorph already supports, written as
YAML and Markdown, with no Python at all.

## Make it yours

1. Copy this directory and rename the package (`codecoremorph_example`), the distribution
   (`codecoremorph-example` in `pyproject.toml`) and the names in the entry points.
2. Delete the kinds you don't need, along with their entry points.
3. Rule IDs are global across every installed pack, so start yours with the pack's name.
4. Install it into the environment CodeCoreMorph runs in, and check that it loads:

   ```sh
   uv pip install -e .             # or: pip install -e .
   codecoremorph rules list        # your rules, with "(pack example)"
   codecoremorph adapters list     # your adapter's language and API version
   codecoremorph providers list
   uv run pytest                   # tests/test_plugin.py
   ```

   A CodeCoreMorph installed with `uv tool install` has its own environment; add the plugin to
   it with `uv tool install codecoremorph --with-editable ./path/to/your/plugin`.

## API versions

Each plugin declares the plugin API version it implements: `api_version = "1"` on an adapter
or provider class, and `api_version: "1"` in a rule pack's `pack.yaml`. CodeCoreMorph loads
only plugins that declare a version it supports. One it skips shows up as a warning in
`codecoremorph adapters list` (or `rules list`, `providers list`) and `codecoremorph validate`,
with the reason.

API versions change only in a major CodeCoreMorph release. Additions that a plugin doesn't
need to know about, such as a new optional adapter method that has a default, keep the
version. That's why `pyproject.toml` depends on `codecoremorph>=0.1,<1`.

## What to read

- The rule format: SPEC §7.3, and the built-in C++ pack (`codecoremorph/rules/packs/cpp/`).
- The adapter contract: SPEC §7.1. Every command goes through `ctx.executor`; an adapter never
  starts a process itself.
- The provider contract: SPEC §7.7.
