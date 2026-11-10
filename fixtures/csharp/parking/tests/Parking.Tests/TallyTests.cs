using System;
using NUnit.Framework;

namespace Parking.Tests
{
    [TestFixture]
    public class TallyTests
    {
        [Test]
        public void CountsEachItem()
        {
            var tally = new Tally<string>();
            tally.Add("a");
            tally.Add("b");
            tally.Add("a");
            Assert.AreEqual(2, tally["a"]);
            Assert.AreEqual(1, tally["b"]);
            Assert.AreEqual(0, tally["c"]);
            Assert.AreEqual(2, tally.Count);
            Assert.AreEqual(3, tally.Total);
        }

        [Test]
        public void TheMostCommonIsTheFirstSeenOfATie()
        {
            var tally = new Tally<int>();
            foreach (int n in new[] { 3, 1, 1, 3, 2 })
            {
                tally.Add(n);
            }
            Assert.AreEqual(3, tally.MostCommon());
            Assert.AreEqual(0, tally[7]);
        }

        [Test]
        public void AnEmptyTallyHasNoMostCommon()
        {
            Assert.Throws<InvalidOperationException>(() => new Tally<string>().MostCommon());
            Assert.Throws<InvalidOperationException>(() => new Tally<int>().MostCommon());
        }

        [Test]
        public void NullIsNotCounted()
        {
            var tally = new Tally<string>();
            var e = Assert.Throws<ArgumentNullException>(() => tally.Add(null));
            Assert.AreEqual("item", e.ParamName);
            Assert.AreEqual(0, tally[null]);
            Assert.AreEqual(0, tally.Total);
        }

        [TestCase("a b a", "a")]
        [TestCase("b a a b", "b")]
        [TestCase("x", "x")]
        public void TheMostCommonWord(string words, string expected)
        {
            var tally = new Tally<string>();
            foreach (string word in words.Split(' '))
            {
                tally.Add(word);
            }
            Assert.AreEqual(expected, tally.MostCommon());
        }
    }
}
