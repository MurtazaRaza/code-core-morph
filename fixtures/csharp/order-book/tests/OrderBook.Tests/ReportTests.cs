using NUnit.Framework;

namespace OrderBook.Tests
{
    [TestFixture]
    public class ReportTests
    {
        [Test]
        public void DescribesEachKindOfValue()
        {
            Order order = new Order("R-1");
            Assert.AreEqual("text: hello", Report.Describe("hello"));
            Assert.AreEqual("order R-1 (open)", Report.Describe(order));
            Assert.AreEqual("amount 1.50 EUR", Report.Describe(new Money(150, "EUR")));
            Assert.AreEqual("unknown", Report.Describe(42));
        }

        [TestCase(OrderStatus.Open, false)]
        [TestCase(OrderStatus.Paid, false)]
        [TestCase(OrderStatus.Shipped, true)]
        [TestCase(OrderStatus.Cancelled, true)]
        public void FinalStatuses(OrderStatus status, bool final)
        {
            Assert.AreEqual(final, Report.IsFinal(status));
        }

        [Test]
        public void TableHasARowPerOrder()
        {
            Book book = new Book();
            book.Open("T-1").Add(new OrderLine("apple", 2, new Money(50, "EUR")));
            book.Open("T-2");
            Assert.AreEqual("id\tstatus\ttotal\nT-1\topen\t1.00 EUR\nT-2\topen\t-\n", Report.Table(book));
        }

        [Test]
        public void SkusInOrder()
        {
            Order order = new Order("S-1");
            Assert.IsEmpty(Report.Skus(order));
            order.Add(new OrderLine("pear", 1, new Money(1, "EUR")));
            order.Add(new OrderLine("apple", 1, new Money(1, "EUR")));
            CollectionAssert.AreEqual(new[] { "pear", "apple" }, Report.Skus(order));
        }
    }
}
