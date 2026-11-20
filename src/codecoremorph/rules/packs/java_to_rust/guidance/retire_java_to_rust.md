# Retire the Java

The last unit of a port from Java to Rust: every type is Rust behind its façade, and every Java
test has its Rust translation in `rust/test-map.toml`. The codemod deleted the Java sources and
tests, the POMs, the façades' support and generated declarations (`codecoremorph/interop`), and
each crate's bridge (`src/bridge`, its JNI glue included), its header and `cbindgen.toml`; the
crate is an `rlib` now, its root forbidding unsafe code. What is left is a Cargo workspace: the
library's crate, CodeCoreMorph's `codecoremorph_java`, and the translated tests.

What the unit does:

1. Build it: whatever still uses the bridge (a `crate::bridge::` path, a `#[unsafe(no_mangle)]`
   function outside it) moves to the library's own code or goes. No unsafe code is left.
2. Make every mapped Rust test pass with the Java gone. The final verification runs the Cargo
   build alone, `--locked` and offline.
3. Look at what the codemod listed as still building the Java (a CI configuration running
   `mvn`, a script, a README's instructions) and rewrite it for Cargo, or say in the symbol
   map's notes why it stays.

Nothing else changes: no new behavior, no refactoring beyond what removing the bridge needs.
