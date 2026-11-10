# Port a C# type to C++ behind its own C# API

The unit moves one type of a C# library to C++ (the standard of `migration.target`), with the
types its note names: its nested types, a reference cycle, or a class with virtual members and
the classes that derive from it. Nothing else changes. The rest of the library and the
project's C# tests keep using the type, which keeps its C# API and becomes a **façade**: each
member's body calls the C++ through P/Invoke. So the tests decide whether the port is right.
Types go leaves first: what this type uses of the library is C++ already, behind its own
façade, so the C++ uses those C++ classes directly.

The C++ runs under AddressSanitizer and UndefinedBehaviorSanitizer in every test, inside the
.NET test host: an out-of-bounds access, a use after free, signed overflow or a misaligned load
aborts the test run with the sanitizer's report. That, `-Wall -Wextra` and clang-tidy's
bug-finding checks are what replaces the runtime's checks.

The unit's checks (`lint_clean`) hold the C++ to that: no warning, clang-tidy's bug-finding
checks quiet, and outside `cpp/bridge` no `new` or `delete` whose pointer no smart pointer owns
(`std::make_shared`, `std::make_unique`; `std::shared_ptr<T>(new T(...))` for a private
constructor) and no cast between pointer types (`reinterpret_cast`, or a C-style cast that is
one). The bridge's handles and contexts are its own casts. Keep `CODECOREMORPH_SANITIZE` in
the top-level `CMakeLists.txt`.

## What the codemod made

- `CMakeLists.txt` at the top and `cpp/CMakeLists.txt` (the first unit made them): the static
  library `<name>_cpp` of the ported code and the shared library `<name>_native` of the bridge,
  each with a marked list of sources. The unit's sources are in the lists already. Don't edit
  them otherwise.
