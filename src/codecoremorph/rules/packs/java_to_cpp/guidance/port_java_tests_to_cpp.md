# Translate a Java test file to GoogleTest

The unit translates the tests of one Java test file (JUnit 3, 4 or 5) into a GoogleTest file
over the ported C++, one test per Java test case. The Java tests stay as they are and keep
running beside the GoogleTest tests until the retirement: both suites judge the port. The Java
files are read-only in this unit, the test doubles and helpers it uses with them.

## What the codemod made

- `cpp/tests/<program>.cpp` (the unit's note names it): empty, built into a GoogleTest program
  of that name, linked with the C++ library and `gtest_main`, which CTest runs
  (`cpp/tests/CMakeLists.txt`; don't edit it). **The translation goes here.** It runs under the
  sanitizers, as the library does.
- `cpp/test-map.toml`: the map of Java tests to GoogleTest tests (below).

## The translation

- The Java test class is the test suite: `@Test public void totalsItsItems()` in `CartTest` is
  `TEST(CartTest, totalsItsItems)`. A nested class's suite joins the names
  (`TaskQueueTest_WhenEmpty`), since a suite name has no `$` or `.`.
- Set-up and tear-down (`@Before`, `@BeforeEach`, JUnit 3's `setUp`, `@After`, `@AfterEach`)
  become a fixture class's `SetUp` and `TearDown` with `TEST_F`; once per class (`@BeforeClass`,
  `@BeforeAll`) is `SetUpTestSuite`.
- Each case of a parameterized test (JUnit 4's `Parameterized` runner, JUnit 5's
  `@ParameterizedTest` with its sources) is a test: a `TEST_P` instantiated with the cases'
  values, or one `TEST` per case when there are few.
- Assertions: `assertEquals(expected, actual)` is `ASSERT_EQ(actual, expected)` (every JUnit
  assertion stops the test); with a delta `ASSERT_NEAR`; `assertTrue`, `assertFalse`,
  `assertNull` and `assertNotNull` are `ASSERT_TRUE` and `ASSERT_FALSE` on the C++ value;
  `assertSame` compares the `shared_ptr`s' objects. `@Test(expected = IllegalArgumentException.class)`
  and `assertThrows(IllegalArgumentException.class, () -> ...)` are
  `ASSERT_THROW(..., ccm::IllegalArgumentException)`; a `try { ...; fail(); } catch (E e) {
  assertEquals("...", e.getMessage()); }` catches the mirror and checks its `what()`. A
  project's exception is its C++ class (its fields through its getters).
- Test doubles (Java classes, anonymous ones included, implementing an interface of the
  library) are C++ classes implementing its abstract class, in the test file; a lambda is a
  lambda.
- A Java class in the tests that extends a ported class does so without overriding (one that
  overrides refused the port): it is the ported C++ class, or one deriving from it in the test
  file.
- Keep every check: I-4 compares how many assertions each Java test and its GoogleTest tests
  make together, and a translation that checks less is refused.

## The test map

`cpp/test-map.toml` gets one entry per Java test of the unit, by the `junit:` ID Surefire's
reports have:

```toml
[[test]]
java = "junit:shop.CartTest#totalsItsItems"
cpp = "gtest:CartTest.totalsItsItems"

[[test]]
java = "junit:shop.MoneyParseTest#parses[12.34 EUR]"
cpp = "gtest:Cases/MoneyParseTest.parses/0"
```

The unit's cases are the ones the Java names exactly. A parameterized method's cases are named
in the unit's note: map each case by the ID Surefire reports, `junit:pkg.Class#method[name]`
(JUnit 4's `Parameterized` names it by the `name` pattern, `[0]` by default) or
`junit:pkg.Class#method(int)[1]` (JUnit 5); the retirement checks that every one is mapped.

A Java test that becomes several GoogleTest tests lists them all. One the C++ can't have (a test
of Java-only behavior, such as reflection over the façade, or a `null` where the C++ takes a
`std::string`) gets an entry without `cpp`, with a `note` saying why.
Every mapped GoogleTest test must run and pass.
