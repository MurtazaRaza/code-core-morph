# Port a Rust module to C behind its own Rust API

The unit moves one module of a Rust crate to C (the standard of `migration.target`). Nothing
else changes: the rest of the crate and the crate's tests keep calling the module's public items,
which stay as they are and become a **façade** over the C, so the tests decide whether the port
is right. Modules go leaves first by the references between them: what this module uses is
Rust still, or C already behind its own façade.

The C runs under AddressSanitizer and UndefinedBehaviorSanitizer in every test, from this unit
on: an out-of-bounds access, a use after free, a leak, signed overflow or a misaligned load
fails the test that reaches it, with the sanitizer's report. That, `-Wall -Wextra` and
clang-tidy's bug-finding checks are what replaces the borrow checker.

## What the codemod made

- `CMakeLists.txt` at the top (once): the project, `CODECOREMORPH_SANITIZE` (CodeCoreMorph's
  builds turn it on) and `add_subdirectory(c)`. Don't edit it.
- `c/CMakeLists.txt`: the static library `<crate>_c` with a marked list of the ported modules'
  sources, `-Wall -Wextra` and `m`. The unit's source is in the list already.
- `c/include/<crate>/<module>.h`: empty, with its include guard. **The module's C interface
  goes here**: the functions, types and constants the façade calls, all prefixed with the
  module (`stats_mean`, `stats_histogram`, `STATS_OK`).
- `c/src/<module>.c`: empty. **The port goes here**, in plain C of the target standard, with
  the module's private items as `static` functions.
- `src/<module>/sys.rs` (in the crate's directory): **generated** by bindgen from the module's
  header before every build; never edit it. It has the header's functions, types and
  constants with their C names: a C enum is an integer type with a constant per value named
  after the enum (`sys::stats_status_STATS_OK`).
- `build.rs` in the crate's directory and `c/cargo-link`: Cargo links the C library through
  them. Don't edit them: I-12 refuses any other build script.

The crate is the repository's root package, or a member of a Cargo workspace below it (such as
the `rust/<crate>/` a port from C left): the C, its CMake build and the maps are at the
repository's root either way, and the paths above under `src/` are the crate's.

The crate root (`src/lib.rs`) is in the unit when it only declares the modules, for its crate
attributes: a `#![forbid(unsafe_code)]` there stops every façade, since nothing can allow what
`forbid` forbids. Make it `#![deny(unsafe_code)]` and start each façade with
`#![allow(unsafe_code)]`, so unsafe code stays where the façades are. Leave the rest of the
crate root as it is.
- `Cargo.toml`: `[package.metadata.codecoremorph.c-modules]` names the module's header.
- `c/symbol-map.toml`: the map of Rust items to C names (below).

## What the unit does

1. Write the header and the C source. Every public item of the module needs its C counterpart.
2. Turn the module into the façade: keep every public item with exactly its signature, docs and
   derives, and make its body call the C through `sys` (declare `mod sys;` in the module, with
   `#[allow(non_camel_case_types, non_upper_case_globals, non_snake_case, dead_code,
   clippy::all)]`). Delete the Rust logic the C replaced, and the private helpers with it.
3. Keep the module's `#[cfg(test)] mod tests` as it is: it tests the façade until the tests
   unit translates it. A test of a private helper that moved to C tests the façade's public
   behavior instead, or waits for the tests unit.
4. Map each public item in `c/symbol-map.toml`.

## The mapping

- **Functions** become `<module>_<name>`. A `&[T]` or `&str` is a pointer and a length
  (`const double *xs, size_t n`); a `&mut T` a pointer the C writes; a `String` returned is a
  `char *` the caller frees with the module's `_free` function, or a buffer and its size.
- **`Option<T>` and `Result<T, E>`** become a status code (an `enum` with an `_OK` value) and an
  out-parameter for the value; the façade turns them back (`.then_some(out)`, `match status`).
  An `enum` of errors without data maps to the status values one for one.
- **A struct the module owns** (a `Vec`, a `Box`, a `String` inside) becomes an opaque C type
  (`typedef struct stats_histogram stats_histogram;`) with `_new` (NULL on failure) and `_free`,
  and the façade struct holds a `NonNull<sys::...>` and frees it in `Drop`. A plain-data struct
  (only numbers, `Copy`) may be a C struct both sides share through `sys`.
- **Enums with data** become a tagged union: an `enum` of tags and a struct of the tag and a
  `union` of the variants' fields.
- **Traits** become a struct of function pointers with a `void *self`; a `dyn Trait` is that
  struct and its context. A trait implemented by the module's own types becomes functions per
  type.
- **Generics**: one C function per type the crate uses the item with (the unit's note lists the
  call sites): `largest_circle`, `largest_rect`; or `void *` with an element size where that is
  the C idiom (`qsort`).
- **Closures** passed in become a callback and a `void *` context; the façade passes a
  trampoline `extern "C" fn` that casts the context back to the closure.
- **Iterators** returned become a cursor type with `_next`, or a filled array.
- **Integers** keep their widths (`uint32_t`, `int64_t`, `size_t` for `usize`). Rust's wrapping
  is explicit: do arithmetic that may overflow in unsigned types, or check it; never overflow a
  signed type. A Rust panic (an index out of bounds, `unwrap` on `None`) becomes a status code
  where the API has one, else `abort()`.
- **Floats**: `f64` is `double`, `f32` is `float`; `f64::total_cmp` and `NaN` handling must be
  written out (`isnan`).
- **Memory**: `malloc`/`calloc`/`free`, checked for NULL. Free everything you allocate: the
  leak checker runs at every test's exit.

## The façade

`unsafe` goes in the façade only, each block with a `// SAFETY:` comment saying why the call is
sound (the pointers' lengths, who owns what). Keep the façade thin: conversions and the call,
nothing the C could do.

## The symbol map

`c/symbol-map.toml` gets one entry per public item the unit lists:

```toml
[[symbol]]
rust = "stats::stats::mean"
c = "stats_mean"

[[symbol]]
rust = "stats::stats::Histogram"
c = ["stats_histogram", "stats_histogram_new", "stats_histogram_free"]
note = "the Rust struct owns a C histogram and frees it in Drop"
```

`c` names what the C index has (functions, types); an item with no C counterpart (a trait
implementation the façade keeps in Rust, such as `Debug`) has `c = []` and a `note` saying why.
