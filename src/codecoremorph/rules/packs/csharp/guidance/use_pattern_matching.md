# Use pattern matching

`dotnet format` has already rewritten the plain cases: `x as T` followed by a null check
became `x is T t` (IDE0019, IDE0260), a type check followed by a cast became `x is T t`
(IDE0020, IDE0038), `!(x is T)` became `x is not T` (IDE0083), and chains of comparisons of one
value became `n is 1 or 2` (IDE0078). What is left:

- A negated check followed by a cast: `if (!(o is Item)) return false; Item other = (Item)o;`
  becomes `if (o is not Item other) return false;`, with `other` used where the cast was.
- A type check with no cast after it (`if (x is Stream)`) is already a pattern: leave it.
- An `as` whose result can be null on purpose (passed on, stored) is not a check: leave it.
- Don't change a check on a value type that boxing or a user-defined conversion affects.

Keep each check meaning exactly what it did; don't merge or reorder conditions.

When nothing that is left is safe to change, submit an empty `edits` list and say why in
`summary`: the unit is then verified as it is.
