using System;
using NUnit.Framework;

namespace OrderBook.Tests
{
    [TestFixture]
    public class BookTests
    {
        private static Book Sample()
        {
            Book book = new Book();
            Order first = book.Open("B-2");
            first.Add(new OrderLine("apple", 3, new Money(50, "EUR")));
            Order second = book.Open("B-1");
            second.Add(new OrderLine("pear", 1, new Money(75, "EUR")));
            second.Pay();
            return book;
        }

        [Test]
        public void IdsAreSorted()
        {
            CollectionAssert.AreEqual(new[] { "B-1", "B-2" }, Sample().Ids());
            Assert.AreEqual(0, new Book().Ids().Length);
        }

        [Test]
        public void GetReturnsNullForAnUnknownId()
        {
            Assert.IsNull(Sample().Get("Z-9"));
        }

        [Test]
        public void OpeningAnIdTwiceFails()
        {
            Book book = Sample();
            Assert.Throws<ArgumentException>(() => book.Open("B-1"));
        }

        [Test]
        public void ASnapshotDoesntSeeLaterChanges()
        {
            Book book = Sample();
            Book copy = book.Snapshot();
            book.Get("B-2").Add(new OrderLine("fig", 1, new Money(20, "EUR")));
            book.Open("B-3");
            Assert.AreEqual(2, copy.Count);
            Assert.AreEqual(1, copy.Get("B-2").Lines.Count);
            Assert.AreEqual(OrderStatus.Paid, copy.Get("B-1").Status);
            Assert.AreEqual(new Money(150, "EUR"), copy.Get("B-2").Total());
        }

        [Test]
        public void ExportWritesALinePerOrderLine()
        {
            Assert.AreEqual("B-1,paid,pear,1,75,EUR\nB-2,open,apple,3,50,EUR\n", Sample().Export());
        }

        [Test]
        public void ImportReadsWhatExportWrote()
        {
            Book book = Book.Import(Sample().Export());
            Assert.AreEqual(2, book.Count);
            Assert.AreEqual(new Money(150, "EUR"), book.Get("B-2").Total());
            Assert.AreEqual("open", book.Get("B-1").StatusText());
        }
    }
}
