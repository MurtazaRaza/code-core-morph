using System.Collections.Generic;
using System.Linq;
using Ledger;
using NUnit.Framework;

namespace Ledger.Tests
{
    [TestFixture]
    public class TotalsTests
    {
        [Test]
        public void ASaleAddsItsAmountTimesItsUnits()
        {
            Assert.AreEqual(31.5m, Totals.Net(new Sale("tea", 10.5m, 3)));
        }

        [Test]
        public void ARefundTakesItsAmountOff()
        {
            Assert.AreEqual(-4m, Totals.Net(new Refund("cup", 4m)));
        }

        [Test]
        public void APartialRefundTakesItsShareOff()
        {
            Assert.AreEqual(-2.5m, Totals.Net(new PartialRefund("pot", 10m, 0.25m)));
        }

        [Test]
        public void ANoteAddsNothing()
        {
            Assert.AreEqual(0m, Totals.Net(new Note("closed early")));
        }

        [Test]
        public void EachEntryOfAListByItsOwnType()
        {
            var entries = new List<Entry>
            {
                new Sale("tea", 2m, 5),
                new PartialRefund("pot", 8m, 0.5m),
                new Note("-"),
                new Refund("cup", 1.25m),
            };
            Assert.AreEqual(4.75m, entries.Sum(Totals.Net));
        }
    }
}
