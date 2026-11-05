# Use switch expressions

OpenRewrite has already turned the switches it could prove equivalent into switch
expressions. Convert one of the rest only when it stays equivalent:

- A case that falls through into the next (no `break`, `return` or `throw`) stays a statement;
  so does a switch whose cases do different kinds of work.
- A switch expression must be exhaustive. A `default` that the statement didn't have changes
  nothing only if it throws what reaching no case would have meant; on an enum, a missing
  constant means the old code did nothing, so keep the statement.
- `break` inside a case that also breaks out of an enclosing loop can't move into an arrow
  case.
- Arrow cases (`case A, B -> …`) never fall through: merge labels that shared a body, and
  check that no case relied on reaching the next one.

When nothing that is left is safe to change, submit an empty `edits` list and say why in
`summary`: the unit is then verified as it is.
