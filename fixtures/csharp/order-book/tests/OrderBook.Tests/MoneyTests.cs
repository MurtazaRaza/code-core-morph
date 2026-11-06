using System;
using NUnit.Framework;

namespace OrderBook.Tests
{
    [TestFixture]
    public class MoneyTests
    {
        [Test]
        public void PlusAddsCents()
        {
            Money sum = new Money(150, "EUR").Plus(new Money(275, "EUR"));
            Assert.AreEqual(425, sum.Cents);
            Assert.AreEqual("EUR", sum.Currency);
        }

        [Test]
        public void PlusRefusesAnotherCurrency()
        {
            Assert.Throws<InvalidOperationException>(() => new Money(1, "EUR").Plus(new Money(1, "USD")));
        }

        [TestCase(199, 3, 597)]
        [TestCase(1000, 1, 1000)]
        [TestCase(5, 0, 0)]
        public void TimesMultiplies(long cents, int quantity, long expected)
        {
            Assert.AreEqual(expected, new Money(cents, "EUR").Times(quantity).Cents);
        }

        [Test]
        public void EqualAmountsAreEqual()
        {
            Assert.AreEqual(new Money(100, "EUR"), new Money(100, "EUR"));
            Assert.AreNotEqual(new Money(100, "EUR"), new Money(100, "USD"));
            Assert.AreEqual(new Money(100, "EUR").GetHashCode(), new Money(100, "EUR").GetHashCode());
        }

        [Test]
        public void FormatsWithTwoDecimals()
        {
            Assert.AreEqual("12.05 EUR", new Money(1205, "EUR").ToString());
            Assert.AreEqual("0.99 USD", new Money(99, "USD").ToString());
        }

        [Test]
        public void NeedsACurrency()
        {
            Assert.Throws<ArgumentNullException>(() => new Money(1, null));
        }
    }
}
