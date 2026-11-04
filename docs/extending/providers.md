# Writing an LLM provider

A provider makes model calls for CodeCoreMorph's built-in agent loop. The built-in ones are
`anthropic` and `ollama`; others register through the `codecoremorph.providers` entry point.
Every model call in CodeCoreMorph goes through this interface, and provider SDKs are imported
nowhere else. `templates/plugin/` has a skeleton.

## The class

```python
from typing import ClassVar, Self

class ExampleProvider:
    api_version: ClassVar[str] = "1"   # the provider API this class implements
    name: ClassVar[str] = "example"    # what llm.roles.<role>.provider names; = the entry point
    local: ClassVar[bool] = False      # True only if no code leaves the machine

    @classmethod
    def from_config(cls, config, llm) -> Self: ...   # llm.providers.example, never credentials
    def capabilities(self, model): ...     # context window, output limit, tool support
    def complete(self, request): ...       # one call, the whole answer
    def count_tokens(self, request): ...   # input tokens, counted the way the service does
    def health_check(self, model=None): ...  # reachable, credentials present, model available
```

## The contract

- **Results, not exceptions, for refusals.** A refusal is a result with
  `StopReason.REFUSAL`. Failures raise the `codecoremorph.core.errors.LLMError` subclasses
  (rate limits, context overflow, timeouts), never an SDK's exceptions, so the loop can retry,
  shrink the context or escalate.
- **No universal sampling settings.** There is no `temperature` in the manifest. Options a
  provider understands arrive in `request.provider_options`, from the role's
  `provider_options`.
- **Append-only conversations.** The loop only ever adds messages, and marks cache breakpoints;
  keep earlier turns byte for byte the same, so that prompt caching works.
- **Credentials** come from the environment or the provider SDK's own credential chain. The
  manifest refuses them.
- **`local`** decides whether the report may say that no code left the machine.

## Registering it

```toml
[project.entry-points."codecoremorph.providers"]
example = "codecoremorph_example.provider:ExampleProvider"
```

Then use it in a role:

```yaml
llm:
  roles:
    editor: {provider: example, model: my-model}
```

`codecoremorph providers list -c migration_config.yaml` shows the providers, their API versions
and the model of each role; `codecoremorph doctor` runs `health_check` for every configured
model. A provider must declare `api_version`; the version changes only in a major release.

Cassettes (`codecoremorph.llm.replay`) record and replay calls, so tests of a provider's use in
a run need no live model.
