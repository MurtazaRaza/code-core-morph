# Use try-with-resources

OpenRewrite has already converted the `try`/`finally` blocks that only close what they
opened. For the rest:

- Declare each resource in the `try (…)` header, in the order it was opened; they close in
  the reverse order, as nested `finally` blocks did.
- A `finally` that does more than close (logging, releasing a lock, resetting state) keeps
  that work in a `finally` of its own after the resources.
- A `close()` wrapped in `try { … } catch (IOException ignored) {}` swallowed close errors; with
  try-with-resources they propagate, or become suppressed exceptions of the one in flight. Keep
  the old behavior where a test or caller relies on it: leave that block as it is.
- A `null` check before `close()` is no longer needed: try-with-resources skips a `null`
  resource.
- A resource that outlives the block (returned, stored in a field) is not a resource of the
  block: leave it.

When nothing that is left is safe to change, submit an empty `edits` list and say why in
`summary`: the unit is then verified as it is.
