using System;
using System.Runtime.CompilerServices;
using Fees;
using NUnit.Framework;

namespace Fees.Tests
{
    public class ScheduleTests
    {
        [Test]
        public void TotalSumsEveryRule()
        {
            var schedule = new Schedule();
            schedule.Add(new FlatFee(1m));
            schedule.Add(new PercentFee(10m, 0m));
            schedule.Add(new CappedFee("capped", 0.5m, 2m));
            schedule.Add(new DoubledFlat(0.5m));
            Assert.AreEqual(4, schedule.Count);
            Assert.AreEqual(5.00m, schedule.Total(10m));
        }

        [Test]
        public void ReportUsesEachRulesLabel()
        {
            var schedule = new Schedule();
            schedule.Add(new PercentFee(10m, 0m));
            schedule.Add(new CappedFee("c", 0.5m, 2m));
            Assert.AreEqual("percent 10%: 1\ncapped c: 2", schedule.Report(10m));
        }

        [Test]
        public void FindGivesBackTheSameObject()
        {
            var schedule = new Schedule();
            var capped = new CappedFee("mine", 0.1m, 5m);
            var flat = new FlatFee(1m);
            schedule.Add(capped);
            schedule.Add(flat);
            Assert.AreSame(capped, schedule.Find("mine"));
            Assert.AreSame(flat, schedule.Find("flat"));
            Assert.IsNull(schedule.Find("none"));
        }

        [Test]
        public void AddRefusesNull()
        {
            var e = Assert.Throws<ArgumentNullException>(() => new Schedule().Add(null));
            Assert.AreEqual("rule", e.ParamName);
        }

        [Test]
        public void AnExceptionFromAnOverrideReachesTheCaller()
        {
            var schedule = new Schedule();
            schedule.Add(new FlatFee(1m));
            schedule.Add(new FailingFee());
            var e = Assert.Throws<TimeZoneNotFoundException>(() => schedule.Total(10m));
            Assert.AreEqual("no fee today", e.Message);
        }

        [Test]
        public void ARuleOnlyTheScheduleHoldsStaysAlive()
        {
            var schedule = new Schedule();
            AddCapped(schedule, "kept");
            Collect();
            Assert.AreEqual(1.00m, schedule.Total(10m));
            var found = schedule.Find("kept") as CappedFee;
            Assert.IsNotNull(found);
            Assert.AreEqual(1, found.Calls);
        }

        [Test]
        public void ARuleNothingHoldsIsCollected()
        {
            WeakReference rule = Unheld();
            Collect();
            Assert.IsFalse(rule.IsAlive);
        }

        [Test]
        public void ARemovedRuleIsCollected()
        {
            var schedule = new Schedule();
            WeakReference rule = AddCapped(schedule, "gone");
            Collect();
            Assert.IsTrue(rule.IsAlive);
            Assert.IsTrue(schedule.Remove("gone"));
            Assert.IsFalse(schedule.Remove("gone"));
            Collect();
            Assert.IsFalse(rule.IsAlive);
            Assert.AreEqual(0, schedule.Count);
        }

        [MethodImpl(MethodImplOptions.NoInlining)]
        private static WeakReference AddCapped(Schedule schedule, string name)
        {
            var rule = new CappedFee(name, 0.1m, 100m);
            schedule.Add(rule);
            return new WeakReference(rule);
        }

        [MethodImpl(MethodImplOptions.NoInlining)]
        private static WeakReference Unheld()
        {
            var rule = new CappedFee("unheld", 0.1m, 100m);
            Assert.AreEqual(1.00m, rule.Charge(10m));
            return new WeakReference(rule);
        }

        private static void Collect()
        {
            for (int i = 0; i < 4; i++)
            {
                GC.Collect();
                GC.WaitForPendingFinalizers();
            }
        }
    }
}
