using Ledger;
using NUnit.Framework;

namespace Ledger.Tests
{
    [TestFixture]
    public class TallyTests
    {
        [Test]
        public void AnEmptyTallyIsTheWholeNumberZero()
        {
            object total = new Tally().Total;
            Assert.AreEqual(0, total);
            Assert.IsInstanceOf<int>(total);
        }

        [Test]
        public void UnitsStayWholeNumbers()
        {
            var tally = new Tally();
            tally.Add(2);
            tally.Add(3);
            object total = tally.Total;
            Assert.AreEqual(5, total);
            Assert.IsInstanceOf<int>(total);
        }

        [Test]
        public void AnAmountMakesItADecimal()
        {
            var tally = new Tally();
            tally.Add(2);
            tally.Add(0.5m);
            tally.Add(1);
            object total = tally.Total;
            Assert.AreEqual(3.5m, total);
            Assert.IsInstanceOf<decimal>(total);
        }

        [Test]
        public void WholeNumbersWrapAsCSharpsDo()
        {
            var tally = new Tally();
            tally.Add(int.MaxValue);
            tally.Add(1);
            Assert.AreEqual(int.MinValue, (int)tally.Total);
        }
    }
}
