# Translate a C# test file to Rust

The unit translates the tests of one C# test file (NUnit, xUnit or MSTest) into an integration
test file of the library's crate, one test per C# test case, over the ported Rust. The C# tests
stay as they are and keep running beside the Rust tests until the retirement: both suites judge
the port. The C# files are read-only in this unit, the test doubles and helpers it uses with
them.

## What the codemod made

- `rust/<name>_rs/tests/<file>.rs` (the unit's note names it): empty. **The translation goes
  here**: Cargo builds it as a test binary of that name, which nextest runs. It uses the
  library's public items (`use <name>_rs::cart::Cart;`), and no `unsafe`.
- `rust/test-map.toml`: the map of C# tests to Rust tests (below).

## The translation

- Each C# test is a `#[test] fn` named in snake case: `[Test] public void AddTakesFromStock()`
  is `fn add_takes_from_stock()`. A nested class's tests go in a `mod` of its name.
- Set-up and tear-down (`[SetUp]`, `[TestInitialize]`, an xUnit constructor, `[TearDown]`,
  `Dispose`) become a function each test calls first (and a guard whose `Drop` tears down);
  once per class (`[OneTimeSetUp]`, a class fixture) is a `OnceLock`.
- Each case of a parameterized test (`[TestCase]`, `[InlineData]`, `[DataRow]`, a data source's
  rows) is a test of its own, calling one shared function with the case's values:
  `percent_is_unrounded_10`, `percent_is_unrounded_15`. The shared function's checks count for
  each case that calls it (I-4 follows the calls to the file's own functions).
- Assertions: `Assert.AreEqual(e, a)`, `Assert.That(a, Is.EqualTo(e))` and `Assert.Equal(e, a)`
  are `assert_eq!(e, a)`, with a tolerance `assert!((a - e).abs() <= tol)`; `Assert.IsTrue`,
  `Assert.IsNull` and their kin are `assert!` on the Rust value; `Assert.AreSame(a, b)` is
  `assert!(Arc::ptr_eq(&a, &b))`. `Assert.Throws<ArgumentNullException>(() => ...)` is
  `let e = ....unwrap_err(); assert!(e.is("System.ArgumentNullException"));`, and where the
  test then checks the exception's message or parameter, check `e.message()` and
  `e.param_name()` too. A project's exception is its .NET type's name with its fields
  (`e.field("sku")`).
- Test doubles (C# classes implementing an interface of the library) are Rust types
  implementing its trait, in the test file.
- Keep every check: I-4 compares how many assertions each C# test and its Rust tests make
  together, and a translation that checks less is refused.

## The test map

`rust/test-map.toml` gets one entry per C# test of the unit, by the `dotnet:` ID the C# test
reports have, and the Rust test's nextest ID, `nextest:<crate>::<file>::<test>`:

```toml
[[test]]
csharp = "dotnet:Shop.Tests.CartTests#AddTakesFromStock"
rust = "nextest:shop_rs::cart_tests::add_takes_from_stock"

[[test]]
csharp = "dotnet:Shop.Tests.MoneyTests#PercentIsUnrounded(10,\"0.333 EUR\")"
rust = "nextest:shop_rs::money_tests::percent_is_unrounded_10"
```

The unit's cases are the ones the C# names exactly. A method whose cases come from data
(`[TestCaseSource]`, `[MemberData]`, `[DynamicData]`) is named in the unit's note: map each of
its cases by the ID the C# test runs report, `dotnet:Ns.Class#Method(args)` with the arguments
as the framework prints them; the retirement checks that every one is mapped.

A C# test that becomes several Rust tests lists them all. One the Rust can't have (a test of
C#-only behavior, such as reflection over the façade, a null delegate, or a null where the Rust
takes a value) gets an entry without `rust`, with a `note` saying why.
Every mapped Rust test must run and pass.
