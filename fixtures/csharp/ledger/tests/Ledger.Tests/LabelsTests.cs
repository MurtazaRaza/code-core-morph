using Ledger;
using Microsoft.CSharp.RuntimeBinder;
using NUnit.Framework;

namespace Ledger.Tests
{
    [TestFixture]
    public class LabelsTests
    {
        [Test]
        public void ASaleAsALine()
        {
            Assert.AreEqual("tea: 10.50", Labels.Line(new Sale("tea", 10.5m, 3)));
        }

        [Test]
        public void AnInvoiceAsALine()
        {
            Assert.AreEqual("rent: 1200.00", Labels.Line(new Invoice("rent", 1200m, 30)));
        }

        [Test]
        public void ARefundAsALine()
        {
            Refund refund = new PartialRefund("pot", 8.125m, 0.5m);
            Assert.AreEqual("pot: 8.13", Labels.Line(refund));
        }

        [Test]
        public void ANoteHasNoAmount()
        {
            var thrown = Assert.Throws<RuntimeBinderException>(() => Labels.Line(new Note("closed")));
            StringAssert.Contains("Amount", thrown.Message);
        }
    }
}
