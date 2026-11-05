# Raise the Java version

The POMs already name the target (a codemod set `maven.compiler.source`/`target`/`release`,
`java.version` or the compiler plugin's settings), and OpenRewrite's migration recipe has
already replaced what it knows how to. The build now runs on the target's JDK. Your job is to
make the code compile and behave the same there. Change behavior nowhere.

Only the sections between the source and the target apply. A Java 8 project moving to 21
needs every section up to "17 to 21".

## What OpenRewrite leaves

- It replaces a call but doesn't always add the import: `Base64.getEncoder()` without
  `import java.util.Base64;`. Add the import; don't qualify the name instead.
- It adds dependencies for the removed Java EE modules (`jakarta.xml.bind:jakarta.xml.bind-api`
  2.3.x keeps the `javax.xml.bind` package names). Leave them, and their versions.
- It raises Maven plugin versions that older releases can't run on the new JDK. Leave those.

## 8 to 11

- The Java EE and CORBA modules are gone from the JDK: `javax.xml.bind` (JAXB),
  `javax.xml.ws` (JAX-WS), `javax.activation`, `javax.annotation` (`@PostConstruct`,
  `@Generated`), `javax.jws`, `org.omg.CORBA`. If the code uses one only for something the JDK
  now has (`DatatypeConverter.printHexBinary` and `parseBase64Binary`, `printBase64Binary`),
  replace it with the JDK's own API: `java.util.HexFormat` is Java 17, so on 11 write the hex
  loop (`String.format("%02X", b)` per byte) or keep JAXB through its dependency;
  `java.util.Base64` for Base64. Otherwise add the API's own artifact to the POM with a fixed
  version (`jakarta.xml.bind:jakarta.xml.bind-api:2.3.3` and
  `org.glassfish.jaxb:jaxb-runtime:2.3.9`, `javax.annotation:javax.annotation-api:1.3.2`).
  Keep the output byte for byte: `printHexBinary` writes upper case.
- JDK internals are hidden: `sun.misc.BASE64Encoder` and `BASE64Decoder` →
  `java.util.Base64` (`getEncoder().encodeToString`; the old encoder wrapped lines every 76
  characters, `getMimeEncoder()` does the same with `\r\n` instead of `\n`, so check what the
  tests compare); `sun.reflect.Reflection` → `StackWalker`.
- `_` is a keyword: rename a variable or parameter called `_`.
- `Thread.destroy()`, `Thread.stop(Throwable)` and `Runtime.runFinalizersOnExit` are removed.
- The system class loader is no longer a `URLClassLoader`: code that casts it to add jars
  must use its own `URLClassLoader` instead.

## 11 to 17

- JDK internals are strongly encapsulated: reflection into `java.*` private fields
  (`setAccessible(true)` on a JDK class) fails at run time with `InaccessibleObjectException`.
  Use the public API; `--add-opens` is not a migration.
- Nashorn (`jdk.nashorn`, `ScriptEngineManager().getEngineByName("nashorn")`) is removed, and
  so is RMI activation (`java.rmi.activation`).
- `SecurityManager` is deprecated for removal; leave existing uses alone unless they break.

## 17 to 21

- `Thread.stop()`, `suspend()` and `resume()` throw `UnsupportedOperationException`.
- `new URL(String)` is deprecated: `URI.create(s).toURL()` (it rejects some strings `new URL`
  accepted, so keep the old call where a test passes such a string).
- Sequenced collections: `List`, `Deque` and `LinkedHashSet` gained `getFirst()`, `getLast()`,
  `reversed()`, `addFirst()`, `removeFirst()` and friends. A subclass method of the same name
  with another return type no longer compiles: rename it, with its callers.
- `finalize()` is deprecated for removal; it still runs, so leave it unless it breaks.

## 21 to 25

- The `SecurityManager` can no longer be installed: `System.setSecurityManager` throws.
- `sun.misc.Unsafe`'s memory-access methods warn at run time; nothing breaks yet.

## Deprecation warnings

`new Integer(5)`, `new Long(x)` and the like are deprecated for removal: `Integer.valueOf(5)`.
`==` between two `new Integer(…)` was always false, while two `valueOf` boxes of a value from
-128 to 127 are the same object: where code compares boxes with `==`, compare with `equals`
(or unbox) so the result doesn't depend on the value.
