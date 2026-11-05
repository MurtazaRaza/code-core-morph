# Use var for local variables

OpenRewrite's `UseVar` has already rewritten the declarations whose type the initializer
names. Change nothing more unless the type stays as obvious as it was:

- Keep the declared type where it is an interface the code means (`List<String> names = new
  ArrayList<>()`): `var` would make `names` an `ArrayList`, which matters for overloads.
- Keep it where the initializer is a method call whose type a reader can't see, a lambda, a
  `null`, or a diamond without arguments to infer from (`var list = new ArrayList<>()` is an
  `ArrayList<Object>`).
- Keep it for numeric literals whose type changes (`long n = 0` is not `var n = 0`).
- Only local variables, including `for` loop variables: never fields, parameters or return
  types.

When nothing that is left is safe to change, submit an empty `edits` list and say why in
`summary`: the unit is then verified as it is.
