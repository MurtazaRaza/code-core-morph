using System;
using NUnit.Framework;

namespace Shop.Tests
{
    [TestFixture]
    public class MoneyTests
    {
        [Test]
        public void AddingKeepsTheScale()
        {
            var sum = new Money(1.10m, "EUR") + new Money(1.0m, "EUR");
            Assert.AreEqual("2.10 EUR", sum.ToString());
        }

        [Test]
        public void EqualAmountsOfDifferentScaleAreEqual()
        {
            Assert.AreEqual(new Money(1.1m, "EUR"), new Money(1.10m, "EUR"));
            Assert.IsTrue(new Money(1.1m, "EUR") == new Money(1.100m, "EUR"));
            Assert.AreNotEqual(new Money(1.1m, "EUR"), new Money(1.1m, "USD"));
        }

        [TestCase(10, "0.333 EUR")]
        [TestCase(15, "0.4995 EUR")]
        [TestCase(33, "1.0989 EUR")]
        public void PercentIsUnrounded(int percent, string expected)
        {
            Assert.AreEqual(expected, new Money(3.33m, "EUR").Percent(percent).ToString());
        }

        [Test]
        public void TimesMultipliesTheAmount()
        {
            Assert.AreEqual("7.50 EUR", new Money(2.50m, "EUR").Times(3).ToString());
        }

        [Test]
        public void CurrenciesDoNotMix()
        {
            var ex = Assert.Throws<InvalidOperationException>(
                () => new Money(1m, "EUR").Add(new Money(1m, "USD")));
            StringAssert.Contains("USD", ex.Message);
        }

        [Test]
        public void ACurrencyHasThreeLetters()
        {
            var ex = Assert.Throws<ArgumentException>(() => new Money(1m, "EURO"));
            Assert.AreEqual("currency", ex.ParamName);
            Assert.Throws<ArgumentNullException>(() => new Money(1m, null));
        }
    }
}
