# Porting Java to Rust

CodeCoreMorph ports a Java library (Java 8 or newer, one Maven module, tested with JUnit 3, 4 or
5) to a Cargo workspace one type at a time, then finishes the job: the tests become Rust tests,
and the Java goes, leaving Rust with no unsafe code. The branch builds and passes its tests at
every commit. It is the usual reason to leave the JVM, with memory safety kept without a garbage
collector; or the Java API stays, as a Java library whose implementation is Rust
(`keep_source_api`).

The port is [Porting Java to C++](porting-java-to-cpp.md)'s with Rust as the target: the same
units, the same Java façades over JNI, the same `CcmNative` declarations, and the same plain C
ABI in the bridge, so the Java side doesn't depend on the target. It is Phase 20 of the plan
(D-91, D-92), done in Docker. The benchmark, java-string-similarity 2.0.0, ported whole with
headless Claude Code: 43 of 43 units in 2 h 25 min, 27 Rust tests at the end, every Java test
mapped, no unsafe code. Over 116,024 seeded calls the Rust behind the façades returned what the
Java did, to the bit, on every call whose inputs UTF-8 can carry, but for one exception's message.

## What replaces the JVM's checks

Rust's own: the borrow checker, no `unsafe` outside each crate's bridge (`unsafe_in_bridge`),
and clippy quiet about each unit's code (`lint_clean`, after clippy's own fixes). Every export
catches panics, so none unwinds into the JVM: the panic becomes an exception the Java test
sees. The tests build in debug, where Rust's integer overflow panics and Java's wraps: the port
keeps Java's arithmetic with `wrapping_*` and `codecoremorph_java::lang` (`div_i32`, `shl_i32`,
`string_hash`, ...), or the test that overflows fails.

## What you need

- A Maven project whose `mvn test` passes, the types to port in one module.
- Docker, and the image for ports from Java: `scripts/build-image java-port`. It holds the JDKs
  and Maven, Rust (cargo, clippy, nextest), cbindgen and bindgen, and the C++ toolchain whose
  libclang reads the bridge's header.

## The manifest

```yaml
migration:
  source: {language: java, standard: java8, build_system: maven}
  target:
    language: rust
    standard: rust2021
    rules: [port_java_type_to_rust, port_java_tests_to_rust, retire_java_to_rust]
sandbox: {mode: docker}
```

## The workspace

`rust/` holds CodeCoreMorph's `codecoremorph_java` (Java's exceptions, `Exception` by Java type
name, and `lang`, Java's semantics where Rust's differ), which stays after the retirement, and
the library's crate `<name>_rs`: a `cdylib` the Java tests load and an `rlib`, built offline and
`--locked` before Maven. Its `src/bridge` holds the exports, one file per unit, and the JNI glue
the bridge `include!`s: `jni.rs` as CodeCoreMorph ships it, `jni_sys.rs` (bindgen's JNI types
from the JDK's `jni.h`) and `jni_g.rs` (a JNI function per export), both written at every
configure from cbindgen's header. A port adds no dependency.

## Ownership

Java shares mutable objects freely; Rust needs an owner. The guidance's order: values and owned
trees; a graph with back references as indices into a `Vec`; shared objects as `Arc`, a mutable
one with its state behind its own `Mutex` (a `synchronized` method is that lock), never held
while calling out to Java; `Rc<RefCell<T>>` only inside a type that never crosses the bridge.

## Limits

As [the port to C++](porting-java-to-cpp.md#limits)'s. After the retirement the workspace
forbids unsafe code (`#![forbid(unsafe_code)]`); with `keep_source_api` the bridge stays for the
Java façades.
