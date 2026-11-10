# Translate a C# test file to GoogleTest

The unit translates the tests of one C# test file (NUnit, xUnit or MSTest) into a GoogleTest
file over the ported C++, one test per C# test case. The C# tests stay as they are and keep
running beside the GoogleTest tests until the retirement: both suites judge the port. The C#
files are read-only in this unit, the test doubles and helpers it uses with them.

## What the codemod made

- `cpp/tests/<program>.cpp` (the unit's note names it): empty, built into a GoogleTest program
  of that name, linked with the C++ library and `gtest_main`, which CTest runs
  (`cpp/tests/CMakeLists.txt`; don't edit it). **The translation goes here.** It runs under the
  sanitizers, as the library does.
- `cpp/test-map.toml`: the map of C# tests to GoogleTest tests (below).

## The translation

- The C# test class is the test suite: `[Test] public void Sound()` in `AnimalTests` is
  `TEST(AnimalTests, Sound)`. A nested class's suite joins the names (`AnimalTests_Nested`),
  since a suite name has no `+` or `.`.
- Set-up and tear-down (`[SetUp]`, `[TestInitialize]`, an xUnit constructor, `[TearDown]`,
  `Dispose`) become a fixture class's `SetUp` and `TearDown` with `TEST_F`; once per class
  (`[OneTimeSetUp]`, `[ClassInitialize]`, a class fixture) is `SetUpTestSuite`.
- Each case of a parameterized test (`[TestCase]`, `[InlineData]`, `[DataRow]`, a data source's
  rows) is a test: a `TEST_P` instantiated with the cases' values
  (`INSTANTIATE_TEST_SUITE_P(Cases, AnimalTests_Ints, ::testing::Values(...))`), or one `TEST`
  per case when there are few.
- Assertions: `Assert.AreEqual(e, a)`, `Assert.That(a, Is.EqualTo(e))` and `Assert.Equal(e, a)`
  are `ASSERT_EQ(a, e)` where the C# stops at a failure (every framework's asserts do), with a
  tolerance `ASSERT_NEAR`; `Assert.IsTrue`, `Assert.IsNull` and their kin are `ASSERT_TRUE` and
  `ASSERT_FALSE` on the C++ value. `Assert.Throws<ArgumentNullException>(() => ...)` is
  `ASSERT_THROW(..., ccm::ArgumentNullException)`; where the test then checks the exception's
  message or parameter, catch it and check them. A project's exception is its C++ class.
- Test doubles (C# classes implementing an interface of the library) are C++ classes
  implementing its abstract class, in the test file.
- Keep every check: I-4 compares how many assertions each C# test and its GoogleTest tests make
  together, and a translation that checks less is refused.

## The test map

`cpp/test-map.toml` gets one entry per C# test of the unit, by the `dotnet:` ID the C# test
reports have:

```toml
[[test]]
csharp = "dotnet:Zoo.Tests.AnimalTests#Sound"
cpp = "gtest:AnimalTests.Sound"

[[test]]
csharp = "dotnet:Zoo.Tests.AnimalTests#Ints(1,\"a\")"
cpp = "gtest:Cases/AnimalTests_Ints.Run/0"
```

The unit's cases are the ones the C# names exactly. A method whose cases come from data
(`[TestCaseSource]`, `[MemberData]`, `[DynamicData]`) is named in the unit's note: map each of
its cases by the ID the C# test runs report, `dotnet:Ns.Class#Method(args)` with the arguments
as the framework prints them; the retirement checks that every one is mapped.

A C# test that becomes several GoogleTest tests lists them all. One the C++ can't have (a test
of C#-only behavior, such as reflection over the façade, or a null where the C++ takes a
`std::string`) gets an entry without `cpp`, with a `note` saying why.
Every mapped GoogleTest test must run and pass.