- `cpp/include/codecoremorph_decimal.hpp` (`ccm::Decimal`, .NET's `decimal` bit for bit) and
  `cpp/include/codecoremorph_exceptions.hpp` (`ccm::Exception` and the mirrors of .NET's
  exceptions), and `cpp/bridge/codecoremorph_bridge.{h,hpp,cpp}` (the bridge's types and
  helpers). CodeCoreMorph ships them: don't edit them.
- `cpp/src/<unit>.hpp` and `cpp/src/<unit>.cpp`: empty. **The port goes here**, in a namespace
  from the C# one in snake case (`Zoo` is `zoo`, `Shop.Billing` is `shop::billing`).
- `cpp/bridge/<unit>.h` and `cpp/bridge/<unit>.cpp`: empty. **The exports go here**: the C
  declarations the façade calls, and their C++ bodies.
- Beside the façade project's file: `CodeCoreMorph.Interop.cs`, the façades' support (don't
  edit it), and `CodeCoreMorph.Native.g.cs`, the C# declarations of the exports, **generated**
  from the bridge headers at every configure (never edit it). A project's own exceptions go in
  `CcmExceptions.cs` beside them (below).
- `cpp/symbol-map.toml`: the map of C# members to C++ names (below).

## What the unit does

1. Port the types to C++ in `cpp/src/<unit>.hpp` and `.cpp`: every public and protected member,
   and what they need of the private ones.
2. Write the bridge: an export for each member the façade calls, declared in
   `cpp/bridge/<unit>.h` and defined in `cpp/bridge/<unit>.cpp`.
3. Turn each C# type into its façade: keep every public and protected member with exactly its
   signature, modifiers, XML docs and attributes (I-6 compares them), and make its body call
   the export. Delete the C# logic and the private members the C++ replaced.
4. Map each public and protected member in `cpp/symbol-map.toml`.

## The bridge

- Each export is `CCM_EXPORT ccm_status <name>_<Type>_<member>(...)` in the header's
  `extern "C"` block (`zoo_Animal_sound`, `shop_Money_add`), with its results in out
  parameters named `result` or `result_<part>`. Its body is
  `return bridge::guard([&] { ... });` (with `namespace bridge = ccm::bridge;`), so no exception
  leaves it: the status carries it to the façade.
- It takes only: fixed-width integers, `bool`, `float`, `double`, `ccm_decimal`,
  `const char *` (a UTF-8 string in, maybe null), `char **` (a string out), handles, `void *`
  contexts, `ccm_release_fn`, function pointer typedefs, out pointers to these, input
  arrays (`const int32_t *values_data` beside `int32_t values_count`, or `const char **` for
  strings) and arrays out (`uint8_t **result_data` beside `int32_t *result_count`). The
  generator refuses anything else, with the header's line.
- **Strings:** `bridge::str(text, "param")` where the C# throws `ArgumentNullException` for
  null, `bridge::opt_str(text)` where null is allowed; out, `bridge::out_str(result, s)`.
- **Arrays** of numbers (a `byte[]`): in, `bridge::array(key_data, key_count, "key")` gives a
  `std::vector`, the façade passing `CcmInterop.Count(key)` (-1 for null, which throws
  `ArgumentNullException` as the C# check did; where the C# allows null, test the count).
  Out, `bridge::out_array(result_data, result_count, bytes)` copies a vector, and the façade
  reads it with `CcmInterop.TakeBytes(data, count)` (`TakeInts` for `int32_t`), which frees
  it: one call per array, not one per element.
- **Decimals:** `bridge::dec(ccm_decimal)` gives a `ccm::Decimal`, and back.
- **A class** is a handle: `CCM_HANDLE(zoo_Animal);` in the header's `extern "C"` block,
  `CCM_HANDLE_CLASS(zoo_Animal, zoo::Animal)` after it (C++ only, with a forward declaration
  of the class), and `CCM_HANDLE_EXPORTS(zoo_Animal)` in the `.cpp`. A handle holds a
  `std::shared_ptr`: `bridge::to_handle<zoo_Animal>(std::make_shared<zoo::Animal>(...))` makes
  one, `bridge::from_handle(self)` reads one (a null handle throws `NullReferenceException`, as
  C# does), and `bridge::from_handle_or_null` takes a null the C# allows. A hierarchy shares its
  root's handle type: each constructor returns the root's handle to the derived object, virtual
  calls dispatch in C++, and an export of a derived class's own member casts
  (`std::dynamic_pointer_cast<zoo::Dog>`). An export that names the object's class (an
  `int32_t` tag) lets the façade wrap an object that comes back in the right C# class.
- **A struct** crosses as its fields (`amount, currency` in; `result_amount, result_currency`
  out): the C# struct keeps its fields, and each member calls the C++ on a copy of them.
- **An enum** crosses as `int32_t`, the C++ an `enum class` with the same values.
- **A collection** the C# returns crosses through exports of its size and of an element
  (`_count`, `_at`), and the façade builds what the C# returned (a `List<T>`, a
  `ReadOnlyCollection<T>`, an array).
- **Calls into C#.** A delegate parameter becomes a function pointer typedef taking a
  `void *context` first and returning a status
  (`typedef ccm_status (*zoo_Pen_fold_fn)(void *context, int32_t seed, zoo_Animal *item, int32_t *result);`);
  the C++ calls it through `bridge::check_managed(fn(context, ...))`, which turns a C#
  exception into `bridge::ManagedException`, and the façade rethrows the C# exception itself.
  An interface implemented in C# (a test double) reaches the C++ through a proxy: an export
  `<name>_<I>_from_managed(void *context, ccm_release_fn release, <fn per member>...,
  <name>_<I> **result)` makes a C++ class implementing the interface over a
  `bridge::ManagedRef`; `_<I>_managed` gives the C# object back, and a ported implementation
  converts to the interface's handle (`_<C>_as_<I>`). An event becomes
  `_subscribe_<event>(self, context, release, fn, int32_t *token)` and
  `_unsubscribe_<event>(self, token)`: the façade keeps its C# subscribers and holds one native
  subscription while it has any.
- **Exceptions.** The C++ throws the mirror of what the C# threw, where it threw it, with the
  same message and parameter name: `ccm::ArgumentNullException("name")`,
  `ccm::InvalidOperationException("an empty pen")`, `ccm::KeyNotFoundException(...)`. The
  façade throws the .NET exception again. A project's own exception type stays a C# class (the
  tests catch it): its C++ class derives from `ccm::Exception` (or the mirror of its .NET base),
  overrides `dotnet_type()` (`"Zoo.ZooException"`) and `fields()` (its C# constructor's
  arguments, by name), and `CcmExceptions.cs` constructs it from them:

  ```csharp
  namespace CodeCoreMorph.Interop
  {
      internal static partial class CcmInterop
      {
          static partial void CreateProjectException(CcmError error, ref Exception result)
          {
              switch (error.Type)
              {
                  case "Zoo.ZooException":
                      result = new Zoo.ZooException(error.Message);
                      break;
              }
          }
      }
  }
  ```

