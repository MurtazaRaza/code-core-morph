"""C# test sources: masking, test methods, assertions and disabled tests (SPEC §11.3)."""

from __future__ import annotations

from codecoremorph.adapters.csharp.testcode import (
    assertions,
    disabled_markers,
    find_cases,
    mask_csharp,
)

NUNIT = """using NUnit.Framework;

namespace Shop.Tests
{
    [TestFixture]
    public class CartTests
    {
        [Test]
        public void Adds()
        {
            var cart = new Cart();
            Assert.AreEqual(3, cart.Add(3));
            Assert.That(cart.Count, Is.EqualTo(1));
        }

        [TestCase(1, 2)]
        [TestCase(3, 4)]
        public void Pairs(int a, int b)
        {
            CollectionAssert.AreEqual(new[] { a }, new[] { b - 1 });
        }

        [Test, Ignore("not yet")]
        public void Later() { }

        [Test]
        [ExpectedException(typeof(System.ArgumentException))]
        public void Throws() => new Cart().Add(-1);

        private int helper = 0;

        public class Nested
        {
            [Test]
            public void Inner() { Assert.IsTrue(true); }
        }
    }
}
"""

XUNIT = """using Xunit;

namespace Shop.Tests;

public class PriceTests
{
    [Fact]
    public void Rounds() => Assert.Equal(2m, Price.Round(1.5m));

    [Theory]
    [InlineData(1)]
    public void Positive(int n)
    {
        n.Should().BePositive().And.BeLessThan(10);
        Assert.True(n > 0, "a [Fact] in a string");
    }

    [Fact(Skip = "flaky")]
    public void Skipped() { }

    public int[] Values = new int[] { 1 };
}
"""

MSTEST = """[TestClass]
public class Plain
{
    [TestMethod]
    public void Works() { Assert.Inconclusive("later"); }

    [DataTestMethod]
    [DataRow(1)]
    public void Rows(int x) { Assume.That(x > 0); }
}
"""


def test_masking_blanks_comments_and_every_kind_of_string() -> None:
    text = (
        'var a = "x\\"y"; // c\n'
        'var b = @"C:\\dir\\" + $"{a}"; /* d */\n'
        'var c = """\n  raw "quoted"\n  """;\n'
        "var d = 'x';\n"
    )
    masked = mask_csharp(text)
    assert len(masked) == len(text)
    assert masked.count("\n") == text.count("\n")
    for gone in ('x\\"y', "// c", "C:\\dir", "/* d */", 'raw "quoted"', "'x'"):
        assert gone not in masked
    assert 'var c = """' in masked
    assert '"""' in masked.splitlines()[4]
    assert mask_csharp(text, strings=False).count("C:\\dir") == 1


def test_nunit_cases_with_nested_classes() -> None:
    cases = {c.test_id: c for c in find_cases(NUNIT)}
    assert list(cases) == [
        "dotnet:Shop.Tests.CartTests#Adds",
        "dotnet:Shop.Tests.CartTests#Pairs",
        "dotnet:Shop.Tests.CartTests#Later",
        "dotnet:Shop.Tests.CartTests#Throws",
        "dotnet:Shop.Tests.CartTests+Nested#Inner",
    ]
    assert cases["dotnet:Shop.Tests.CartTests#Adds"].line == 8
    assert NUNIT.splitlines()[cases["dotnet:Shop.Tests.CartTests#Pairs"].line - 1].strip() == (
        "[TestCase(1, 2)]"
    )


def test_xunit_cases_in_a_file_scoped_namespace() -> None:
    ids = [c.test_id for c in find_cases(XUNIT)]
    assert ids == [
        "dotnet:Shop.Tests.PriceTests#Rounds",
        "dotnet:Shop.Tests.PriceTests#Positive",
        "dotnet:Shop.Tests.PriceTests#Skipped",
    ]


def test_mstest_cases() -> None:
    assert [c.test_id for c in find_cases(MSTEST)] == ["dotnet:Plain#Works", "dotnet:Plain#Rows"]


def test_assertions_are_normalized_calls_and_chains() -> None:
    cases = {c.method: c for c in find_cases(NUNIT)}
    assert assertions(NUNIT, cases["Adds"]) == [
        "Assert.AreEqual(3,cart.Add(3))",
        "Assert.That(cart.Count,Is.EqualTo(1))",
    ]
    assert assertions(NUNIT, cases["Pairs"]) == ["CollectionAssert.AreEqual(new[]{a},new[]{b - 1})"]
    assert assertions(NUNIT, cases["Throws"]) == [
        "[ExpectedException(typeof(System.ArgumentException))]"
    ]
    xunit = {c.method: c for c in find_cases(XUNIT)}
    assert assertions(XUNIT, xunit["Rounds"]) == ["Assert.Equal(2m,Price.Round(1.5m))"]
    assert assertions(XUNIT, xunit["Positive"]) == [
        "Should().BePositive().And.BeLessThan(10)",
        'Assert.True(n > 0,"a[Fact]in a string")',  # spaces go, in strings too
    ]


def test_disabled_markers() -> None:
    assert disabled_markers("CartTests.cs", NUNIT) == ["CartTests.cs:23: Ignore"]
    assert disabled_markers("PriceTests.cs", XUNIT) == ["PriceTests.cs:18: Skip ="]
    assert disabled_markers("Plain.cs", MSTEST) == [
        "Plain.cs:5: Assert.Inconclusive",
        "Plain.cs:9: Assume.That",
    ]


def test_strings_and_comments_hold_no_tests() -> None:
    text = 'class C {\n  // [Test]\n  string s = "[Fact] public void X() {}";\n}\n'
    assert find_cases(text) == []
    assert disabled_markers("C.cs", 'class C { string s = "[Ignore]"; }') == []
