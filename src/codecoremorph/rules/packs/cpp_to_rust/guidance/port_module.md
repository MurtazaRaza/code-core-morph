# Port a C++ module to Rust behind a façade

The unit moves one C++ module (a header and its sources) to Rust. The C++ that uses the module,
and the project's C++ tests, don't change: they keep calling the module through its header, and
the tests decide whether the port is right. So the header keeps its public API, and its sources
become a **façade** that forwards every call to Rust through a C interface, the **bridge**.
Modules go leaves first: what this module uses is either Rust already or the C++ standard
library.

## What the codemod made

Under `rust/`, a Cargo workspace with one crate per C++ library target (`<target>_rs`), built
and linked into that target by CMake (the marked Corrosion block at the end of the top-level
`CMakeLists.txt`):

- `src/lib.rs`: `#![deny(unsafe_code)]`, `pub mod bridge;` (the one module that may use
  `unsafe`) and `pub mod <module>;` for each ported module. Keep the deny.
- `src/<module>.rs`: empty. **The port goes here, in safe, idiomatic Rust**, usable from Rust
  alone: Phase 14 retires the C++ and keeps this code.
- `src/bridge/<module>.rs`: empty. The module's C interface goes here: thin
  `#[no_mangle] pub extern "C" fn` wrappers (`pub unsafe extern "C" fn` when they dereference
  pointers) that convert C types and call `crate::<module>`. No logic of their own.
- `src/bridge/mod.rs`: the shared bridge types `<Crate>Str` (borrowed bytes) and `<Crate>Sink`
  (Rust appends bytes to a C++ object through a callback).
- `include/<crate>/bridge.h`: **generated** by cbindgen from the crate's `extern "C"` functions,
  `#[repr(C)]` types and `pub const`s before every build; never edit it. Run in your head what
  cbindgen writes: `c_long` → `long`, `usize` → `size_t`, `*const u8` → `const uint8_t*`,
  `bool` → `bool`, a `#[repr(C)] struct CalcRsX` → `typedef struct CalcRsX {...} CalcRsX;`, a
  plain `pub struct CalcRsX` (no `#[repr(C)]`) → an opaque `typedef struct CalcRsX CalcRsX;`.
- `include/<crate>/bridge.hpp`: C++98 helpers for the façades: `<crate>::str(s)` passes a
  `std::string` as `<Crate>Str`; `<crate>::into(out)` makes a sink that appends to a
  `std::string` or pushes strings onto a `std::vector<std::string>`.
- `rust/symbol-map.toml`: the map of C++ names to Rust items (below).

## Names

Bridge functions are `<crate>_<module>_<what>` (`calc_rs_rational_add`); bridge types are
`<Crate><Name>` (`CalcRsRational`); status constants `<CRATE>_<NAME>` (`CALC_RS_OK`). They are
C symbols, global across the whole program, so the prefix matters. Rust items inside the crate
follow Rust naming (`Rational::new`, `fn to_lower`).

## The façade

- **Keep every public declaration of the header**: same names, parameters, return types,
  `const`, `virtual`, default arguments, inline functions. Adding a declaration is fine (a copy
  constructor the class now needs). Private members may change.
- **Value types** (copyable, small, like a fraction): keep the C++ members as they are and pass
  them to Rust as a `#[repr(C)]` struct of the same fields; the header's inline accessors keep
  working. Rust returns the new fields; the façade stores them.
- **Classes with state Rust owns**: replace the private members with a handle,
  `struct CalcRsLexer* handle_;` (declare `struct CalcRsLexer;` in the header; don't include
  the bridge header from a public header). Rust makes it with `Box::into_raw` in `..._new`
  and frees it with `Box::from_raw` in `..._free`, which the destructor calls. A class that was
  copyable must stay copyable: add the copy constructor and assignment, using a `..._clone`.
- **Strings**: `std::string` holds bytes, not UTF-8. Take `<Crate>Str` and use
  `unsafe { s.bytes() }` (`&[u8]`); return strings through a `<Crate>Sink` (`sink.push(&bytes)`).
  Port string functions to `&[u8]`/`Vec<u8>` unless the C++ API promises text. A `const char*`
  argument becomes `std::ffi::CStr`.
