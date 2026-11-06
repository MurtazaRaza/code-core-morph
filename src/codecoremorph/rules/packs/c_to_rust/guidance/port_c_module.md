# Port a C source file to Rust behind its own headers

The unit moves one C source file of a library to Rust. Nothing else changes: the C files that
call it, and the project's tests, keep calling its functions through the same headers, and the
tests decide whether the port is right. Every C function already has C linkage, so there is no
façade: the crate **exports the file's functions and globals under their C names**, and the
Make build folds the crate into the library's archive, so whatever links the library links the
Rust code. Files go leaves first by the calls between them: what this file calls is Rust
already, or C that Rust calls through `sys`.

## What the codemod made

Under `rust/`, a Cargo workspace with one crate per C library (`<library>_rs`):

- `src/lib.rs`: `#![deny(unsafe_code)]`, `pub mod bridge;` (the one module that may use
  `unsafe`) and `pub mod <module>;` for each ported file. Keep the deny.
- `src/<module>.rs`: empty. **The port goes here, in safe, idiomatic Rust** over Rust types
  (`&[u8]`, `Vec<T>`, `Box<T>`, `Result`, enums), usable from Rust alone: the last units retire
  the C and keep this code.
- `src/bridge/<module>.rs`: empty. The file's C interface goes here: the functions other files
  use, as `#[no_mangle] pub unsafe extern "C" fn` with exactly the C name and signature (`pub
  extern "C" fn` when it dereferences no pointer). They convert between C types and Rust ones
  and call `crate::<module>`. Logic belongs in the module.
- `src/bridge/sys.rs`: **generated** by bindgen from the headers the unit keeps, before every
  build; never edit it. It has their types (`sys::sbuf`), constants, typedefs and the
  declarations of the functions still in C, which the bridge may call. Functions the crate
  exports leave it as they move to Rust: then call the Rust function instead. Read it for the
  exact names: a C enum is an integer type with a constant per value, named after the enum
  (`sys::wf_status_WF_OK`).
- `rust/symbol-map.toml`: the map of C names to Rust items (below).
- The Makefile: a marked block at its end builds the workspace, the library's archive rule
  folds the crate into the archive, and each link of the library gets `$(CODECOREMORPH_RUST_LIBS)`
  (Rust's native libraries). With automake, the block is at the end of the library's
  `Makefile.am`, and the library takes the crate in its `_LIBADD`, as one object. Don't edit the
  block.

## What the unit does

1. Port the file's logic to `src/<module>.rs`; its `static` functions become private Rust
   functions there.
2. Export every name the unit lists from `src/bridge/<module>.rs`, with the header's signature
   (`c_abi_kept` compares them).
3. Delete the C source file (and the headers only it includes, which the unit lists), and remove
   its object from the build files: the library's object list (`OBJS`), its compile rules and
   its dependency lines, or with automake the file in the library's `_SOURCES`
   (`libfoo_la_SOURCES`) of its `Makefile.am`. If `configure.ac`'s `AC_CONFIG_SRCDIR` names
   the file, configure no longer runs: name a file that stays (a kept header). Programs and
   tests that link the library keep linking it.
4. Map the exports in `rust/symbol-map.toml`.

## The C types and the bridge

- **Use the headers' types from `sys`**; define no copy of them. A struct that C code reads or
  writes field by field (`b->len`) must stay that struct: the bridge takes `*mut sys::sbuf`,
  turns it into Rust values for the module, and writes the fields back before it returns.
- **Memory that crosses the boundary** keeps the C allocator: what C code may `free()` or
  `realloc()`, or what Rust frees after C allocated it, is allocated and freed with `malloc`,
  `realloc` and `free`. Declare them in the bridge (`extern "C" { fn malloc(size: usize) -> *mut
  c_void; ... }`, from `std::ffi`) and use them there only. Memory only Rust touches is a `Vec`
  or a `Box`. An opaque handle the headers only declare (`typedef struct wf_table wf_table;`)
  can be a Rust struct, handed out with `Box::into_raw` and freed with `Box::from_raw`.
- **Globals** that other files read or write (`extern size_t wf_conf_min_length;`) stay data
  symbols of the same type: `#[no_mangle] pub static wf_conf_min_length: AtomicUsize` in the
  bridge (the same size as `size_t`; C reads it as a plain variable), with the module reading it
  through `load(Ordering::Relaxed)`. Plain `static mut` needs `unsafe` at every use.
- **Errors**: the module returns `Result<T, E>`; the bridge turns `Err` into the C status code
  or `NULL` and sets `errno` where the C did (`std::io::Error::last_os_error` reads it; set it
  through `extern "C" { fn __errno_location() -> *mut c_int; }` on Linux, in the bridge).
- Check every pointer the C contract allows to be `NULL`; one it doesn't is the caller's bug, as
  it was in C.

## Mapping C to Rust

- **Pointer and length** → a slice (`std::slice::from_raw_parts` in the bridge, `&[u8]` in the
  module); a `const char *` string → `CStr` (bytes, not necessarily UTF-8: keep them as
  `&[u8]` unless the C treats them as text).
- **`malloc`/`free` ownership inside the file** → `Box`, `Vec`, `String`, and `Drop`; a growable
  buffer → `Vec::reserve`/`push`; `memcpy`/`memmove` → `copy_from_slice`/`copy_within`.
- **`goto` cleanup** → early returns with `?` and values that clean up when dropped.
- **Error codes and `errno`** → `Result` inside, the codes at the boundary.
- **Macros** → `const`, functions (`#[inline]`), or `macro_rules!` when nothing else fits.
- **Global state** → `static` with `OnceLock`, `Mutex` or atomics.
- **Integers**: C's integer promotions and implicit conversions are explicit in Rust (`as`,
  `u32::from`, `try_from`). Unsigned arithmetic wraps in C: use `wrapping_add`/`wrapping_mul`
  where it can overflow (hashes, checksums); signed overflow was undefined and panics in a debug
  build. Shifts by the type's width or more are undefined in C and panic in Rust.
