# Use lambdas for anonymous classes

OpenRewrite has already turned the plain cases into lambdas. The anonymous classes left are
ones where a lambda means something else. Convert one only when none of these holds:

- It has state (a field) or more than one method, or its interface has more than one
  abstract method (an abstract class is never a lambda).
- Its body uses `this` or `super` meaning the anonymous object (`this.equals(o)`, removing
  itself as a listener with `remove(this)`): in a lambda, `this` is the enclosing object.
- It declares a variable with the same name as one in the enclosing method: an anonymous
  class may shadow it, a lambda may not.
- Code relies on each evaluation creating a new object (identity, `==`, or a listener
  registered twice): a non-capturing lambda may be one shared instance.
- It is serialized, or a test checks its class name.

A lambda whose body is one call with the same arguments can be a method reference
(`String::length`). Keep the parameter names readable; don't change what the code does.

When nothing that is left is safe to change, submit an empty `edits` list and say why in
`summary`: the unit is then verified as it is.
