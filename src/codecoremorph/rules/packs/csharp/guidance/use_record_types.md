# Use records for value classes

The sites are classes that look like values: every instance field read-only and no property
with a setter, no base class, no subclass in the project, and `Equals(object)` and
`GetHashCode` of their own. Turn one into a `record` (a `sealed record` if the class was
sealed) only when all of this holds; otherwise leave it and say why in your notes.

- `Equals` and `GetHashCode` compare exactly the fields, all of them. A record compares every
  field it has; a class that left one out (a cache, a derived value) keeps its own `Equals`
  (`public virtual bool Equals(Item? other)`) or stays a class.
- Keep the members callers use. If callers read `item.Sku`, the record keeps a `Sku`
  property: a positional record `record Item(string Sku, int Quantity)` gives one per
  parameter. Never rename a member in this rule.
- A record adds `==` and `!=` that compare values. If code compared these objects with `==`
  for identity, that code's meaning changes: leave the class.
- `ToString` changes to `Item { Sku = a, Quantity = 1 }`. If a test, a log format or a caller
  reads the old text, keep the class's `ToString` in the record.
- Validation in the constructor stays: give the record an explicit constructor, or validate
  in the property's `init`.
- A record's `GetHashCode` is its own, so hash values change, and with them the order of a
  `HashSet` or `Dictionary` enumerated by tests or output: keep the class's `GetHashCode` in
  the record where that order shows.
- A `[Serializable]` class read back by a formatter, or a type bound by name (configuration,
  JSON), stays a class unless the shape stays the same.

When nothing that is left is safe to change, submit an empty `edits` list and say why in
`summary`: the unit is then verified as it is.
