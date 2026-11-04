# Writing a language adapter

A language adapter wraps one language's tools: configure, build, test, index and codemods. The
core finds adapters through the `codecoremorph.adapters` entry point and never imports one
directly, so adding a language changes nothing in the orchestrator. The built-in C++ adapter
(`codecoremorph.adapters.cpp`) is the complete example; `templates/plugin/` has a skeleton.

## The class

```python
from codecoremorph.adapters.base import AdapterContext, LanguageAdapter

class JavaAdapter(LanguageAdapter):
    api_version = "1"      # the adapter API this class implements
    language = "java"      # what migration.source.language names
    version = "0.1.0"      # the adapter's own version

    def capabilities(self): ...            # optional methods it has, standards, queries
    def toolchain_requirements(self, lang): ...   # image (Docker) and tools (native)
    def configure(self, ctx): ...          # prepare an out-of-source build in ctx.build_dir
    def build(self, ctx, targets=None): ...       # every diagnostic; warnings never fail it
    def run_tests(self, ctx, selection=None): ... # stable, framework-qualified test IDs
    def index(self, ctx, files=None): ...  # symbols, references, file dependencies
    def run_codemods(self, ctx, codemods, files): ...
```

## The rules adapters follow

- **Every command goes through `ctx.executor`.** An adapter never starts a process itself;
  the executor runs it in the Docker sandbox or natively, with the run's limits and network
  policy. Convert host paths with `ctx.sandbox(path)` before putting them on a command line.
- **Write only inside `ctx.worktree`** (codemods, formatting, test registration). The core
  records those writes in the attempt's diff and checks them like the agent's edits.
- **Heavy analysis runs inside the sandbox**, as a helper program that prints normalized JSON,
  so the host needs no toolchain in Docker mode.
- **Keep no state between calls**, apart from `ctx.cache_dir`.
- **List the standards** in `capabilities().standards`, oldest first. The core compares
  standards only through this list.

## Optional methods

Declare them in `capabilities()`: `parse_check`, `format`, `detect_disabled_tests` (I-3),
`extract_assertions` (I-4), the characterization group (`register_test_files` and the methods
that go with it) and `scaffold_project`. `detect_project(root, files)` (what
`codecoremorph init` writes) and `project_standard(ctx)` have defaults and need no
declaration. A method an adapter doesn't declare raises `AdapterNotSupportedError`, and the
core does without it.

## Registering it

```toml
[project.entry-points."codecoremorph.adapters"]
java = "codecoremorph_java.adapter:JavaAdapter"
```

`codecoremorph adapters list` shows the adapter, its API version and its optional methods, or
why it didn't load. An adapter must declare `api_version`; the version changes only in a major
release, and new optional methods with defaults don't change it.

The full contract, including the data models an adapter returns, is in the
[specification](../SPEC.md#71-language-adapter-contract).
