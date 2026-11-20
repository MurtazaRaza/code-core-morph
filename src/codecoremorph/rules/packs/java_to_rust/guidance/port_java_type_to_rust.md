# Port a Java type to Rust behind its own Java API

The unit moves one type of a Java library to Rust (the edition of `migration.target`), with the
types its note names: its nested and inner types, a reference cycle, or a class with
overridable members and the library's classes that extend it. Nothing else changes. The rest of
the library and the project's Java tests keep using the type, which keeps its Java API and
becomes a **façade**: each member's body calls the Rust through JNI. So the tests decide whether
the port is right. Types go leaves first: what this type uses of the library is Rust already,
behind its own façade, so the Rust uses those Rust types directly.

What replaces the JVM's checks is Rust's: the borrow checker, no `unsafe` outside the crate's
bridge (`unsafe_in_bridge`), and clippy quiet about the unit's code (`lint_clean`, after
clippy's own fixes). The tests build in debug, so integer overflow panics where Java wraps:
every export catches panics and turns them into an exception (a panic never unwinds into the
JVM), but the test that hit it fails. Keep Java's arithmetic (below).

## What the codemod made

- `rust/Cargo.toml` (the workspace), and `rust/codecoremorph_java/`: CodeCoreMorph's crate of
  Java's exceptions (`Exception`, by Java type name) and semantics (`lang`). CodeCoreMorph ships
  it: don't edit it.
- `rust/<name>_rs/`, the library's crate: `Cargo.toml` (a `cdylib`, which the façades load, and
  an `rlib`; **add no dependency**: the port builds offline), `cbindgen.toml` and `src/lib.rs`
  (`#![deny(unsafe_code)]`, the unit's `pub mod <unit>;` added, and `mod bridge;` the one module
  allowed unsafe code). Don't edit them but to declare modules.
- `rust/<name>_rs/src/<unit>.rs`: empty. **The port goes here**, safe Rust; modules for the
  Java packages where the library has several.
- `rust/<name>_rs/src/bridge/<unit>.rs`: empty. **The exports go here.** Beside it, the bridge's
  runtime `ccm.rs` and the JNI glue: `jni.rs` (as shipped), `jni_sys.rs` (bindgen's JNI types)
  and `jni_g.rs` (a JNI function per export), the last two written at every configure. Never
  edit any of them.
- `rust/<name>_rs/include/<name>_rs/bridge.h`: the C header of the exports, which cbindgen
  writes from the bridge at every configure: never edit it. `codecoremorph_bridge.h` beside it
  is the bridge's C ABI.
- In the façade module's sources, package `codecoremorph.interop`: `CcmInterop.java`, the
  façades' support (don't edit it); `CcmNative.java`, the Java declarations of the exports,
  **generated** from the header at every configure (never edit it); and `CcmExceptions.java`,
  where the project's own exceptions are made (below).
- `rust/symbol-map.toml`: the map of Java members to Rust paths (below).

## What the unit does

1. Port the types to Rust in `src/<unit>.rs`: every public, protected and package-private
   member other code of the library uses, and what they need of the private ones.
2. Write the bridge: an export for each member the façade calls, in `src/bridge/<unit>.rs`.
3. Turn each Java type into its façade: keep every public and protected member with exactly its
   signature, modifiers, Javadoc, annotations and `throws` (I-6 compares them), and make its body
   call the export. Delete the Java logic and the private members the Rust replaced.
4. Map each public and protected member in `rust/symbol-map.toml`.

## The bridge

- Each export is `#[unsafe(no_mangle)] pub extern "C" fn <name>_<Type>_<member>(...) ->
  ccm_status` (`shop_Money_plus`, `shop_Cart_add`; a nested type joins its names,
  `shop_Cart_ItemFilter_accepts`), with its results in out parameters named `result` or
  `result_<part>`, and the receiver named `this`. Its body is
  `ccm::guard(|| { ...; Ok(()) })` (`use super::ccm::{self, ccm_status, ...};`): an
  `Err(Exception)` or a panic becomes the status the façade throws from.
