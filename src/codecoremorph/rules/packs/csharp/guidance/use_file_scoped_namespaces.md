# Use file-scoped namespaces

`dotnet format` (IDE0161) has already rewritten each file with one namespace: `namespace X;`
after the `using`s, and everything in it one indentation level less. What is left is a file it
declined:

- A file with more than one namespace, or a namespace nested in another, can't be file-scoped:
  leave it.
- A file with code outside the namespace (a type declared before it, an `extern alias`):
  leave it.

Change nothing but the namespace declaration and the indentation of what it contains.

When nothing that is left is safe to change, submit an empty `edits` list and say why in
`summary`: the unit is then verified as it is.
