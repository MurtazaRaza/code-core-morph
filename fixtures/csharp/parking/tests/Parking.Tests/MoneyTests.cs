using System;
using System.Globalization;
using NUnit.Framework;

namespace Parking.Tests
{
    [TestFixture]
    public class MoneyTests
    {
        private static Money Of(string amount)
        {
            return new Money(decimal.Parse(amount, CultureInfo.InvariantCulture));
        }

        [Test]
        public void AddingKeepsTheCents()
        {
            Assert.AreEqual(new Money(3.75m), new Money(1.25m) + new Money(2.50m));
        }

        [Test]
        public void EqualAmountsAreEqualWhateverTheirScale()
        {
            Assert.IsTrue(new Money(2.0m) == new Money(2.00m));
            Assert.IsFalse(new Money(2.0m) != new Money(2m));
            Assert.AreEqual(new Money(2.0m).GetHashCode(), new Money(2.00m).GetHashCode());
            Assert.IsFalse(new Money(2m).Equals((object)2m));
        }

        [Test]
        public void NegativeMoneyIsRefused()
        {
            var e = Assert.Throws<ArgumentOutOfRangeException>(() => new Money(-0.01m));
            Assert.AreEqual("amount", e.ParamName);
        }

        [Test]
        public void ZeroIsTheDefault()
        {
            Assert.AreEqual(0m, Money.Zero.Amount);
            Assert.AreEqual(default(Money), Money.Zero);
        }

        [Test]
        public void MinIsTheSmaller()
        {
            Assert.AreEqual(new Money(1m), Money.Min(new Money(4m), new Money(1m)));
            Assert.AreEqual(new Money(1m), Money.Min(new Money(1m), new Money(4m)));
        }

        [TestCase("1.5", 3, "4.50 EUR")]
        [TestCase("0.10", 7, "0.70 EUR")]
        [TestCase("2", 0, "0.00 EUR")]
        public void TimesAQuantity(string amount, int times, string expected)
        {
            Assert.AreEqual(expected, (Of(amount) * times).ToString());
        }

        [TestCase("2.345", "2.35 EUR")]
        [TestCase("2.355", "2.36 EUR")]
        [TestCase("0.004", "0.00 EUR")]
        [TestCase("1234567.5", "1234567.50 EUR")]
        public void ToStringRoundsToCents(string amount, string expected)
        {
            Assert.AreEqual(expected, Of(amount).ToString());
        }

        [Test]
        public void TimesAFactorKeepsItsScale()
        {
            Money half = new Money(5.00m) * 0.5m;
            Assert.AreEqual(2.500m, half.Amount);
            Assert.AreEqual("2.500", half.Amount.ToString(CultureInfo.InvariantCulture));
        }
    }
}
