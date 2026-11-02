# Raise the C++ standard

The build files already name the target standard (a codemod changed `CMAKE_CXX_STANDARD`,
`cxx_std_NN` and `-std=` flags). Your job is to make the code compile and behave the same under
it. Change behavior nowhere.

Only the sections between the source and the target standard apply. A C++98 project moving to
C++20 needs every section up to "C++17 to C++20"; a C++17 project moving to C++23 needs only
the last two.

## C++98/03 to C++11

- Identifiers that became keywords (`nullptr`, `constexpr`, `decltype`, `noexcept`,
  `static_assert`, `thread_local`, `alignas`, `alignof`, `char16_t`, `char32_t`) → rename them,
  with every use. A home-made `nullptr` class or macro (the C++98 idiom) → delete it and use the
  keyword.
- A string literal directly followed by a macro (`"%"PRId64`) is now a user-defined literal →
  add a space: `"%" PRId64`.
- Narrowing conversions inside braces (`int a[] = {x * 1.5};`, `Point p = {w / 2.0, h};`) are
  errors → add an explicit `static_cast` with the same result.
- `auto` as a storage class (`auto int n = 0;`) → remove `auto`.
- Destructors are `noexcept` by default, so an exception leaving a destructor now calls
  `std::terminate` instead of propagating. Where a test or caller relies on that exception,
  mark the destructor `noexcept(false)` and say so in your notes; don't change what is thrown.
- `std::make_pair<A, B>(a, b)` with explicit template arguments no longer accepts lvalues →
  drop the template arguments, or write `std::pair<A, B>(a, b)`.
- Streams convert to `bool` only explicitly: `bool ok = stream;` and `return stream;` from a
  `bool` function → `static_cast<bool>(stream)` or `!stream.fail()`; `stream == NULL` →
  `!stream`.
- `std::swap` is declared in `<utility>`; add the include where `<algorithm>` used to bring it.

## C++11 to C++14

Almost nothing breaks. Watch for `constexpr` member functions, which are no longer implicitly
`const`: add `const` where callers use them on const objects.

## C++14 to C++17

- `std::auto_ptr` → `std::unique_ptr`. Copies of an `auto_ptr` transferred ownership; write
  `std::move` where the old code relied on that.
- `std::random_shuffle(first, last)` → `std::shuffle(first, last, engine)` with a
  `std::mt19937` engine. The resulting order differs; if a test checks a specific order, say
  so in your notes instead of changing the test.
- `std::bind1st`, `std::bind2nd`, `std::ptr_fun`, `std::mem_fun`, `std::mem_fun_ref` → lambdas
  (or `std::bind`, `std::mem_fn`).
- `std::unary_function` / `std::binary_function` base classes → remove them; add the
  `argument_type` / `result_type` aliases only if code uses them.
- Dynamic exception specifications `throw(X)` → remove them; `throw()` → `noexcept`.
- The `register` storage class → remove the keyword.
- Trigraphs (`??=`, `??/`, ...) → the characters they stood for.
- `std::ios_base::io_state`, `open_mode`, `seek_dir` → `iostate`, `openmode`, `seekdir`.
- `noexcept` is part of the function type: function pointers to `noexcept` functions may need
  their type updated.

## C++17 to C++20

- `std::uncaught_exception()` → `std::uncaught_exceptions() > 0`.
- `std::result_of<F(Args...)>::type` → `std::invoke_result_t<F, Args...>`; `std::not1` /
  `std::not2` → `std::not_fn`; `std::is_literal_type`, `std::raw_storage_iterator`,
  `std::get_temporary_buffer` → remove or replace with the obvious modern code.
- Identifiers that became keywords (`concept`, `requires`, `char8_t`, `co_await`, `co_yield`,
  `co_return`, `consteval`, `constinit`) → rename them, with every use.
- `u8"..."` literals are `const char8_t[]` → where the code needs `const char*`, use a plain
  literal with the same bytes, or `reinterpret_cast<const char*>(u8"...")`.
- Implicit capture of `this` by `[=]` is deprecated → write `[=, this]`.
- Aggregates with user-declared constructors are no longer aggregates → give the type a real
  constructor, or remove the user-declared (e.g. `= delete`d or `= default`ed) one.
- Ambiguous comparisons from rewritten operators → make `operator==` symmetric and `const`,
  and remove redundant `operator!=` overloads that now conflict.
- `std::shared_ptr::unique()` → `use_count() == 1`.

## C++20 to C++23

- Returning a by-value or rvalue-reference parameter from a function that returns an lvalue
  reference (`T& f(T&& x) { return x; }`) no longer compiles, because `x` is now an rvalue
  there → `return static_cast<T&>(x);`, or fix the return type.
- Commas inside subscripts (`a[i, j]`) are now an overloaded multi-argument subscript → write
  `a[(i, j)]` if the comma operator was meant, which it almost never is.
- Garbage-collection support (`std::declare_reachable` and friends) is removed → delete the
  calls; they never did anything on mainstream implementations.
- `std::aligned_storage` and `std::aligned_union` are deprecated → `alignas(T) std::byte
  buf[sizeof(T)]`.

Keep the public API stable: if a public function's signature must change (for example it took
an `auto_ptr`), change it and every caller in the unit, and say so in your notes.
