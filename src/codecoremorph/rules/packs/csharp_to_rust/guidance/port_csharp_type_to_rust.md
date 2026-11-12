# Port a C# type to Rust behind its own C# API

The unit moves one type of a C# library to Rust (the edition of `migration.target`), with the
types its note names: its nested types, a reference cycle, or a class with virtual members and
the classes that derive from it. Nothing else changes. The rest of the library and the
project's C# tests keep using the type, which keeps its C# API and becomes a **façade**: each
member's body calls the Rust through P/Invoke. So the tests decide whether the port is right.
Types go leaves first: what this type uses of the library is Rust already, behind its own
façade, so the Rust uses those Rust types directly.

What replaces the runtime's checks is Rust's: the borrow checker, no `unsafe` outside the
crate's bridge and its `native` module (`unsafe_in_bridge`), and clippy quiet about the unit's code (`lint_clean`, after
clippy's own fixes). The tests build in debug, so integer overflow panics; every export catches
panics and turns them into an exception (a panic never unwinds into .NET), but don't rely on
that: return the exception the C# threw.

## What the codemod made

- `rust/Cargo.toml` (the workspace), and `rust/codecoremorph_runtime/`: CodeCoreMorph's crate of
  .NET's `decimal` (`Decimal`, bit for bit, `checked_*` returning the exception .NET throws) and
  exceptions (`Exception`, `Result<T>`). CodeCoreMorph ships it: don't edit it.
- `rust/<name>_rs/`, the library's crate: `Cargo.toml` (a `cdylib`, which the façades load, and
  an `rlib`; **add no dependency**: the port builds offline), `cbindgen.toml` and
  `src/lib.rs` (`#![deny(unsafe_code)]`, the unit's `pub mod <unit>;` added, and `mod bridge;`
  the one module allowed unsafe code). Don't edit them but to declare modules.
- `rust/<name>_rs/src/<unit>.rs`: empty. **The port goes here**, safe Rust; modules for the C#
  namespaces where the library has several.
- `rust/<name>_rs/src/bridge/<unit>.rs`: empty. **The exports go here.** `src/bridge/ccm.rs`
  is the bridge's runtime (don't edit it).
- `rust/<name>_rs/include/<name>_rs/bridge.h`: the C header of the exports, which cbindgen
  writes from the bridge at every configure: never edit it. `codecoremorph_bridge.h` beside it
  is the bridge's C ABI.
- Beside the façade project's file: `CodeCoreMorph.Interop.cs`, the façades' support (don't
  edit it), and `CodeCoreMorph.Native.g.cs`, the C# declarations of the exports, **generated**
  from the header at every configure (never edit it). A project's own exceptions go in
  `CcmExceptions.cs` beside them (below).
- `rust/symbol-map.toml`: the map of C# members to Rust paths (below).
- Where the unit's types call a native library through P/Invoke: `src/native/` (below, *Native
  calls*).

## What the unit does

1. Port the types to Rust in `src/<unit>.rs`: every public and protected member, and what they
   need of the private ones.
2. Write the bridge: an export for each member the façade calls, in `src/bridge/<unit>.rs`.
3. Turn each C# type into its façade: keep every public and protected member with exactly its
   signature, modifiers, XML docs and attributes (I-6 compares them), and make its body call
   the export. Delete the C# logic and the private members the Rust replaced.
4. Map each public and protected member in `rust/symbol-map.toml`.

## The bridge

- Each export is `#[unsafe(no_mangle)] pub extern "C" fn <name>_<Type>_<member>(...) ->
  ccm_status` (`zoo_Animal_sound`, `shop_Money_add`), with its results in out parameters named
  `result` or `result_<part>`, and the receiver named `self_`. Its body is
  `ccm::guard(|| { ...; Ok(()) })` (`use super::ccm::{self, ccm_status, ...};`): an
  `Err(Exception)` or a panic becomes the status the façade throws from.
