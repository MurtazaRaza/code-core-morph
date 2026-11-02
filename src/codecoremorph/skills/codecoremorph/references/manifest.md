# Writing `migration_config.yaml`

CodeCoreMorph reads one manifest at the repository root. A minimal C++ manifest:

```yaml
version: "1.0"
project:
  name: my-project
  source_dirs: ["src", "include"]    # directories with code to migrate
  test_dirs: ["tests"]               # directories with the project's tests
  public_api: ["include/**"]         # headers other projects include
migration:
  source: {language: cpp, standard: cpp11, build_system: cmake}
  target: {language: cpp, standard: cpp20, rules: []}
```

How to fill it in:

- **Standards:** `cpp98`, `cpp03`, `cpp11`, `cpp14`, `cpp17`, `cpp20`, `cpp23`. Find the source standard in the build files (`CMAKE_CXX_STANDARD`, `set_property(... CXX_STANDARD ...)`, `-std=` flags). Confirm the target with the user.
- **Directories:** look at the repository layout. Leave out vendored code with `project.exclude: ["third_party/**"]`.
- **Rules:** `target.rules` lists the rules a full run applies, in order (`codecoremorph rules list`); the standard bump runs first automatically. A unit started with `unit start --rule` doesn't need its rule listed here.
- **Build:** only CMake is supported. `build.cmake.configure_args` passes extra configure flags, e.g. `["-DBUILD_TESTING=ON"]` when tests are off by default.
- **Sandbox:** `sandbox.mode: auto` uses Docker when it is available, otherwise native.
- **Retries:** `guardrails.max_retry_budget` (default 3) is the number of retries after a unit's first attempt; `guardrails.max_edit_retries` (default 2) is the number of extra tries for rejected edits within an attempt.

The `llm` section configures model providers for unattended runs; this workflow doesn't use it, so leave it out.

Check the result with `codecoremorph validate`. `codecoremorph schema` prints the full JSON Schema.
