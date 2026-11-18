using Ledger;
using NUnit.Framework;

namespace Ledger.Tests
{
    [TestFixture]
    public class JournalTests
    {
        [Test]
        public void AnEmptyJournal()
        {
            var journal = new Journal();
            Assert.AreEqual(0, journal.Count);
            Assert.AreEqual(0m, journal.Sum());
            Assert.AreEqual("", journal.Last());
        }

        [Test]
        public void SalesAndInvoicesAddUp()
        {
            var journal = new Journal();
            journal.Record(new Sale("tea", 2.5m, 4));
            journal.Record(new Invoice("rent", 100m, 30));
            journal.Record(new Sale("cake", 3m, 1));
            Assert.AreEqual(3, journal.Count);
            Assert.AreEqual(105.5m, journal.Sum());
        }

        [Test]
        public void TheLastLineOfEither()
        {
            var journal = new Journal();
            journal.Record(new Sale("tea", 2.5m, 4));
            Assert.AreEqual("tea: 2.50", journal.Last());
            journal.Record(new Invoice("rent", 100m, 30));
            Assert.AreEqual("rent: 100.00", journal.Last());
        }
    }
}
