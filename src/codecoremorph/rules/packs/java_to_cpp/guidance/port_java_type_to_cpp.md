# Port a Java type to C++ behind its own Java API

The unit moves one type of a Java library to C++ (the standard of `migration.target`), with the
types its note names: its nested and inner types, a reference cycle, or a class with
overridable members and the library's classes that extend it. Nothing else changes. The rest of
the library and the project's Java tests keep using the type, which keeps its Java API and
becomes a **façade**: each member's body calls the C++ through JNI. So the tests decide whether
the port is right. Types go leaves first: what this type uses of the library is C++ already,
behind its own façade, so the C++ uses those C++ classes directly.

The C++ runs under AddressSanitizer and UndefinedBehaviorSanitizer in every test, inside the
JVM Surefire forks: an out-of-bounds access, a use after free, signed overflow or a misaligned
load aborts the test run with the sanitizer's report. That, `-Wall -Wextra` and clang-tidy's
bug-finding checks are what replace the JVM's checks.

The unit's checks (`lint_clean`) hold the C++ to that: no warning, clang-tidy's bug-finding
checks quiet, and outside `cpp/bridge` no `new` or `delete` whose pointer no smart pointer owns
and no cast between pointer types. Keep `CODECOREMORPH_SANITIZE` in the top-level
`CMakeLists.txt`.

## What the codemod made

- `CMakeLists.txt` at the top and `cpp/CMakeLists.txt` (the first unit made them): the static
  library `<name>_cpp` of the ported code and the shared library `<name>_native` of the bridge,
  which the JVM loads, each with a marked list of sources. The unit's sources are in the lists
  already. Don't edit them otherwise.
