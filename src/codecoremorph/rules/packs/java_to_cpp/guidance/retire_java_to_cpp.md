# Retire the Java

The last unit of a port from Java to C++: every type is C++ behind its façade, and every Java
test has its GoogleTest translation in `cpp/test-map.toml`. The codemod deleted the Java
sources and tests, the POMs, the façades' support and generated declarations
(`codecoremorph/interop`), and the bridge (`cpp/bridge`, its JNI glue included) with its block in
`cpp/CMakeLists.txt`. What is left is a CMake project: the library `<name>_cpp` and the
GoogleTest programs, built and tested under the sanitizers.

What the unit does:

1. Build it: whatever still includes a bridge header, or uses `codecoremorph_bridge.hpp`'s
   helpers outside the bridge, moves to the library's own code or goes.
2. Make every mapped GoogleTest test pass with the Java gone. The final verification runs the
   CMake build alone.
3. Look at what the codemod listed as still building the Java (a CI configuration running
   `mvn`, a script, a README's instructions) and rewrite it for the CMake build, or say in the
   symbol map's notes why it stays.

Nothing else changes: no new behavior, no refactoring beyond what removing the bridge needs.
