# Port a Rust program to C

The unit moves a program of the crate (`src/main.rs`, `src/bin/<name>.rs`) to C. Its code
becomes a function of the C library, `<crate>_<program>_main(int argc, char **argv)`, and the
Rust program becomes a façade that calls it with its arguments and exits with its status, so
the tests that run the program (`env!("CARGO_BIN_EXE_<name>")`) run the C. A CMake program of
the same name calls the same function; it is the program once the Rust is retired.

## What the codemod made

- `c/include/<crate>/<program>.h`: declares `<crate>_<program>_main`.
- `c/src/<program>.c`: empty, in the library's source list. **The program goes here.**
- `c/src/<program>_entry.c` and the CMake program in `c/CMakeLists.txt`: `main` calls the
  function. Don't edit them.

## What the unit does

1. Write the program in `c/src/<program>.c`, on the C of the modules it uses (their headers).
   Keep its output byte for byte: the same text on stdout and stderr, the same exit status.
2. Turn the Rust program into the façade:

```rust
use std::ffi::{c_char, c_int, CString};

// Cargo links the C to the crate's library only: a program that uses nothing else of the
// library must name it, or the C isn't linked into the program.
extern crate stats;

extern "C" {
    fn stats_tool_main(argc: c_int, argv: *mut *mut c_char) -> c_int;
}

fn main() {
    let args: Vec<CString> = std::env::args_os()
        .map(|a| CString::new(a.into_encoded_bytes()).expect("no NUL in an argument"))
        .collect();
    let mut argv: Vec<*mut c_char> = args.iter().map(|a| a.as_ptr().cast_mut()).collect();
    argv.push(std::ptr::null_mut());
    // SAFETY: argv holds argc NUL-terminated strings and a final NULL, alive for the call.
    let status = unsafe { stats_tool_main(args.len() as c_int, argv.as_mut_ptr()) };
    std::process::exit(status);
}
```

Flush stdout in the C before returning (`fflush(stdout)`): `std::process::exit` doesn't flush
C's buffers for it.

A program in a package of its own (a workspace member that depends on the library's package)
does the same: `<crate>` is the library's crate, the façade names it with `extern crate`, and
the package's dependency on it stays.