- **`qsort` and callbacks** → `sort_by`; keep the C comparison exactly, including how ties
  order (`sort_by` is stable, `qsort` isn't: if the output depends on the order of equal
  elements, sort by the same key the C did and check the tests). A callback with a `void *`
  context stays a C function pointer at the boundary (`sys::htab_visit`); inside, a closure.
- **No direct equivalent**: `setjmp`/`longjmp` → `Result` through the call chain; a union →
  an `enum` inside (the C union stays in `sys` at the boundary); bit fields → a struct with
  accessor methods over an integer (bindgen writes them for `sys` types); variadic functions →
  slices or a builder inside, and the C `...` signature stays C.
- **What stays C**: stable Rust can't define a function that takes `...` or a `va_list`
  (`printf`-style logging), so the unit doesn't export one; it says which stay. Keep them in the
  C file, reduced to formatting the arguments (`vsnprintf` into a buffer) and calling a function
  the bridge exports with the result: one the headers declare already (`log_write(level, msg)`),
  or a new one, which the C file then declares itself (the headers stay as they are). Delete
  everything else in the file; it stays in the build until the retirement. Rust code formats
  with `format!` and calls the Rust function.

## The symbol map

Add an entry to `rust/symbol-map.toml` for each name the unit exports:

```toml
[[symbol]]
c = "htab_add"
rust = "wf_rs::htab::Table::add"

[[symbol]]
c = "wf_conf_min_length"
rust = "wf_rs::bridge::conf::wf_conf_min_length"
note = "An exported C global; the module reads it through min_length()."
```

Each Rust path names an item of the crate: a function, a type, a method in an `impl`, a static.
An entry with no Rust item needs a `note` that says why. Use `note` for any behavior the port
couldn't keep.

## What else the unit must not do

- No crates from crates.io (no `libc`: declare the few C functions the bridge needs), no
  `build.rs`, no procedural macros.
- No `unsafe` outside the bridge, and no `#[allow(...)]` outside it either: fix what clippy
  reports.
- Don't edit the kept headers, the tests, the C files outside the unit, `sys.rs` or the marked
  block of the Makefile.
