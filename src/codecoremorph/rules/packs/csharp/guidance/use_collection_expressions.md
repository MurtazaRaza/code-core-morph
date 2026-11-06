# Use collection expressions

`dotnet format` has already rewritten the collections whose target type is the collection's
own (IDE0028, IDE0300 to IDE0305): `List<int> xs = new List<int>();` is `List<int> xs = [];`,
`new int[] { 1, 2 }` is `[1, 2]`, `Array.Empty<int>()` is `[]`, and a list built from another
sequence is `[.. values]`. What is left, it left because the type would change:

- A target typed as an interface (`IEnumerable<int> xs = new List<int>()`): a collection
  expression may build another type there, and code that casts back to `List<int>` breaks.
  Leave it.
- `var xs = new List<int>();` has no target type: leave it.
- A collection with a capacity or a comparer (`new List<int>(100)`,
  `new HashSet<string>(StringComparer.Ordinal)`): leave it.

When nothing that is left is safe to change, submit an empty `edits` list and say why in
`summary`: the unit is then verified as it is.