- It takes only: `i8` to `u64`, `bool`, `f32`, `f64`, `*const c_char` (a UTF-8 string in, maybe
  null), `*mut *mut c_char` (a string out), handles (`*const`/`*mut` of the handle types),
  `*mut c_void` contexts, `ccm_release_fn`, function pointer types (below), out pointers to
  these, input arrays (`values_data: *const i32` beside `values_count: i32`, or
  `*const *const c_char` for strings) and arrays out (`result_data: *mut *mut i32` beside
  `result_count: *mut i32`). The generator refuses anything else, with the header's line.
- From the header, CodeCoreMorph writes `CcmNative`: per export a Java method of its name, with
  `String` for `*const c_char`, `String[]` (one element) for a string out, `long` for a handle,
  `long[]` for a handle out, an array of one element for any other out parameter (`int[]`,
  `long[]`, `boolean[]`), a Java array for an input array (`null` crosses as a count of -1),
  `T[][]` for an array out, `Object` for a `*mut c_void` context, and nothing for a
  `ccm_release_fn` or a function pointer (the glue passes its own). A Java keyword as a
  parameter's name gets a `_` (`this` is `this_`).
- **Out parameters:** `ccm::put(result, value)`.
- **Strings:** `ccm::str_in(text, Some("message"))?` where the Java dereferences the string
  (null is `NullPointerException`, with the message the Java's own check gave, or `None`),
  `ccm::opt_str(text)` where null is allowed; out, `ccm::out_str(result, &s)`; a string a call
  into Java returns, `ccm::take_str(ptr)`.
- **Arrays:** in, `ccm::array(values_data, values_count)?` gives a `Vec` (a count of -1 is
  `NullPointerException`), `ccm::strings(data, count)?` a `Vec<Option<String>>`; out,
  `ccm::out_array(result_data, result_count, &values)`.
- **A class** is a handle: `pub struct shop_Cart { _private: [u8; 0] }` with `impl Handle for
  shop_Cart { type Target = Cart; }`, and its two exports written out (cbindgen expands no
  macro): `shop_Cart_release(handle: *mut shop_Cart)` calling `ccm::release(handle)` and
  `shop_Cart_key(handle: *const shop_Cart) -> u64` calling `ccm::key(handle)`. A handle holds an
  `Arc` of its target: `ccm::to_handle::<shop_Cart>(Arc::new(cart))` makes one,
  `ccm::from_handle(this)?` reads one (a null handle is `NullPointerException`, as Java
  throws), `ccm::from_handle_or_null` takes a null the Java allows. The target is
  `Send + Sync`: the façades' cleaner thread releases handles.
- **An interface or a hierarchy** is a trait, and its handle's target is the trait object
  (`type Target = dyn PriceRule;`). A ported type that implements it converts to it with an
  export of its own (`shop_Book_as_Item`). A virtual call dispatches in Rust.
- **An immutable value class** (final, every field private and final, of primitives, strings,
  enums and other such classes; `Money`) may cross as its fields instead
  (`cents, currency` in; `result_cents, result_currency` out): the Java class keeps its fields,
  and each member calls the Rust on a copy of them. Its constructor's checks run in an export
  of their own (`shop_Money_new(cents, currency)`), and a private constructor takes the fields
  the Rust made, checked already.
- **An enum** crosses as its ordinal (`i32`), the Rust an enum with the same discriminants. The
  Java enum keeps its constants, in their order, and drops what their constructors gave them.
- **A collection** the Java returns crosses through exports of its size and of an element
  (`_count`, `_at`, or one array out for numbers), and the façade builds what the Java returned.
  **A collection the Java takes and changes** crosses in, comes back, and the façade applies the
  change to the caller's object, only where the Java changed it.
- **Calls into Java.** A parameter of a functional interface (a lambda, an anonymous class)
  becomes a function pointer type taking a context first and returning a status:

  ```rust
  pub type shop_Cart_ItemFilter_accepts_fn = Option<
      unsafe extern "C" fn(context: *mut c_void, item: *const c_char, category: i32,
          result: *mut bool) -> ccm_status,
  >;
  ```

  The export takes `context: *mut c_void` and the function pointer; the Rust calls it with
  `ccm::check_managed(unsafe { f(context, ...) })?` (`let f = ccm::callback(accepts)?;` first),
  which turns a Java exception into `Exception::managed()`: pass it on with `?`, and the façade
  rethrows the Java exception itself, the same object. The context is the Java object the
  façade passes, implementing the generated interface `CcmNative.<type>`, whose
  `int call(...)` calls the Java one and returns `CcmInterop.OK`. An export that keeps the
  callback also takes `release: ccm_release_fn` after the context and holds both in an
  `Arc<ManagedRef>` (`ManagedRef::new(context, release)`, which deletes the JNI global reference
  when the last clone goes); one that only calls it during the call takes none.
  An interface implemented in Java (a test double) reaches the Rust through a proxy: a struct
  in the bridge holding the `Arc<ManagedRef>` and the functions, implementing the trait over
  them, made by `<name>_<I>_from_managed(context, release, <fn per member>..., result)`.
  In the interface's own file, after it, a package-private top-level class (`final class
  PriceRules`, whose `static long handleOf(PriceRule rule)` returns 0 for null) makes that
  handle: its context implements every type's interface of the members (a private static
  nested class), and the caller releases the handle after passing it. A unit edits only its own
  files and those under the rule's scope, so the class can't have a file of its own.
- **A Java class staying Java that extends a ported one and overrides** (a test's subclass, an
  anonymous one included) refuses the unit: a port from Java has no directors yet (D-92), so
  the Rust that calls the member would no longer run the override. It isn't yours to work
  around; the planner names it.
- **Exceptions.** Return what the Java threw, where it threw it, with the same message:
  `Err(Exception::illegal_argument(format!("quantity must be positive: {quantity}")))`,
  `Exception::null_pointer(Some("item"))`, `Exception::no_such_element(...)`,
  `Exception::new("java.lang.UnsupportedOperationException", ...)`. The façade throws the Java
  exception again (an empty message, or `Exception::bare(...)`, is Java's null). A project's own
  exception type stays a Java class (the tests catch it): the Rust returns
  `Exception::with_fields("shop.OutOfStockException", message, vec![("item".into(),
  item.into()), ("missing".into(), missing.to_string())])`, with its Java constructor's
  arguments by name, and `CcmExceptions.java` makes it from them:

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

  Put a function that builds it beside the type (`pub fn out_of_stock(item: &str, missing: i32)
  -> Exception`), which the Rust keeps after the retirement. A checked exception is thrown under
  the façade's unchanged `throws`.

## The façade

The façade calls `CcmNative.<export>` (`import codecoremorph.interop.CcmInterop;` and
`CcmNative`), generated from the header at the next configure, and passes each status to
`CcmInterop.check(...)`, which throws what the Rust reported.

- **A class** holds `private final long handle;`. Its constructor calls `_new` and
  `handle = CcmInterop.track(this, result[0], CcmNative::shop_Cart_release);`, which releases
  the handle once the façade is unreachable, and `CcmInterop.register(CcmNative.shop_Cart_key(handle), this)`
  where the Rust may hand the object back. A private constructor takes a handle that came back;
  `static Cart fromHandle(long h)` returns `CcmInterop.wrap(CcmNative.shop_Cart_key(h), h,
  CcmNative::shop_Cart_release, Cart::new)`, so an object that comes back from Rust is the same
  Java object (`assertSame` holds).
- **Out parameters** are arrays of one element the façade makes and reads.
- An `interface` stays a Java interface, unchanged; an `abstract` class keeps its modifiers. A
  hierarchy's façades stay a hierarchy: the base holds the handle, and a derived class passes
  its handle to a package-private constructor of the base's (a protected one would add to the
  API). The base's own constructors keep their signatures; one only a subclass could call (an
  abstract class's) throws `UnsupportedOperationException`, since no Java subclass that
  overrides is left to call it.
- A generic façade whose member returns its type parameter casts the object that comes back,
  `return (T) Product.fromHandle(result[0]);`: it is the one the Java gave the native code,
  and javac only notes the unchecked cast. Add no `@SuppressWarnings` (I-7 refuses a new one).
- `equals`, `hashCode` and `toString` are members like the others.

## Ownership

Java shares mutable objects freely; Rust needs an owner. In this order:

1. **Values and owned trees.** An immutable class, a class with one evident owner, a collection
   of them: plain Rust values, `Clone` where the Java copied them.
2. **A graph with back references** (a parent a child points to): indices into a `Vec` the
   owner holds, not references.
3. **Shared objects** (what Java hands out and keeps, what a test compares with `assertSame`):
   an `Arc<T>`, which is also what a handle holds. A type Java mutates while it is shared keeps
   its state behind its own lock (a `std::sync::Mutex` field, methods taking `&self`); a
   `synchronized` method is that lock.
4. `Rc<RefCell<T>>` only inside a type that never crosses the bridge.

**Never hold a lock while calling out** (a closure, a listener, a trait object a Java class
implements): Java may call back into the same object, and a lock held across the call
deadlocks. Copy what the call needs, drop the guard, then call.

## The mapping

- Interfaces are traits (`pub trait PriceRule: Send + Sync`); a hierarchy with overrides is a
  trait for its overridable members with a struct for the base's state, or an enum where the
  set of types is closed.
- Getters and setters are methods (`cents()`, `set_name()`); `equals` and `hashCode` are
  `PartialEq`, `Eq` and a `hash_code()` that computes what the Java did (below); `toString` is a
  method returning what the Java printed (`Display` where it can't fail).
- `null` is `Option`; errors are `Result<T, Exception>` with `?`; `AutoCloseable` is `Drop`;
  lambdas and functional interfaces are closures (`impl Fn(..) -> Result<..>`, `Arc<dyn Fn ...>`
  where kept); streams are iterators; listeners are subscribers with tokens.
- `ArrayList`, `HashMap` and `HashSet` are `Vec`, `HashMap` and `HashSet`. `LinkedHashMap` keeps
  insertion order (a `Vec` of entries, or of keys beside the map; add no crate); `TreeMap` is a
  `BTreeMap` with the Java comparator's order. A `HashMap`'s iteration order is Java's own: where
  the tests depend on it, keep it and note it in the symbol map.
- `String` is `String`, UTF-8. Java's `length()`, `charAt` and indices count UTF-16 units: the
  same for ASCII; otherwise `lang::string_length` and say what the Rust indexes by in the symbol
  map. Pieces the Java cuts by `char` and keeps (substrings as a map's keys, shingles, n-grams)
  can be half a surrogate pair, which UTF-8 can't hold: keep them as UTF-16 units (`Vec<u16>`),
  so a split pair stays the two values the Java had. `String.trim` removes what is at most U+0020 (`trim_matches(|c| c <= '\u{20}')`),
  `split` drops trailing empty strings: port what the Java's calls do.
- Number parsing and formatting keep Java's results: `Long.parseLong`'s accepted forms and its
  `NumberFormatException` message (`For input string: "x"`); `Double.toString`'s format differs
  from Rust's (`1.0E10`, `0.1`): write what the Java printed.
- **Integers.** Java's `int` and `long` arithmetic wraps: `wrapping_add` and its kin wherever a
  value may overflow (a `hashCode`'s `31 * h` always may); `Math.addExact` is
  `lang::add_exact_i32` and kin. Integer `/` and `%` by zero throw `ArithmeticException`, and
  `MIN_VALUE / -1` is `MIN_VALUE`: `lang::div_i32`, `rem_i32` (and `_i64`). Shifts mask their
  count: `lang::shl_i32`, `ushr_i32` for `>>>`. `(int)` of a `long` is `as i32`; of a `double`,
  `as i32` too (it saturates as Java's does). `Long.hashCode`, `Boolean.hashCode` and
  `String.hashCode` are `lang::long_hash`, `bool_hash`, `string_hash`. A bad index throws
  `IndexOutOfBoundsException` (an `ArrayList`) or `ArrayIndexOutOfBoundsException` (an array):
  check, and return what the Java threw.

## The symbol map

`rust/symbol-map.toml` gets one entry per public and protected member of the unit's types:

```toml
[[symbol]]
java = "java:shop.Money#plus(shop.Money)"
rust = "shop_rs::money::Money::plus"

[[symbol]]
java = "java:shop.Category#vatPercent()"
rust = "shop_rs::category::Category::vat_percent"
```

`java` is the member's symbol ID, as the index has it (the unit's checks name every one the map
lacks); `rust` names what the Rust has, by its path from the crate. A member with no Rust
counterpart (a getter the façade answers from its own fields) has `rust = []` and a `note`
saying why.
