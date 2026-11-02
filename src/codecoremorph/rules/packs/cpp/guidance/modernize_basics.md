# Basic modernizations

clang-tidy fixes these on its own. You are only asked to help when its fix didn't compile or
left a site behind:

- `NULL` and `0` used as pointers → `nullptr`
- Overriding virtual functions → `override` (drop the redundant `virtual`)
- Empty constructors and destructors → `= default`
- Private, undefined copy operations → `= delete` (made public)
- Index and iterator loops over a whole container → range-based `for`
- `<stdio.h>`-style headers → `<cstdio>`
- `f(void)` → `f()`
- C++14 and newer: `typename std::remove_const<T>::type` → `std::remove_const_t<T>`; C++17:
  `std::is_same<A, B>::value` → `std::is_same_v<A, B>`
- C++17 and newer: `namespace a { namespace b {` → `namespace a::b {`; `static_assert(x, "")`
  → `static_assert(x)`
- C++20 and newer: `s.find(prefix) == 0` and `s.compare(0, n, prefix) == 0` →
  `s.starts_with(prefix)`

Make the smallest change that compiles. Don't add suppressions (`NOLINT`).