- `cpp/include/codecoremorph_exceptions.hpp` (`ccm::Exception` and the mirrors of Java's
  exceptions), `cpp/include/codecoremorph_java.hpp` (Java's semantics where C++'s differ,
  below), `cpp/bridge/codecoremorph_bridge.{h,hpp,cpp}` (the bridge's types and helpers) and
  `cpp/bridge/codecoremorph_jni.{h,c}` (the JNI glue's fixed part). CodeCoreMorph ships them:
  don't edit them.
- `cpp/src/<unit>.hpp` and `cpp/src/<unit>.cpp`: empty. **The port goes here**, in a namespace
  from the Java package (`shop` is `shop`, `org.example.ledger` is `org::example::ledger`).
- `cpp/bridge/<unit>.h` and `cpp/bridge/<unit>.cpp`: empty. **The exports go here**: the C
  declarations the façade calls, and their C++ bodies.
- In the façade module's sources, package `codecoremorph.interop`: `CcmInterop.java`, the
  façades' support (don't edit it); `CcmNative.java`, the Java declarations of the exports, and
  `cpp/bridge/codecoremorph_jni.g.c`, their JNI functions, both **generated** from the bridge
  headers at every configure (never edit them); and `CcmExceptions.java`, where the project's
  own exceptions are made (below).
- `cpp/symbol-map.toml`: the map of Java members to C++ names (below).

## What the unit does

1. Port the types to C++ in `cpp/src/<unit>.hpp` and `.cpp`: every public, protected and
   package-private member other code of the library uses, and what they need of the private
   ones.
2. Write the bridge: an export for each member the façade calls, declared in
   `cpp/bridge/<unit>.h` and defined in `cpp/bridge/<unit>.cpp`.
3. Turn each Java type into its façade: keep every public and protected member with exactly its
   signature, modifiers, Javadoc, annotations and `throws` (I-6 compares them), and make its body
   call the export. Delete the Java logic and the private members the C++ replaced.
4. Map each public and protected member in `cpp/symbol-map.toml`.

## The bridge

- Each export is `CCM_EXPORT ccm_status <name>_<Type>_<member>(...)` in the header's
  `extern "C"` block (`shop_Money_plus`, `shop_Cart_add`; a nested type joins its names,
  `shop_Cart_ItemFilter_accepts`), with its results in out parameters named `result` or
  `result_<part>`. Its body is `return bridge::guard([&] { ... });` (with
  `namespace bridge = ccm::bridge;`), so no exception leaves it: the status carries it to the
  façade. Overloads get a suffix that says which (`shop_Money_parse_text`).
- It takes only: fixed-width integers, `bool`, `float`, `double`, `const char *` (a UTF-8
  string in, maybe null), `char **` (a string out), handles, `void *` contexts,
  `ccm_release_fn`, function pointer typedefs, out pointers to these, input arrays
  (`const int32_t *values_data` beside `int32_t values_count`, or `const char **` for strings)
  and arrays out (`int32_t **result_data` beside `int32_t *result_count`). The generator refuses
  anything else, with the header's line.
- From the header, CodeCoreMorph writes `CcmNative`: per export a Java method of its name, with
  `String` for `const char *`, `String[]` (one element) for `char **`, `long` for a handle,
  `long[]` for a handle out, an array of one element for any other out parameter (`int[]`,
  `long[]`, `boolean[]`), a Java array for an input array (`null` crosses as a count of -1),
  `T[][]` for an array out, `Object` for a `void *` context, and nothing for a `ccm_release_fn` or
  a function pointer (the glue passes its own). A Java keyword as a parameter's name gets a `_`.
- **Strings:** `bridge::str(text, "message")` where the Java dereferences the string (null throws
  `NullPointerException`, with the message the Java's own check gave, if it gave one),
  `bridge::opt_str(text)` where null is allowed; out, `bridge::out_str(result, s)`.
- **Arrays** of numbers: in, `bridge::array(values_data, values_count)` gives a `std::vector`
  (a count of -1 throws `NullPointerException`); out, `bridge::out_array(result_data,
  result_count, values)`.
- **A class** is a handle: `CCM_HANDLE(shop_Cart);` in the header's `extern "C"` block,
  `CCM_HANDLE_CLASS(shop_Cart, shop::Cart)` after it (C++ only, with a forward declaration of
  the class), and `CCM_HANDLE_EXPORTS(shop_Cart)` in the `.cpp`. A handle holds a
  `std::shared_ptr`: `bridge::to_handle<shop_Cart>(std::make_shared<shop::Cart>(...))` makes
  one, `bridge::from_handle(self)` reads one (a null handle throws `NullPointerException`, as
  Java does), and `bridge::from_handle_or_null` takes a null the Java allows. A hierarchy shares
  its root's handle type: each constructor returns the root's handle to the derived object,
  virtual calls dispatch in C++, and an export of a derived class's own member casts
  (`std::dynamic_pointer_cast<shop::Book>`). An export that names the object's class (an
  `int32_t` tag) lets the façade wrap an object that comes back in the right Java class.
- **An immutable value class** (final, every field private and final, of primitives, strings,
  enums and other such classes; `Money`) may cross as its fields instead
  (`cents, currency` in; `result_cents, result_currency` out): the Java class keeps its fields,
  and each member calls the C++ on a copy of them. Its constructor's checks run in an export
  of their own (`shop_Money_new(cents, currency)`), and a private constructor takes the fields
  the C++ made, checked already. A null of such a class can't cross as fields: where the Java
  passes or returns one, add a `bool` that says so.
- **An enum** crosses as its ordinal (`int32_t`), the C++ an `enum class` with the same order.
  The Java enum keeps its constants, in their order, and drops what their constructors gave
  them (it is the C++'s now): `vatPercent()` calls an export with `ordinal()`, and a lookup
  returns `values()[result[0]]`.
- **A collection** the Java returns crosses through exports of its size and of an element
  (`_count`, `_at`, or one array out for numbers), and the façade builds what the Java returned
  (an `ArrayList`, `Collections.unmodifiableList`, an array). **A collection the Java takes and
  changes** (a `Map` it puts into) crosses in, comes back, and the façade applies the change to
  the caller's object, only where the Java changed it.
- **Calls into Java.** A parameter of a functional interface (a lambda, an anonymous class)
  becomes a function pointer typedef taking a `void *context` first and returning a status
  (`typedef ccm_status (*shop_Cart_ItemFilter_accepts_fn)(void *context, const char *item, int32_t category, bool *result);`),
  and the export takes `void *context` and the function pointer; the C++ calls it through
  `bridge::check_managed(fn(context, ...))`, which turns a Java exception into
  `bridge::ManagedException`, and the façade rethrows the Java exception itself, the same
  object. The context is the Java object the façade passes: an object implementing the
  generated interface `CcmNative.<typedef>`, whose `int call(...)` calls the Java one and
  returns `CcmInterop.OK`. An export that keeps the context (stores the callback) also takes a
  `ccm_release_fn release` after it, and holds both in a `bridge::ManagedRef`; one that only
  calls it during the call takes none, and the glue lets go of it afterwards.
  An interface implemented in Java (a test double) reaches the C++ through a proxy: an export
  `<name>_<I>_from_managed(void *context, ccm_release_fn release, <fn per member>...,
  <name>_<I> **result)` makes a C++ class implementing the interface over a
  `bridge::ManagedRef`. In the interface's own file, after it, a package-private top-level
  class (`final class PriceRules`, whose `static long handleOf(PriceRule rule)` returns 0 for
  null) makes that handle: its context implements every typedef's interface of the members (a
  private static nested class), and the caller releases the handle after passing it
  (`CcmNative.<name>_<I>_release`). A unit edits only its own files and those under the rule's
  scope, so the class can't have a file of its own.
- **A Java class staying Java that extends a ported one and overrides** (a test's subclass, an
  anonymous one included) refuses the unit: a port from Java has no directors yet (D-92), so
  the C++ that calls the member would no longer run the override. It isn't yours to work
  around; the planner names it.
- **Exceptions.** The C++ throws the mirror of what the Java threw, where it threw it, with the
  same message: `ccm::IllegalArgumentException("quantity must be positive: 0")`,
  `ccm::NullPointerException("item")`, `ccm::NoSuchElementException(...)`. The façade throws the
  Java exception again (an empty message is Java's null). A project's own exception type stays a
  Java class (the tests catch it): its C++ class derives from `ccm::Exception` (or the mirror of
  its Java base), overrides `java_type()` (`"shop.OutOfStockException"`) and `fields()` (its Java
  constructor's arguments, by name), and `CcmExceptions.java` makes it from them:

  ```java
  static Throwable create(CcmInterop.CcmError error) {
      switch (error.type) {
          case "shop.OutOfStockException":
              return new shop.OutOfStockException(error.field("item"),
                  Integer.parseInt(error.field("missing")));
          default:
              return null;
      }
  }
  ```

  A checked exception is thrown under the façade's unchanged `throws`.

## The façade

The façade calls `CcmNative.<export>` (`import codecoremorph.interop.CcmInterop;` and
`CcmNative`), generated from your header at the next configure, and passes each status to
`CcmInterop.check(...)`, which throws what the C++ reported.

- **A class** holds `private final long handle;`. Its constructor calls `_new` and
  `handle = CcmInterop.track(this, result[0], CcmNative::shop_Cart_release);`, which releases
  the handle once the façade is unreachable, and `CcmInterop.register(CcmNative.shop_Cart_key(handle), this)`
  where the C++ may hand the object back. A private constructor takes a handle that came back;
  `static Cart fromHandle(long h)` returns `CcmInterop.wrap(CcmNative.shop_Cart_key(h), h,
  CcmNative::shop_Cart_release, Cart::new)`, so an object that comes back from C++ is the same
  Java object (`assertSame` holds), and `static long handleOf(Cart c)` returns `0` for null.
  Other façades of the library use these two (package-private).
- **Out parameters** are arrays of one element the façade makes and reads:
  `long[] cents = new long[1]; String[] currency = new String[1];`.
- An `interface` stays a Java interface, unchanged; an `abstract` class keeps its modifiers.
  A hierarchy's façades stay a hierarchy: the base holds the handle, and a derived class passes
  its handle to a package-private constructor of the base's (a protected one would add to the
  API). The base's own constructors keep their signatures; one only a subclass could call (an
  abstract class's) throws `UnsupportedOperationException`, since no Java subclass that
  overrides is left to call it.
- A generic façade whose member returns its type parameter casts the object that comes back,
  `return (T) Product.fromHandle(result[0]);`: it is the one the Java gave the native code,
  and javac only notes the unchecked cast. Add no `@SuppressWarnings` (I-7 refuses a new one).
- `equals`, `hashCode` and `toString` are members like the others: the façade calls exports
  for them (`equals(Object)` first checks the class as the Java did). Static members call
  exports that use the C++ static state; a `static final` constant the Java exposes stays.

## The mapping

- A class is held by `std::shared_ptr` where the Java shares it, and by `std::unique_ptr` or by
  value where it has one evident owner; an immutable value class is a value type. No owning raw
  pointer outside the bridge.
- Interfaces are abstract classes (pure virtual functions, a virtual destructor); an abstract
  class keeps its abstract members pure virtual; `final` is `final`. Every Java method that a
  ported subclass overrides is `virtual` in C++.
- Getters and setters stay getters and setters (`cents()`, `set_name()`); `equals` and
  `hashCode` are `operator==` and a `hash_code()` that computes what the Java did (below);
  `toString` is `to_string()`.
- `null` is `std::optional` or an empty `shared_ptr`; try-with-resources and `AutoCloseable` are
  destructors; lambdas and functional interfaces are `std::function`; streams are `<algorithm>`
  (and ranges from C++20); `synchronized` is a `std::mutex` with `std::lock_guard`.
- `ArrayList`, `HashMap` and `HashSet` are `std::vector`, `std::unordered_map` and
  `std::unordered_set`. `LinkedHashMap` keeps insertion order (a vector of keys beside the map,
  or a vector of entries); `TreeMap` is `std::map` with the Java comparator. A `HashMap`'s
  iteration order is Java's own: where the Java iterates one and the tests depend on the order,
  keep it and note it in the symbol map.
- `String` is UTF-8 `std::string`. Java's `length()`, `charAt` and indices count UTF-16 units:
  the same for ASCII; otherwise use `ccm::java::string_length` and say what the C++ indexes by
  in the symbol map. Pieces the Java cuts by `char` and keeps (substrings as a map's keys,
  shingles, n-grams) can be half a surrogate pair, which UTF-8 can't hold: keep them as UTF-16
  units (`std::u16string`), so a split pair stays the two values the Java had. `String.trim` removes what is at most U+0020, `split` drops trailing empty
  strings, `equalsIgnoreCase` maps case per character: port what the Java's calls do.
- Number parsing and formatting keep Java's results: `Long.parseLong`'s accepted forms and its
  `NumberFormatException` message (`For input string: "x"`), `Integer.toString`;
  `Double.toString`'s format differs from C++'s (`1.0E10`, `0.1`): write what the Java printed.

## Java's semantics the C++ keeps

`codecoremorph_java.hpp` has them (`namespace ccm::java`):

- `int` and `long` arithmetic wraps in Java and is undefined on overflow for signed C++
  integers: `ccm::java::add`, `sub`, `mul`, `neg` wherever a value may overflow (a `hashCode`'s
  `31 * h` always may), and `add_exact`/`mul_exact` where the Java called `Math.addExact`.
- Integer `/` and `%` by zero throw `ArithmeticException("/ by zero")`, and
  `Integer.MIN_VALUE / -1` is `MIN_VALUE`: `ccm::java::div` and `rem`.
- Shifts mask their count (`x << 33` is `x << 1` for an `int`): `shl`, `shr`, and `ushr` for
  `>>>`.
- `(int)` of a `long` keeps the low bits: `to_int`; `Long.hashCode` is `long_hash`,
  `Boolean.hashCode` `bool_hash`, `String.hashCode` `string_hash` (over UTF-16 units).
- A bad index throws `IndexOutOfBoundsException` (an `ArrayList`) or
  `ArrayIndexOutOfBoundsException` (an array): check, and throw the mirror the Java threw.
- `float` and `double` are IEEE in both; `(int)` of a `double` saturates in Java (NaN is 0) and
  is undefined out of range in C++: clamp as Java does.

## The symbol map

`cpp/symbol-map.toml` gets one entry per public and protected member of the unit's types:

```toml
[[symbol]]
java = "java:shop.Money#plus(shop.Money)"
cpp = "shop::Money::plus"

[[symbol]]
java = "java:shop.Category#vatPercent()"
cpp = "shop::vat_percent"
note = "an enum's member is a free function of the enum class"
```

`java` is the member's symbol ID, as the index has it (the unit's checks name every one the
map lacks); `cpp` names what the C++ has, qualified as the C++ index has it
(`shop::Money::Money` for a constructor). A member with no C++ counterpart (a getter the
façade answers from its own fields) has `cpp = []` and a `note` saying why.