- It takes only: `i8` to `u64`, `bool`, `f32`, `f64`, `ccm_decimal`, `*const c_char` (a UTF-8
  string in, maybe null), `*mut *mut c_char` (a string out), handles (`*const`/`*mut` of the
  handle types), `*mut c_void` contexts, `ccm_release_fn`, function pointer types (below), out
  pointers to these, input arrays (`values_data: *const i32` beside `values_count: i32`, or
  `*const *const c_char` for strings) and arrays out (`result_data: *mut *mut u8` beside
  `result_count: *mut i32`). The generator refuses anything else, with the header's line.
- **Out parameters:** `ccm::put(result, value)`.
- **Strings:** `ccm::str_in(text, "param")?` where the C# throws `ArgumentNullException` for
  null, `ccm::opt_str(text)` where null is allowed (an `Option<String>`); out,
  `ccm::out_str(result, &s)` or `ccm::out_opt_str(result, s.as_deref())`.
- **Arrays** of numbers (a `byte[]`): in, `ccm::array(key_data, key_count, "key")?` gives a
  `Vec`, the façade passing `CcmInterop.Count(key)` (-1 for null, which throws
  `ArgumentNullException` as the C# check did; where the C# allows null, test the count); a
  string array is `ccm::strings(...)`. Out, `ccm::out_array(result_data, result_count,
  &bytes)?` copies a slice, and the façade reads it with `CcmInterop.TakeBytes(data, count)`
  (`TakeInts` for `i32`), which frees it: one call per array, not one per element.
- **Decimals:** `ccm::dec(d)?` gives a `Decimal`, `ccm::dec_out(x)` the `ccm_decimal`.
- **A class** is a handle: `pub struct zoo_Animal(());` with `impl Handle for zoo_Animal { type
  Target = Animal; }`, and its two exports, `zoo_Animal_release(handle: *mut zoo_Animal)`
  calling `ccm::release(handle)` and `zoo_Animal_key(handle: *const zoo_Animal) -> u64` calling
  `ccm::key(handle)`. A handle holds an `Arc` of its target:
  `ccm::to_handle::<zoo_Animal>(Arc::new(animal))` makes one, `ccm::from_handle(self_)?` reads
  one (a null handle is `NullReferenceException`, as C# throws), `ccm::from_handle_or_null`
  takes a null the C# allows (an `Option<Arc<_>>`), `ccm::required(handle, "param")?` throws
  `ArgumentNullException` for one it doesn't. The target is `Send + Sync`: .NET releases
  handles on its finalizer thread.
- **An interface or a hierarchy** is a trait, and its handle's target is the trait object
  (`type Target = dyn Speaker;`). A ported type that implements it converts to it:
  `zoo_Dog_as_Speaker(self_, result)` puts `ccm::to_handle::<zoo_Speaker>(dog.clone())` (the
  `Arc<Dog>` becoming an `Arc<dyn Speaker>`); keys are the data's address, so both handles of
  one object have the same key and the façade finds the same C# object. A virtual call
  dispatches in Rust. An export that names the object's type (an `i32` tag) lets the façade wrap
  an object that comes back in the right C# class.
- **A struct** crosses as its fields (`amount, currency` in; `result_amount, result_currency`
  out): the C# struct keeps its fields, and each member calls the Rust on a copy of them.
- **An enum** crosses as `i32`, the Rust an enum with the same discriminants.
- **A collection** the C# returns crosses through exports of its size and of an element
  (`_count`, `_at`), and the façade builds what the C# returned.
- **Calls into C#.** A delegate parameter becomes a function pointer type taking a context
  first and returning a status:

  ```rust
  pub type zoo_Pen_fold_fn = Option<
      unsafe extern "C" fn(context: *mut c_void, seed: i32, item: *mut zoo_Animal, result: *mut i32)
          -> ccm_status,
  >;
  ```

  The Rust calls it with `ccm::check_managed(unsafe { f(context, ...) })?` (`let f =
  ccm::callback(fold)?;` first: a null delegate is `NullReferenceException`), which turns a C#
  exception into `Exception::managed()`: pass it on with `?`, and the façade rethrows the C#
  exception itself. A handle the Rust passes to C# is a new one the C# owns
  (`ccm::to_handle(x.clone())`); a string C# returns is read and freed with `ccm::take_str`.
  An interface implemented in C# (a test double) reaches the Rust through a proxy: a struct in
  the bridge holding an `Arc<ManagedRef>` (`ManagedRef::new(context, release)`, which frees the
  GCHandle when the last clone goes) and the C# functions, implementing the trait over them,
  made by `<name>_<I>_from_managed(context, release, <fn per member>..., result)`, which calls
  `ccm::register_managed(Arc::as_ptr(&proxy) as usize, context)` (and the proxy's `Drop`,
  `unregister_managed`), so `<name>_<I>_managed(self_, result)` gives the C# object back
  (`ccm::managed_of(address)`). An event becomes `_subscribe_<event>(self_, context, release,
  fn, token: *mut i32)` and `_unsubscribe_<event>(self_, token)`: the façade keeps its C#
  subscribers and holds one native subscription while it has any; the Rust type holds
  `Arc<dyn Fn(&Arc<CartLine>) -> Result<()> + Send + Sync>` subscribers by token.
- **Exceptions.** Return what the C# threw, where it threw it, with the same message and
  parameter name: `Err(Exception::argument_null("name"))`,
  `Exception::argument_out_of_range("index", "...")`, `Exception::invalid_operation("an empty
  pen")`, `Exception::key_not_found(...)`, `Exception::null_reference()`; an empty message is
  .NET's own. The façade throws the .NET exception again. A project's own exception type stays
  a C# class (the tests catch it): the Rust returns `Exception::custom("Zoo.ZooException",
  message, [("field", value)])`, with its C# constructor's arguments by name, and
  `CcmExceptions.cs` constructs it from them:

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

  Put a function that builds it beside the type (`pub fn zoo_error(message: &str) ->
  Exception`), which the Rust keeps after the retirement.

## The façade

The façade calls `CcmNative.<export>` from `CodeCoreMorph.Native.g.cs` (`using
CodeCoreMorph.Interop;`), generated from the header at the next configure: `int` for `i32`,
`string` for `*const c_char`, `out IntPtr` for `*mut *mut c_char`, `CcmDecimal` for
`ccm_decimal`, `zoo_AnimalHandle` for a handle, a delegate of the type's name for a function
pointer. Each call goes through `CcmInterop.Check(...)`, which throws what the Rust reported.

- **A class** holds `internal readonly zoo_AnimalHandle Handle;`. Its constructor calls `_new`
  and `CcmObjects.Register(handle.Key, this)`; a private constructor takes a handle that came
  back; `internal static Animal FromHandle(zoo_AnimalHandle h)` returns
  `CcmObjects.Wrap(h, x => new Animal(x))`, so an object that comes back from Rust is the same
  C# object (`Assert.AreSame` holds); `internal static zoo_AnimalHandle HandleOf(Animal a)`
  returns `zoo_AnimalHandle.Null` for null. Other façades use these two.
- **Strings out** are read with `CcmInterop.TakeString(ptr)`, which frees them; decimals with
  `CcmDecimal.From(x)` and `.ToDecimal()`.
- **A delegate** passed to Rust is a `CcmNative` delegate built around the C# one, with the C#
  call inside `CcmInterop.Callback(...)`; keep it alive for the call (a local the call uses
  last). A context the Rust keeps is `CcmInterop.Pin(obj)` with `CcmInterop.Release`.
- **An interface** stays a C# interface, unchanged. Beside it, an internal static class gives
  the handle of any implementation: a ported class's own, or a proxy over a C# one.
- A hierarchy's façades stay a hierarchy: the base holds the handle, and a derived class passes
  its handle to the base's protected constructor. An `abstract` or `virtual` member keeps its
  modifiers, and each override's body calls the export.
- Static members call exports that use the Rust's static state; static fields the C# exposes
  become what they were (a `const` stays a `const`).

## Ownership

C# shares mutable objects freely; Rust needs an owner. In this order:

1. **Values and owned trees.** A struct, a class with one evident owner, a collection of them:
   plain Rust values, `Clone` where the C# copied them.
2. **A graph with back references** (a parent a child points to, a node list): indices into a
   `Vec` the owner holds, not references.
3. **Shared objects** (what C# hands out and keeps, what a test compares with `AreSame`): an
   `Arc<T>`, which is also what a handle holds. A type C# mutates while it is shared keeps its
   state behind its own lock (`std::sync::Mutex` fields, methods taking `&self`).
4. `Rc<RefCell<T>>` only inside a type that never crosses the bridge.

**Never hold a lock while calling out** (a closure, a subscriber, a trait object a C# class
implements): C# may call back into the same object (a subscriber that reads the cart while
`Add` raises its event), and a lock held across the call deadlocks. Copy what the call needs,
drop the guard, then call. The report counts each unit's shared cells (`Mutex`, `RwLock`,
`RefCell`, `Cell`); keep them few.

## The mapping

- Interfaces are traits (`pub trait Speaker: Send + Sync`), taking a shared object as `&Arc<T>`
  where a C# implementation needs the same object back; a hierarchy with overrides is a trait
  for its virtual members with a struct for the base's state, or an enum where the set of types
  is closed.
- Properties are methods (`name()`, `set_name()`); an indexer is `at()` or `get()`; operators
  are `std::ops` traits where they can't fail, methods returning `Result` where the C# could
  throw; `Equals` and `GetHashCode` are `PartialEq`, `Eq` and `Hash`; `ToString` is `Display`.
- `null` is `Option`: a reference C# lets be null is an `Option<Arc<T>>`, a string an
  `Option<&str>`, and code that dereferenced it returns `Exception::null_reference()` where the
  C# threw. Errors are `Result<T, Exception>` (`codecoremorph_runtime::Result<T>`) with `?`.
- `IDisposable` and `using` are `Drop` (or an explicit method where the C# observes a second
  `Dispose`); delegates are closures (`impl FnMut(..) -> Result<..>`, `Arc<dyn Fn ...>` where
  kept); events are subscribers with tokens; LINQ is iterators; `lock` is a `Mutex`; static
  mutable state is a `static` behind `OnceLock` or `Mutex`, never `static mut`.
- `List<T>`, `Dictionary<K,V>` and `HashSet<T>` are `Vec`, `HashMap` and `HashSet`. C#'s
  `Dictionary` enumerates in insertion order while nothing was removed, and tests often depend
  on it: where the C# enumerates one, keep the order (a `Vec` of keys beside the map; add no
  crate).
- `string` is `String`, UTF-8. C#'s `Length` and indices count UTF-16 units
  (`s.encode_utf16().count()`): the same for ASCII; otherwise note it in the symbol map.
- `decimal` is `codecoremorph_runtime::Decimal`: `checked_add` and its kin return the exception
  .NET throws (use them with `?`), `round`, `truncate`, `parse` and `Display` are .NET's
  (`ToString` in the invariant culture). Format numbers as the C# did in the invariant culture.
- **Integers.** The tests build in debug, where Rust's overflow panics: unchecked C# wraps, so
  arithmetic that may overflow is `wrapping_add` and its kin. Where the C# was `checked` (or
  the note says the project checks overflow, `CheckForOverflowUnderflow`: every integer `+`,
  `-`, `*`, conversion and negation outside `unchecked`), use `checked_*` and return
  `Exception::overflow("Arithmetic operation resulted in an overflow.")`. Integer division by
  zero, a bad index and a null dereference throw in C#: check, and return what the C# threw
  (`divide_by_zero()`, `ArgumentOutOfRangeException` for a `List`, `IndexOutOfRangeException`
  for an array).
- A generic type is a generic type, exported once per type argument in use (the unit's note
  lists them: `zoo_Pen_Dog_add`, `zoo_Pen_Bird_add`); the C# generic façade dispatches on
  `typeof(T)` to the right exports.

## What the C# runtime did that the Rust does itself

The unit's note names each of these where the C# has them (D-82).

- **Native calls.** A P/Invoke (`[DllImport("libsodium")] static extern int
  crypto_box_easy(...)`) becomes a call of the C function from the crate's `native` module,
  which the scaffold made when the unit's types call a native library: `src/native/mod.rs`, and
  `src/native/<library>_sys.rs` (`sodium_sys`), the library's declarations, which bindgen
  writes from its own header (`sodium.h`) at every configure: **never edit it**. The scaffold
  declares `#[allow(unsafe_code)] mod native;` in `lib.rs` and links the library. In
  `native/mod.rs`, write a safe function for each call the port makes, taking slices and
  returning a `Result` (`pub(crate) fn box_easy(message: &[u8], nonce: &[u8; 24], ...) ->
  Result<Vec<u8>>`), each `unsafe` block with a `// SAFETY:` comment saying why the pointers and
  lengths are valid (the slice's `as_ptr()` and `len()`, an output buffer sized as the header
  says), and check what the C function returns as the C# did. The port calls those functions;
  `native` is, with the bridge, the only module that may hold unsafe code, and the only one after
  the retirement. The C# types that only declare the functions (an `Interop` class) need no port:
  leave them, the retirement deletes them. A `ref`/`out` parameter is a `&mut` local passed as a
  pointer; an `IntPtr` the library returns (`sodium_version_string`) is the `*const c_char` or
  handle its header says (`CStr::from_ptr` for a string).
- **Two copies of a native library.** Until the retirement, the C# not ported yet still calls
  the library through its own P/Invokes, which load the package's copy (its NuGet native file),
  while the Rust links the toolchain's: two copies in one process, each with its own state.
  What the C# did to the library before using it (`sodium_init`, a global setting), the Rust
  does for its copy (once, behind a `std::sync::Once` or `OnceLock` in `native/mod.rs`, before
  its first call), and the façade keeps doing for the C#'s copy, until the retirement:
  otherwise the C# that still calls it finds an uninitialized library (libsodium-core's
  AES-GCM then looks unavailable).
