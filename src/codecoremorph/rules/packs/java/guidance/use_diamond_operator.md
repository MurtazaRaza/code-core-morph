# Use the diamond operator

OpenRewrite has already replaced the type arguments it can prove the compiler infers the same
way. What is left, it left on purpose; change one only when you can see why it is safe:

- An anonymous class (`new Comparator<String>() { … }`) takes `<>` only from Java 9; keep the
  arguments when the build's level is 8.
- A constructor call passed straight to an overloaded or generic method may infer another
  type with `<>` (`foo(new ArrayList<Object>())` where `foo` takes `List<? extends T>`): keep
  the arguments.
- A raw type (`new ArrayList()`) is not this rule's: leave it.

When nothing that is left is safe to change, submit an empty `edits` list and say why in
`summary`: the unit is then verified as it is.
