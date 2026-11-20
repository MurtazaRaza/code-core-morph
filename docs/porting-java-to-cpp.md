# Porting Java to C++

CodeCoreMorph ports a Java library (Java 8 or newer, one Maven module, tested with JUnit 3, 4 or
5) to C++17, 20 or 23 one type at a time, then finishes the job: the tests become GoogleTest
tests, and the Java goes, leaving a CMake project. The branch builds and passes its tests at
every commit. It is for native deployment without a JVM, embedding the code in a C++ host, and
code that has outgrown the JVM's memory or start-up. Or the Java API stays, as a Java library
whose implementation is C++ (`keep_source_api`).

Each ported type keeps its Java API as a **façade**: its members keep their signatures, and their
bodies call the C++ through JNI. So the rest of the library and the project's own JUnit tests
keep using the type unchanged, and they decide, unit by unit, whether the port is right.

The port is Phase 20 of the plan (D-91, D-92), done in Docker. A fixture written for it
(`fixtures/java/warehouse`: a value class, an enum with data, a hierarchy, a bounded generic, a
checked exception of its own, a listener and a lambda, 30 JUnit 4 tests) ports whole to both
targets, and with `keep_source_api` stays a Java library over each. The benchmark,
java-string-similarity 2.0.0 (22 library types, 24 JUnit 4 tests, 3.8k lines), ported whole with
headless Claude Code: 43 of 43 units in 2 h 33 min, its 39 GoogleTest tests passing under
AddressSanitizer and UndefinedBehaviorSanitizer, every Java test mapped. Over 116,024 seeded calls
the C++ behind the façades returned what the Java did, but for strings with a character outside
the BMP, whose shingles it kept as UTF-8; the guidance now keeps such pieces as UTF-16 units.

## What is lost, and what replaces it

The garbage collector goes, and with it the JVM's checks: array bounds, null dereferences,
`ArithmeticException` on integer division by zero, and reflection. In their place, every unit's
C++ must pass:

- the compiler with `-Wall -Wextra`, with no warning;
- clang-tidy's bug-finding checks (`bugprone-*`, `clang-analyzer-*`), with no finding;
- no owning raw pointer outside the bridge, and no cast between pointer types outside it;
- every test under AddressSanitizer and UndefinedBehaviorSanitizer, from the first unit on. The
  JUnit tests run the C++ inside Surefire's JVM, with the shared ASan runtime preloaded, so an
  out-of-bounds access, a use after free or a signed overflow aborts the test run, and the
  sanitizer's report is the failure.

Java defines arithmetic C++ doesn't: `int` and `long` wrap, `/` and `%` by zero throw, shifts
mask their count. The port keeps it, with `codecoremorph_java.hpp` (`ccm::java::add`, `div`,
`shl`, ...). A `hashCode`'s `31 * h` overflows on every call: written as C++'s `*`, it is a
sanitizer's report, not a different hash.

## What you need

- A Maven project whose `mvn test` passes, the types to port in one module.
- Docker, and the image for ports from Java: `scripts/build-image java-port` (`.bat` on
  Windows). It holds the JDKs, Maven, the Spoon index helper, and the C++ toolchain: clang,
  clang-tidy, CMake, Ninja and GoogleTest. The dependency step uses the network.

## The manifest

```yaml
migration:
  source: {language: java, standard: java8, build_system: maven}
  target:
    language: cpp
    standard: cpp17
    rules: [port_java_type_to_cpp, port_java_tests_to_cpp, retire_java_to_cpp]
sandbox: {mode: docker}
```

## How a port goes

1. **Types**, leaves first by the references between them (`port_java_type_to_cpp`). A
   reference cycle is one unit, and so is a class with overridable members with the library's
   classes that extend it; nested and inner types go with their outer type. Each unit writes
   the C++ (`cpp/src/<unit>.hpp` and `.cpp`), the exports the façade calls
   (`cpp/bridge/<unit>.h` and `.cpp`, a plain C ABI, every body in `bridge::guard`), and turns
   the Java into its façade. CodeCoreMorph writes `codecoremorph.interop.CcmNative` (the Java
   declarations) and `cpp/bridge/codecoremorph_jni.g.c` (their JNI functions) from the bridge
   headers at every configure; both are committed, so the branch builds without CodeCoreMorph.
2. **Tests**, one unit per test file (`port_java_tests_to_cpp`): each JUnit case becomes a
   GoogleTest test, mapped in `cpp/test-map.toml` from its `junit:` ID to its `gtest:` IDs; a
   Parameterized method's cases, which only the run names, by the IDs Surefire reported
   (`#countsPieces[pc]`). Both suites run until the retirement. A test the C++ can't have (a
   null where it takes a `std::string`) has an entry with no `gtest:` ID and a note.
3. **The retirement** (`retire_java_to_cpp`) deletes the Java, the POMs, the façades' support
   and the bridge with its JNI glue. The final verification is the CMake build alone, every
   translated test under the sanitizers.

## How Java maps

- Classes are C++ classes held by `std::shared_ptr` where Java shares them, values where they
  have one owner; an immutable value class crosses the bridge as its fields; interfaces are
  abstract classes; an enum is an `enum class` crossing as its ordinal.
- `null` is `std::optional` or an empty `shared_ptr`; exceptions are mirrors of Java's
  (`codecoremorph_exceptions.hpp`), which the façade throws again as the Java ones, the
  project's own made by `CcmExceptions.java`; `synchronized` is a mutex; lambdas are
  `std::function`.
- `ArrayList`, `HashMap` and `HashSet` are `std::vector`, `std::unordered_map` and
  `std::unordered_set`; `LinkedHashMap` keeps its insertion order.
- `String` is UTF-8 `std::string`; `length()` and indices counted UTF-16 units, the same for
  ASCII. Pieces the Java cuts by `char` and keeps (shingles, a map's keys) are UTF-16 units,
  `std::u16string`: half a surrogate pair has no UTF-8. `String.hashCode` is
  `ccm::java::string_hash`, over UTF-16 units.
- A generic type's parameter ports to its bound; one with no bound ports where every use gives
  it the same argument.

## Limits

The first version refuses, with the reason in the plan:

- reflection, serializers and annotation-driven frameworks (JAXB, Jackson, JPA, Spring,
  dependency injection), dynamic proxies;
- `java.math` (`BigDecimal`, `BigInteger`), until a runtime type is shipped;
- executors, futures, locks and new threads (`java.util.concurrent` beyond its containers);
- the library's own `native` methods;
- a generic parameter with no bound that the project instantiates with different arguments;
- a class a Java class staying Java (a test's subclass, an anonymous one included) extends and
  overrides: Java has no directors yet. A Mockito mock of a class (`mock`, `spy`, `@Mock`) is
  such a subclass, made at run time with no C++ object behind it, so a class the tests mock is
  refused too (a mock of an interface is an implementation, and ports);
- types in more than one Maven module; a type, or a test file, that uses a refused one.

While the façades last, and for good with `keep_source_api`, a string crosses the bridge as
UTF-8: an unpaired surrogate, which a Java `String` can hold and no UTF-8 can, crosses as `?`.

Programs (a class with `main`) port as types; a program's own unit, with a CMake program, comes
later. Native Windows runs come later: the port runs in Docker.
