# Writing rules

A rule is a YAML file, plus Markdown guidance for the agent. Rules for your own project go in a
directory that the manifest lists in `migration.rules_paths`; rules for others go in a rule pack
installed as a plugin. Both work the same way.

## A rule

```yaml
id: replace_typedefs_with_using        # the file's name, replace_typedefs_with_using.yaml
language: cpp
applies_to: {min_target_standard: cpp11}
kind: api_preserving                   # api_preserving | api_changing | project | port
description: Replace typedef declarations with alias declarations (using X = Y).
detector:                              # optional: finds sites; files without any are skipped
  type: adapter_query
  query: typedef_declarations
codemods:                              # optional: a deterministic first pass
  - tool: clang-tidy
    checks: [modernize-use-using]
llm:                                   # optional: guidance for what codemods can't do
  guidance: guidance/replace_typedefs_with_using.md
  examples: examples/replace_typedefs_with_using/
postconditions:
  - {type: detector_empty, severity: error}   # no typedef left in the unit's files
skip:
  - c_compatible                       # never apply to headers shared with C
```

- **`kind`.** `api_preserving` rules change code without changing interfaces. `api_changing`
  rules may change them: the planner adds the files that use the changed API to the unit, and
  the `api_changes` unit gate applies. `project` rules change the build configuration and may
  edit build files. `port` rules replace code with code in another language behind the same
  interface: they name `target_language` (the manifest's `migration.target.language`) and
  `extra_scope`, globs of the new code's files a unit may create (`rust/**`). `port_units`
  says how units are cut: `c_header` (the default) holds the files with sites, the headers
  declaring what they define (read-only) and the build files; `module` makes one unit per
  module of a library target, leaves first, its headers kept as façades; `executable` one per
  program that isn't a test; `tests` one per test source, read-only, translated beside it; and
  `retirement` one last unit that removes what is left of the source language.
  See [Porting C++ to Rust](../porting-to-rust.md).
- **`applies_to`.** `min_target_standard` and `max_source_standard` limit a rule to some
  migrations; `codecoremorph validate` reports a listed rule that doesn't apply.
- **`detector`.** `type: pattern` with regular expressions, matched on code only (comments and
  string literals are blanked out first); a pattern can carry its own standard conditions, as
  in `{pattern: '\bauto_ptr\b', min_target_standard: cpp17}`. `type: adapter_query` asks the
  language adapter (`codecoremorph adapters list` shows its queries). `type: files` makes each
  of a unit's files of the rule's language a site until it is deleted (a port that replaces its
  files). Without a detector, the rule applies to every file of its language.
- **`codemods`.** Tools the adapter runs before the agent. For C++: `clang-tidy` with checks
  that have fixes.
- **`llm`.** `guidance` is Markdown that tells the agent what to do and what to leave alone;
  `examples` is a directory of `before` and `after` files. A rule with only `llm` is a
  natural-language rule.
- **`postconditions`.** `detector_empty` (no sites left), `codemod_clean` (the codemods report
  nothing left), `c_abi_kept` (a port's new code exports the C interface its headers declare;
  every port rule checks it), and for module ports `unsafe_in_bridge`, `lint_clean` (the target
  language's linter is quiet about the new code) and `symbol_map` (the project's symbol map
  covers the module's names), and `test_map` (every test the unit translates is in the project's
  test map, its new test run and passing). `error` sends the agent back to work; `warning` is a
  review item.
- **`allow_suppressions`.** `true` lets the agent add `NOLINT`, `#[allow(...)]` and the like;
  a list of globs allows them in those files only (`port_module`: the Rust bridge).
- **`hooks`** (optional, `module:function`) add a custom detector or post-condition in Python.
  A detector hook is called as `function(worktree, files)` and returns rule sites. Hooks import
  only installed modules; the project being migrated is never on `sys.path`.

## Writing guidance

The agent reads the guidance with the unit's files. Good guidance:

- says what the change is and what "done" looks like, in a few sentences;
- names the cases to leave alone, and asks the agent to say why it left a site;
- keeps behavior exactly the same, and changes nothing else.

The guidance of the built-in C++ rules (`codecoremorph/rules/packs/cpp/guidance/`) are worked
examples.

## Trying a rule

```sh
codecoremorph rules list -c migration_config.yaml      # your rules, with "(pack project:...)"
codecoremorph debug unit --rule my_rule src/file.cpp   # one unit, start to finish
```

## A rule pack

A pack adds `pack.yaml` beside the rules:

```yaml
name: example
language: cpp
api_version: "1"        # the rule pack API (the rule format) the pack is written for
version: "0.1.0"
description: An example rule pack.
min_tool_versions:
  clang-tidy: "18"      # checks the pack uses need at least these versions
```

To ship it, put the directory in a Python package and name it in the `codecoremorph.rules`
entry point: a `Path`, or a function that returns one. Rule IDs are global across every
installed pack, so start yours with the pack's name. The repository's `templates/plugin/` is a
complete example to copy.

## API versions

A plugin pack must declare `api_version`. CodeCoreMorph loads only packs whose version it
supports, and says which it skipped and why (`codecoremorph rules list`, `validate`). The
version changes only in a major release; a new optional field doesn't change it.
