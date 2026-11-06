using System;
using NUnit.Framework;

namespace OrderBook.Tests
{
    [TestFixture]
    public class OrderTests
    {
        private static Order Sample()
        {
            Order order = new Order("A-1");
            order.Add(new OrderLine("apple", 3, new Money(50, "EUR")));
            order.Add(new OrderLine("pear", 2, new Money(75, "EUR")));
            return order;
        }

        [Test]
        public void TotalSumsTheLines()
        {
            Assert.AreEqual(new Money(300, "EUR"), Sample().Total());
        }

        [Test]
        public void AnEmptyOrderHasNoTotal()
        {
            Assert.IsNull(new Order("A-2").Total());
        }

        [Test]
        public void FindsALineBySku()
        {
            Order order = Sample();
            Assert.AreEqual(2, order.Find("pear").Quantity);
            Assert.IsNull(order.Find("plum"));
        }

        [Test]
        public void MovesFromOpenToShipped()
        {
            Order order = Sample();
            order.Pay();
            Assert.AreEqual("paid", order.StatusText());
            order.Ship();
            Assert.AreEqual(OrderStatus.Shipped, order.Status);
        }

        [Test]
        public void APaidOrderTakesNoLines()
        {
            Order order = Sample();
            order.Pay();
            Assert.Throws<InvalidOperationException>(() => order.Add(new OrderLine("fig", 1, new Money(10, "EUR"))));
        }

        [Test]
        public void AShippedOrderCantBeCancelled()
        {
            Order order = Sample();
            order.Pay();
            order.Ship();
            Assert.Throws<InvalidOperationException>(() => order.Cancel());
        }

        [Test]
        public void TheNoteStartsEmpty()
        {
            Order order = Sample();
            Assert.IsNull(order.Note);
            order.Note = "leave at the door";
            Assert.AreEqual("leave at the door", order.Note);
        }

        [Test]
        public void ALineNeedsAUnit()
        {
            Assert.Throws<ArgumentOutOfRangeException>(() => new OrderLine("apple", 0, new Money(1, "EUR")));
        }

        public class Lines
        {
            [Test]
            public void EqualLinesAreEqual()
            {
                OrderLine a = new OrderLine("apple", 3, new Money(50, "EUR"));
                OrderLine b = new OrderLine("apple", 3, new Money(50, "EUR"));
                Assert.AreEqual(a, b);
                Assert.AreEqual(a.GetHashCode(), b.GetHashCode());
                Assert.AreEqual(new Money(150, "EUR"), a.Total);
            }
        }
    }
}
