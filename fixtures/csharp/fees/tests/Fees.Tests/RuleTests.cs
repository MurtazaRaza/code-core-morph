using System;
using Fees;
using NUnit.Framework;

namespace Fees.Tests
{
    public class RuleTests
    {
        [Test]
        public void FlatFeeChargesItsFee()
        {
            var flat = new FlatFee(2.5m);
            Assert.AreEqual(2.50m, flat.Charge(100m));
            Assert.AreEqual(0m, flat.Charge(0m));
            Assert.AreEqual(2.5m, flat.Amount);
            Assert.AreEqual("flat", flat.Name);
        }

        [Test]
        public void FlatFeeRefusesANegativeFee()
        {
            var e = Assert.Throws<ArgumentOutOfRangeException>(() => new FlatFee(-1m));
            Assert.AreEqual("fee", e.ParamName);
        }

        [Test]
        public void PercentFeeAppliesFromItsMinimum()
        {
            var percent = new PercentFee(10m, 50m);
            Assert.AreEqual(0m, percent.Charge(40m));
            Assert.AreEqual(6.00m, percent.Charge(60m));
            Assert.AreEqual("percent 10%: 6", percent.Describe(60m));
        }

        [Test]
        public void ABaseTypedReferenceDispatches()
        {
            FeeRule rule = new PercentFee(10m, 50m);
            Assert.IsFalse(rule.Applies(40m));
            Assert.IsTrue(rule.Applies(50m));
            Assert.AreEqual(5m, rule.Fee(50m));
        }

        [Test]
        public void ACSharpRuleChargesThroughItsOverrides()
        {
            var capped = new CappedFee("capped", 0.1m, 5m);
            Assert.AreEqual(3.00m, capped.Charge(30m));
            Assert.AreEqual(5.00m, capped.Charge(100m));
            Assert.AreEqual(2, capped.Calls);
            Assert.AreEqual(0m, capped.Charge(-1m));
            Assert.AreEqual(0m, capped.Charge(20000m));
            Assert.AreEqual(2, capped.Calls);
        }

        [Test]
        public void ACSharpRuleOverridesTheProtectedLabel()
        {
            var capped = new CappedFee("mine", 0.1m, 5m);
            Assert.AreEqual("capped mine: 3.0", capped.Describe(30m));
            Assert.AreEqual(1.24m, capped.Rounded(1.235m));
        }

        [Test]
        public void ACSharpRuleOverAPortedOneCallsItsBase()
        {
            var doubled = new DoubledFlat(1.25m);
            Assert.AreEqual(2.50m, doubled.Fee(10m));
            Assert.AreEqual(2.50m, doubled.Charge(10m));
            Assert.AreEqual(1.25m, doubled.Amount);
            Assert.AreEqual("flat: 2.50", doubled.Describe(10m));
        }

        [Test]
        public void ACSharpRuleOverAPortedOverride()
        {
            var even = new EvenPercent();
            Assert.AreEqual(6.00m, even.Charge(60m));
            Assert.AreEqual(0m, even.Charge(61m));
            Assert.AreEqual(0m, even.Charge(40m));
            Assert.AreEqual("percent 10%: 6", even.Describe(60m));
        }

        [Test]
        public void ACSharpRuleNeedsAName()
        {
            var e = Assert.Throws<ArgumentNullException>(() => new CappedFee(null, 1m, 1m));
            Assert.AreEqual("name", e.ParamName);
        }
    }
}
