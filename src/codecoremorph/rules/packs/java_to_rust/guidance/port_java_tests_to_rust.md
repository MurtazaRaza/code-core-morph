# Translate a Java test file to Rust

The unit translates the tests of one Java test file (JUnit 3, 4 or 5) into the crate's Rust
integration tests, one test per Java test case. The Java tests stay as they are and keep running
beside the Rust tests until the retirement: both suites judge the port. The Java files are
read-only in this unit, the test doubles and helpers it uses with them.

## What the codemod made

- `rust/<name>_rs/tests/<module>.rs` (the unit's note names it): empty, a test target of the
  library's crate, run by nextest. **The translation goes here**, safe Rust over the crate's
  public API.
- `rust/test-map.toml`: the map of Java tests to Rust tests (below).

## The translation

- A Java test method is a `#[test] fn` in snake case: `totalsItsItems` in `CartTest` is
  `fn totals_its_items()` in `tests/cart_test.rs`. A nested test class is a module
  (`mod when_empty`).
- Set-up and tear-down (`@Before`, `@BeforeEach`, JUnit 3's `setUp`, `@After`) become a helper
  each test calls (`fn cart() -> Cart`), or a guard whose `Drop` tears down.
- Each case of a parameterized test (JUnit 4's `Parameterized` runner, JUnit 5's
  `@ParameterizedTest`) is a `#[test]` of its own, or one test over a table of the cases when
  there are many (then map every case to it).
- Assertions: `assertEquals(expected, actual)` is `assert_eq!(actual, expected)`, with a delta
  `assert!((a - e).abs() <= delta)`; `assertTrue`, `assertFalse`, `assertNull`,
  `assertNotNull` are `assert!` on the Rust value; `assertSame` compares the `Arc`s
  (`Arc::ptr_eq`). `@Test(expected = IllegalArgumentException.class)` and
  `assertThrows(...)` check the `Err`: `let e = cart.add(...).unwrap_err();
  assert_eq!(e.java_type(), "java.lang.IllegalArgumentException");`, and its message where the
  Java checked `getMessage()` (`e.message()`). A project's exception is its Java type name, its
  fields through `e.fields()`.
- Test doubles (Java classes, anonymous ones included, implementing an interface of the
  library) are Rust types implementing its trait, in the test file; a lambda is a closure.
- A Java class in the tests that extends a ported class does so without overriding (one that
  overrides refused the port): it is the ported Rust type.
- Keep every check: I-4 compares how many assertions each Java test and its Rust tests make
  together, and a translation that checks less is refused.

## The test map

`rust/test-map.toml` gets one entry per Java test of the unit, by the `junit:` ID Surefire's
reports have:

```toml
[[test]]
java = "junit:shop.CartTest#totalsItsItems"
rust = "nextest:shop_rs::cart_test::totals_its_items"

[[test]]
java = "junit:shop.MoneyParseTest#parses[12.34 EUR]"
rust = "nextest:shop_rs::money_parse_test::parses_12_34_eur"
```

The unit's cases are the ones the Java names exactly. A parameterized method's cases are named
in the unit's note: map each case by the ID Surefire reports, `junit:pkg.Class#method[name]`
(JUnit 4's `Parameterized`) or `junit:pkg.Class#method(int)[1]` (JUnit 5); the retirement
checks that every one is mapped.

A Java test that becomes several Rust tests lists them all. One the Rust can't have (a test of
Java-only behavior, such as reflection over the façade) gets an entry without `rust`, with a
`note` saying why. Every mapped Rust test must run and pass.