- **Unsafe code** is safe Rust: `fixed (byte* p = buffer)` and pointer arithmetic are slices and
  indices, `stackalloc` a local array or a `Vec`, reading one type through a pointer to another
  (`*(int*)p`) `i32::from_le_bytes`. A pointer into a `fixed` string walks UTF-16 units, which a
  `String` hasn't: say in the symbol map what the Rust walks instead.
- **A deep copy through a serializer** (a `BinaryFormatter` writing the object into a
  `MemoryStream` and reading it back): copy the object graph. Each object the copy reaches is
  a new object (`Arc::new((**order).clone())` for each entry of a shared collection, not
  `Arc::clone`); a field marked `[NonSerialized]` gets its default value in the copy, as the
  note says.
- **Platform checks** (`RuntimeInformation.IsOSPlatform`, `NativeLibrary.Load`, a
  `SetDllImportResolver`) go: keep what the C# did for the platform the port builds on (Linux),
  and say in the symbol map what went.

## The symbol map

`rust/symbol-map.toml` gets one entry per public and protected member of the unit's types:

```toml
[[symbol]]
csharp = "csharp:Zoo.Animal.Sound"
rust = "zoo_rs::animal::Animal::sound"

[[symbol]]
csharp = "csharp:Zoo.Pen`1.Add(`0)"
rust = ["zoo_rs::pen::Pen::add"]
note = "exported for Zoo.Dog and Zoo.Bird"
```

`csharp` is the member's symbol ID, as the index has it (the unit's checks name every one the
map lacks); `rust` names the Rust item by its path in the crate (`zoo_rs::animal::Animal::new`
for a constructor; a trait impl's method belongs to the implementing type, so `ToString` as
`Display` is `zoo_rs::money::Money::fmt` and `operator +` as `Add` is
`zoo_rs::money::Money::add`). A member with no Rust
counterpart (an `Equals(object)` the façade keeps in C#) has `rust = []` and a `note` saying
why.
