# Use switch expressions

`dotnet format` (IDE0066) has already turned the switch statements whose cases each return a
value, or each assign one variable, into switch expressions. What is left, it declined:

- A case that runs statements before its `return` (a log call, a counter) can't be an arm:
  leave the switch.
- A case that falls through to another (`case 1: case 2: return x;` is fine as `1 or 2 =>`; a
  case with statements that continues into the next one isn't).
- A switch without a `default` whose missing case did nothing: an expression must produce a
  value for every input, and a `_ =>` arm that throws changes behavior. Leave it.

An arm keeps its case's value and its order; `default` becomes `_ =>` and goes last.

When nothing that is left is safe to change, submit an empty `edits` list and say why in
`summary`: the unit is then verified as it is.