- **Errors**: an exception can't cross the bridge. Rust returns `Result`; the bridge turns the
  error into a status code (`pub const` values, 0 for success) and writes results through
  out-pointers; the façade throws the same exception type with the same `what()` text as the
  C++ did. Keep which inputs fail, and how.
- **Virtual functions and C++ subclasses**: when Rust must call back into C++ objects (a C++
  subclass of a ported base class, defined in a test or elsewhere), pass a `#[repr(C)]` table
  of `extern "C"` function pointers plus a `*mut c_void` to the object, and have Rust wrap it in
  a type that implements the trait. Rust types of the ported hierarchy implement the same trait,
  so Rust code holds `Box<dyn Trait>` and never needs to know which side made a node.
- **Templates and inline functions** in a header only this unit's C++ still uses go with the
  unit: when the last use is gone, delete the header and its `#include`s.
- **Functions with C linkage** (`extern "C"` in a header): export them from the bridge with
  exactly the header's names and C signatures, and delete their C++ definitions; a type the
  header only declares (`typedef struct calc_session calc_session;`) becomes a Rust struct of
  that name (`#[allow(non_camel_case_types)]`, allowed in the bridge only), handed out with
  `Box::into_raw`.
- Free functions and static members that only call the bridge, simple accessors and empty
  bodies count as ported. A function that still has C++ logic doesn't, even when it calls
  another module's façade.

## Mapping C++ to Rust

- **Ownership**: `std::unique_ptr<T>` and an owning `T*` → `Box<T>`; `std::shared_ptr<T>` →
  `Rc<T>` (or `Arc<T>` across threads); a non-owning pointer → `&T`/`&mut T`, or `Option<&T>`
  when it may be null; `delete` and destructors → `Drop`, usually implied.
- **Containers and iterators**: `std::vector` → `Vec`, `std::map` → `BTreeMap` (ordered, like
  `std::map`), `std::unordered_map` → `HashMap`, `std::set` → `BTreeSet`, `std::pair` → a tuple;
  index loops → iterators where the meaning is the same.
- **Errors**: exceptions and status codes → `Result<T, E>` with an error enum that implements
  `Display` (its text is the C++ `what()` text); `assert` → `debug_assert!`.
- **Classes**: a class hierarchy → a trait (the virtual functions) and a type per class, or an
  `enum` when the set of classes is closed; templates → generics with the bounds the code uses;
  operator overloads → `std::ops` and `std::cmp` traits (`Add`, `Neg`, `PartialEq`, `Ord`), and
  `operator<<` on a stream → `fmt::Display`.
- **Integers**: `long` stays `c_long` across the bridge (32 bits on Windows, 64 elsewhere);
  `int` → `c_int`/`i32`; `size_t` → `usize`. Unsigned arithmetic wraps: use `wrapping_*` where it
  can overflow. Signed overflow was undefined in C++ and panics in a debug build of Rust; keep
  the same range of inputs, and note in the symbol map where behavior differs. `strtol` and
  friends saturate on overflow, Rust's `parse` fails: port the C++ behavior.
- **State and threads**: `static` state → `OnceLock`/`Mutex` statics; threads and locks →
  `std::thread`, `std::sync::Mutex`; namespaces → modules; macros → `const`, functions or
  `macro_rules!`; `printf`/iostreams → `format!` and `write!`.

## The symbol map

Add an entry to `rust/symbol-map.toml` for each function, type and constructor the module's
headers declare publicly (one entry covers all overloads of a name):

```toml
[[symbol]]
cpp = "calc::Rational::Rational"
rust = "calc_rs::rational::Rational::new"

[[symbol]]
cpp = "calc::operator-"
rust = ["calc_rs::rational::Rational::neg", "calc_rs::rational::Rational::sub"]

[[symbol]]
cpp = "calc::Rational::parse"
rust = "calc_rs::rational::Rational::parse"
note = "Saturates out-of-range numbers like strtol."
```

Each Rust path names an item of the crate: a function, a type, a method in an `impl` (trait
impls included), a variant. An entry with no Rust item needs a `note` that says why. Use `note`
for any behavior the port couldn't keep.

## What else the unit must not do

- No new crates from crates.io, no `build.rs`, no procedural macros.
- No `unsafe` outside the bridge, and no `#[allow(...)]` outside it either: fix what clippy
  reports.
- Don't edit the tests, the C++ files outside the unit, or the generated `bridge.h`.
