# Use records for value classes

The sites are classes that look like values: every field private and final, no superclass,
no subclass in the project, and `equals` and `hashCode` of their own. Turn one into a record
only when all of this holds; otherwise leave it and say why in your notes.

- `equals` and `hashCode` compare exactly the fields, all of them. A record compares every
  component; a class that left one out (a cache, a derived value) keeps its own `equals`, or
  stays a class.
- Keep the accessors callers use. A record's accessor is `x()`; if callers call `getX()`, keep
  `public int getX() { return x; }` in the record, so no call site changes. Never rename the
  accessors in this rule.
- A record's `hashCode` is its own, so hash values change, and with them the iteration order
  of a `HashMap` or `HashSet` of these values. Where a test or output depends on that order,
  keep the class's `hashCode` in the record.
- `toString` changes to `Point[x=1, y=2]`. If a test, a log format or a caller reads the old
  text, keep the class's `toString` in the record.
- Validation in the constructor goes into a compact constructor (`record P(int x) { P { if (x
  < 0) throw …; } }`); defensive copies of arrays and collections go there too.
- A record serializes differently from a class: leave a `Serializable` class alone when
  anything reads data it serialized.
- Static fields, static methods and nested types move into the record unchanged.

When nothing that is left is safe to change, submit an empty `edits` list and say why in
`summary`: the unit is then verified as it is.
