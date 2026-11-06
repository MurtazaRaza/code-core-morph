# Use target-typed new

`dotnet format` (IDE0090) has already dropped the type from each `new` whose target names it
(`List<int> numbers = new();`). What is left:

- A `new` passed to an overloaded method or a generic one, or returned from a lambda: without
  the type, the call can bind another overload or infer another type. Keep the type.
- A `var` declaration has no target type: `var x = new Foo();` stays (it already names the
  type once).

When nothing that is left is safe to change, submit an empty `edits` list and say why in
`summary`: the unit is then verified as it is.
