# Port a C++ module behind a C header to Rust

The unit replaces the C++ code that defines a C interface with a Rust crate that exports the
same interface. The header and the tests stay exactly as they are: the C++ tests call the Rust
code through the header, and they decide whether the port is right.

The `rust-crate` codemod has already made the crate (under `rust/`): `Cargo.toml` (a
`staticlib`), `Cargo.lock`, an empty `src/lib.rs`, and a marked block at the end of the
top-level `CMakeLists.txt` that imports the crate with Corrosion and links it to the C++
library that compiled the module. Then:

1. **Write the Rust implementation in `src/lib.rs`.** Export every function the header
   declares, with the same name and the same C signature:
   - `#[no_mangle] pub extern "C" fn name(...)`, or `pub unsafe extern "C" fn` when it
     dereferences raw pointers; give each `unsafe` function a `/// # Safety` section.
   - Map the C types exactly: `size_t` → `usize`, `int32_t` → `i32`, `uint32_t` → `u32`,
     `int64_t` → `i64`, `uint8_t` → `u8`, `int` → `std::os::raw::c_int`, `char` →
     `std::os::raw::c_char`, `T*` → `*mut T`, `const T*` → `*const T`, `void*` →
     `*mut std::ffi::c_void`. Keep `const` exactly where the header has it.
   - A type the header only declares (`typedef struct tk_ring tk_ring;`) is opaque to C:
     define it as a plain Rust struct with the same name, marked
     `#[allow(non_camel_case_types)]`, and hand it out as `Box::into_raw`; free it with
     `Box::from_raw`. A struct whose fields C code reads needs `#[repr(C)]` and the same fields
     in the same order.
2. **Keep the behavior, not just the signatures.** Port what the C++ code does, edge cases
   included: what it returns for NULL pointers, empty inputs and full or empty containers, and
   its arithmetic. Unsigned C++ arithmetic wraps, so use `wrapping_*` where it can overflow;
   integer conversions use `as` only where the C++ code converted the same way. A panic must
   not cross into C: don't `unwrap` on input C code controls.
3. **Remove the C++ module.** Delete its source files (`delete` edits), and take them out of
   the CMake target that compiled them (`add_library`). Leave the Corrosion block as the
   codemod wrote it, and leave the header alone.

Constraints:

- No new dependencies: builds run `--locked` and offline, so the crate uses the standard
  library only. No `build.rs`, no procedural macros.
- Only the crate's files and the CMake files change; the tests and the header never do.
- Code that isn't part of the C interface stays private (no `pub`, no `#[no_mangle]`).