## The façade

The façade calls `CcmNative.<export>` from `CodeCoreMorph.Native.g.cs` (`using
CodeCoreMorph.Interop;`), generated from your header at the next configure: `int` for
`int32_t`, `string` for `const char *`, `out IntPtr` for `char **`, `CcmDecimal` for
`ccm_decimal`, `zoo_AnimalHandle` for a handle, a delegate of the typedef's name for a function
pointer. Each call goes through `CcmInterop.Check(...)`, which throws what the C++ reported.

- **A class** holds `internal readonly zoo_AnimalHandle Handle;`. Its constructor calls `_new`
  and `CcmObjects.Register(handle.Key, this)`; a private constructor takes a handle that came
  back; `internal static Animal FromHandle(zoo_AnimalHandle h)` returns
  `CcmObjects.Wrap(h, x => new Animal(x))`, so an object that comes back from C++ is the same
  C# object (`Assert.AreSame` holds); `internal static zoo_AnimalHandle HandleOf(Animal a)`
  returns `zoo_AnimalHandle.Null` for null. Other façades use these two.
- **Strings out** are read with `CcmInterop.TakeString(ptr)`, which frees them; decimals with
  `CcmDecimal.From(x)` and `.ToDecimal()`.
- **A delegate** passed to C++ is a `CcmNative` delegate built around the C# one, with the C#
  call inside `CcmInterop.Callback(...)`; keep it alive for the call (a local the call uses
  last). A context the C++ keeps is `CcmInterop.Pin(obj)` with `CcmInterop.Release`.
- **An interface** stays a C# interface, unchanged. Beside it, an internal static class gives
  the handle of any implementation: a ported class's own, or a proxy over a C# one.
- A hierarchy's façades stay a hierarchy: the base holds the handle, and a derived class passes
  its handle to the base's protected constructor. An `abstract` or `virtual` member keeps its
  modifiers, and each override's body calls the export.
- Static members call exports that use the C++ static state; static fields the C# exposes
  become what they were (a `const` stays a `const`).

## The mapping

- A class is held by `std::shared_ptr` where the C# shares it, and by `std::unique_ptr` or by
  value where it has one evident owner; a struct is a value type. No owning raw pointer outside
  the bridge.
- Interfaces are abstract classes (pure virtual functions, a virtual destructor); a hierarchy
  keeps its `virtual` and `override`, `sealed` is `final`.
- Properties are getters and setters (`name()`, `set_name()`); an indexer is `operator[]` or
  `at()`; operators stay operators; `Equals` and `GetHashCode` are `operator==` and a
  `std::hash` specialization; `ToString` is `to_string()`.
- `null` is `std::optional` or an empty `shared_ptr`; `IDisposable` and `using` are
  destructors; delegates and events are `std::function`; LINQ is `<algorithm>` (and ranges from
  C++20); `lock` is `std::mutex`.
- `List<T>`, `Dictionary<K,V>` and `HashSet<T>` are `std::vector`, `std::unordered_map` and
  `std::unordered_set`. C#'s `Dictionary` enumerates in insertion order while nothing was
  removed, and tests often depend on it: where the C# enumerates one, keep the order (a vector
  of keys beside the map).
- `string` is UTF-8 `std::string`. C#'s `Length` and indices count UTF-16 units: the same for
  ASCII; otherwise note it in the symbol map.
- `decimal` is `ccm::Decimal`, with its arithmetic, rounding, `to_string` and `parse`.
  `DateTime` and `TimeSpan` are `<chrono>`. Format numbers as the C# did in the invariant
  culture.
