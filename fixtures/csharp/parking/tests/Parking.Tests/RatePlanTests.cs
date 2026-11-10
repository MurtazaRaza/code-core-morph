using System;
using NUnit.Framework;

namespace Parking.Tests
{
    [TestFixture]
    public class RatePlanTests
    {
        private static readonly HourlyRate Hourly = new HourlyRate(new Money(2.50m), new Money(20m));

        [Test]
        public void AFlatRateIgnoresTheTime()
        {
            IRatePlan plan = new FlatRate(new Money(3m));
            Assert.AreEqual(new Money(3m), plan.Charge(new Car("a"), 0));
            Assert.AreEqual(new Money(3m), plan.Charge(new Car("a"), 5000));
        }

        [Test]
        public void AFlatRateScalesWithTheVehicle()
        {
            Assert.AreEqual("1.50 EUR", new FlatRate(new Money(3m)).Charge(new Motorcycle("m"), 10).ToString());
        }

        [TestCase(0, "0.00 EUR")]
        [TestCase(1, "2.50 EUR")]
        [TestCase(60, "2.50 EUR")]
        [TestCase(61, "5.00 EUR")]
        [TestCase(600, "20.00 EUR")]
        [TestCase(1440, "20.00 EUR")]
        [TestCase(1500, "22.50 EUR")]
        public void AnHourlyRateChargesEachStartedHour(int minutes, string expected)
        {
            Assert.AreEqual(expected, Hourly.Charge(new Car("a"), minutes).ToString());
        }

        [Test]
        public void AnHourlyRateScalesWithTheVehicle()
        {
            Money fee = Hourly.Charge(new Motorcycle("m"), 61);
            Assert.AreEqual(2.500m, fee.Amount);
            Assert.AreEqual("2.50 EUR", fee.ToString());
        }

        [Test]
        public void AnHourlyRateKeepsItsPrices()
        {
            Assert.AreEqual(new Money(2.50m), Hourly.PerHour);
            Assert.AreEqual(new Money(20m), Hourly.DailyCap);
        }

        [Test]
        public void ACapBelowAnHourIsRefused()
        {
            var e = Assert.Throws<ArgumentException>(() => new HourlyRate(new Money(5m), new Money(1m)));
            Assert.AreEqual("dailyCap", e.ParamName);
        }

        [Test]
        public void ANegativeStayIsRefused()
        {
            Assert.Throws<ArgumentOutOfRangeException>(() => Hourly.Charge(new Car("a"), -1));
            Assert.Throws<ArgumentOutOfRangeException>(() => new FlatRate(new Money(1m)).Charge(new Car("a"), -1));
        }

        [Test]
        public void ChargingNoVehicleIsRefused()
        {
            var e = Assert.Throws<ArgumentNullException>(() => Hourly.Charge(null, 10));
            Assert.AreEqual("vehicle", e.ParamName);
        }

        [Test]
        public void PlansAreNamedByTheirPrice()
        {
            Assert.AreEqual("flat 3.00 EUR", new FlatRate(new Money(3m)).Name);
            Assert.AreEqual("hourly 2.50 EUR", Hourly.Name);
        }
    }
}
