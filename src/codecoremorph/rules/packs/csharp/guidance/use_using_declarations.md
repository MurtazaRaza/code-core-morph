# Use using declarations

`dotnet format` (IDE0063) has already turned each `using (...) { ... }` that runs to the end of
its enclosing block into `using var x = ...;`. What is left, it left because the object would
then be disposed later:

- Code after the `using` block in the same scope: the resource must be closed before it runs
  (a file read and then deleted or moved, a lock or a transaction released). Leave the block.
- Two `using` blocks one after the other in one scope: turning both into declarations disposes
  them together at the end, in reverse order. Leave them unless the order doesn't matter.

When nothing that is left is safe to change, submit an empty `edits` list and say why in
`summary`: the unit is then verified as it is.