- The checks C# makes: unchecked integer overflow wraps in C# and is undefined for signed C++
  integers, so do arithmetic that may overflow in unsigned types and convert back, and throw
  `ccm::OverflowException` where the C# was `checked`. Integer division by zero, a bad index
  and a null dereference throw in C#: check, and throw the mirror the C# threw
  (`DivideByZeroException`, `ArgumentOutOfRangeException` for a `List`,
  `IndexOutOfRangeException` for an array).
- A generic type is a template, exported once per type argument in use (the unit's note lists
  them: `zoo_Pen_Dog_add`, `zoo_Pen_Bird_add`); the C# generic façade dispatches on
  `typeof(T)` to the right exports.
- **Checked arithmetic.** When the note says the project checks integer overflow
  (`CheckForOverflowUnderflow`), every integer `+`, `-`, `*`, conversion and negation outside
  `unchecked` throws `System.OverflowException` in the C#: check each in the C++ (the
  `__builtin_*_overflow` functions) and throw `ccm::OverflowException`.

## What the C# runtime did that the C++ does itself

The unit's note names each of these where the C# has them (D-82).

- **Native calls.** A P/Invoke (`[DllImport("libsodium")] static extern int
  crypto_box_easy(...)`) becomes a plain call of the C function: include the library's own
  header (`#include <sodium.h>`) in the `.cpp` that calls it, and pass `std::vector<uint8_t>`'s
  `data()` and `size()` where the C# passed a `byte[]`. The scaffold links the library to
  `<name>_cpp` (`find_library` in `cpp/CMakeLists.txt`'s marked block of native libraries).
  The C# types that only declare the functions (an `Interop` class) need no port: leave them,
  the retirement deletes them. A `ref`/`out` parameter is a pointer to a local; an `IntPtr`
  the library returns (`sodium_version_string`) is the `const char *` or handle its header
  says.
- **Two copies of a native library.** Until the retirement, the C# not ported yet still calls
  the library through its own P/Invokes, which load the package's copy (its NuGet native
  file), while the C++ links the toolchain's: two copies in one process, each with its own
  state. What the C# did to the library before using it (`sodium_init`, a global setting), the
  façade keeps doing for the C#'s copy beside the C++'s, until the retirement: otherwise the
  C# that still calls it finds an uninitialized library (libsodium-core's AES-GCM then looks
  unavailable).
- **Unsafe code** is plain C++: `fixed (byte* p = buffer)` is `buffer.data()` (nothing moves
  objects in C++, so there is nothing to pin), `stackalloc` a local `std::array` or a
  `std::vector`, pointer arithmetic stays. Reading one type through a pointer to another
  (`*(int*)p`) is undefined behavior in C++ and not in C#: copy the bytes with `std::memcpy`
  (or `std::bit_cast` from C++20). A pointer into a `fixed` string walks UTF-16 units, which a
  `std::string` hasn't: say in the symbol map what the C++ walks instead.
- **A deep copy through a serializer** (a `BinaryFormatter` writing the object into a
  `MemoryStream` and reading it back): copy the object graph. Each object the copy reaches is
  a new object (`std::make_shared<Order>(*order)` for each entry of a shared collection, not the
  pointer); a field marked `[NonSerialized]` gets its default value in the copy, as the note
  says.
- **Platform checks** (`RuntimeInformation.IsOSPlatform`, `NativeLibrary.Load`, a
  `SetDllImportResolver`) go: the C++ is built for its platform and links its libraries. Keep
  what the C# did for the platform the port builds on (Linux), and say in the symbol map what
  went.

## The symbol map

`cpp/symbol-map.toml` gets one entry per public and protected member of the unit's types:

```toml
[[symbol]]
csharp = "csharp:Zoo.Animal.Sound"
cpp = "zoo::Animal::sound"

[[symbol]]
csharp = "csharp:Zoo.Pen`1.Add(`0)"
cpp = ["zoo::Pen::add"]
note = "exported for Zoo.Dog and Zoo.Bird"
```

`csharp` is the member's symbol ID, as the index has it (the unit's checks name every one the
map lacks); `cpp` names what the C++ has, qualified as the C++ index has it
(`zoo::Animal::Animal` for a constructor, `zoo::operator+` for a friend operator). A member
with no C++ counterpart (an `Equals(object)` the façade keeps in C#) has `cpp = []` and a `note`
saying why.
