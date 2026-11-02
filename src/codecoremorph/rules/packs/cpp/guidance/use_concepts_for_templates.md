# Use concepts instead of SFINAE

clang-tidy (`modernize-use-constraints`) has converted the `std::enable_if` uses it
understands. Convert the rest:

- `typename std::enable_if<C, R>::type f(...)` → `R f(...) requires C`
- A defaulted template parameter `typename = std::enable_if_t<C>` → a `requires C` clause.
- `std::enable_if` on class template partial specializations → constrain the specialization:
  `template <typename T> requires C struct S<T> { ... };`
- Detection idioms (`decltype(std::declval<T>().f(), void())`, `void_t`) → a `requires`
  expression: `requires(T t) { t.f(); }`.
- When the same constraint appears on several templates, define a named concept once (in the
  header that declares the templates) and use it: `template <Arithmetic T>`.

Overload resolution must pick the same function as before for every call in the project.
Prefer the standard library's concepts (`std::integral`, `std::floating_point`,
`std::convertible_to`, `std::same_as`) when they say exactly what the old trait said.
