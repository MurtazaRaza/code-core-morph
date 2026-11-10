using System;
using System.Collections.Generic;
using NUnit.Framework;

namespace Shop.Tests
{
    [TestFixture]
    public class CartTests
    {
        private static readonly Item Pen = new Item("PEN", "Pen", new Money(1.50m, "EUR"));
        private static readonly Item Pad = new Item("PAD", "Notepad", new Money(3.25m, "EUR"));

        private static Cart NewCart()
        {
            return new Cart("EUR", new Dictionary<string, int> { { "PEN", 10 }, { "PAD", 2 } });
        }

        /// <summary>A test double: takes a fixed amount off lines of one SKU, and records calls.</summary>
        private class FixedOff : IDiscountPolicy
        {
            public readonly List<CartLine> Seen = new List<CartLine>();
            private readonly string sku;
            private readonly Money off;

            public FixedOff(string sku, Money off)
            {
                this.sku = sku;
                this.off = off;
            }

            public Money Discount(CartLine line)
            {
                Seen.Add(line);
                return line.Item.Sku == sku ? off : Money.Zero(line.Total.Currency);
            }
        }

        private class Exploding : IDiscountPolicy
        {
            public Money Discount(CartLine line)
            {
                throw new NotSupportedException("no discounts today");
            }
        }

        [Test]
        public void AddTakesFromStock()
        {
            var cart = NewCart();
            cart.Add(Pen, 3);
            Assert.AreEqual(1, cart.Count);
            Assert.AreEqual(7, cart.InStock("PEN"));
            Assert.AreEqual(3, cart[0].Quantity);
        }

        [Test]
        public void LinesKeepTheItemsGiven()
        {
            var cart = NewCart();
            cart.Add(Pen, 1);
            cart.Add(Pad, 1);
            Assert.AreSame(Pen, cart[0].Item);
            Assert.AreSame(Pad, cart[1].Item);
            Assert.AreSame(cart[1], cart[1]);
        }

        [Test]
        public void SubtotalAddsTheLines()
        {
            var cart = NewCart();
            cart.Add(Pen, 3);
            cart.Add(Pad, 2);
            Assert.AreEqual(new Money(11.00m, "EUR"), cart.Subtotal());
            Assert.AreEqual("11.00 EUR", cart.Subtotal().ToString());
        }

        [Test]
        public void TotalAsksThePolicyForEachLine()
        {
            var cart = NewCart();
            cart.Add(Pen, 2);
            cart.Add(Pad, 1);
            var policy = new FixedOff("PAD", new Money(0.25m, "EUR"));
            Assert.AreEqual("6.00 EUR", cart.Total(policy).ToString());
            Assert.AreEqual(2, policy.Seen.Count);
            Assert.AreSame(cart[0], policy.Seen[0]);
            Assert.AreSame(Pad, policy.Seen[1].Item);
        }

        [Test]
        public void PercentOffTakesAShareOfEachLine()
        {
            var cart = NewCart();
            cart.Add(Pen, 3);
            Assert.AreEqual("4.05 EUR", cart.Total(new PercentOff(10)).ToString());
        }

        [Test]
        public void APolicysExceptionReachesTheCaller()
        {
            var cart = NewCart();
            cart.Add(Pen, 1);
            var ex = Assert.Throws<NotSupportedException>(() => cart.Total(new Exploding()));
            Assert.AreEqual("no discounts today", ex.Message);
        }

        [Test]
        public void OutOfStock()
        {
            var cart = NewCart();
            var ex = Assert.Throws<OutOfStockException>(() => cart.Add(Pad, 3));
            Assert.AreEqual("PAD", ex.Sku);
            Assert.AreEqual(0, cart.Count);
            Assert.AreEqual(2, cart.InStock("PAD"));
        }

        [Test]
        public void BadArguments()
        {
            var cart = NewCart();
            Assert.AreEqual("item", Assert.Throws<ArgumentNullException>(() => cart.Add(null, 1)).ParamName);
            Assert.AreEqual(
                "quantity", Assert.Throws<ArgumentOutOfRangeException>(() => cart.Add(Pen, 0)).ParamName);
            Assert.Throws<InvalidOperationException>(
                () => cart.Add(new Item("X", "X", new Money(1m, "USD")), 1));
            Assert.Throws<ArgumentOutOfRangeException>(() => { var unused = cart[0]; });
        }

        [Test]
        public void RemoveGivesStockBack()
        {
            var cart = NewCart();
            cart.Add(Pad, 2);
            cart.Remove("PAD");
            Assert.AreEqual(0, cart.Count);
            Assert.AreEqual(2, cart.InStock("PAD"));
            Assert.Throws<KeyNotFoundException>(() => cart.Remove("PAD"));
            Assert.Throws<KeyNotFoundException>(() => cart.InStock("INK"));
        }

        [Test]
        public void FindTakesALambda()
        {
            var cart = NewCart();
            cart.Add(Pen, 1);
            cart.Add(Pad, 1);
            Assert.AreSame(Pad, cart.Find(i => i.Price.Amount > 2m));
            Assert.IsNull(cart.Find(i => i.Sku == "INK"));
        }

        [Test]
        public void FindWithoutAPredicate()
        {
            var cart = NewCart();
            Assert.IsNull(cart.Find(null));
            cart.Add(Pen, 1);
            Assert.Throws<NullReferenceException>(() => cart.Find(null));
        }

        [Test]
        public void APredicatesNullReferenceReachesTheCaller()
        {
            var cart = NewCart();
            cart.Add(Pen, 1);
            Item missing = null;
            Assert.Throws<NullReferenceException>(() => cart.Find(i => missing.Sku == i.Sku));
        }

        [Test]
        public void ASubscribersExceptionReachesTheCaller()
        {
            var cart = NewCart();
            cart.LineAdded += line => { throw new InvalidOperationException("listener failed"); };
            var ex = Assert.Throws<InvalidOperationException>(() => cart.Add(Pen, 1));
            Assert.AreEqual("listener failed", ex.Message);
            Assert.AreEqual(1, cart.Count);
        }

        [Test]
        public void LineAddedIsRaised()
        {
            var cart = NewCart();
            var added = new List<CartLine>();
            cart.LineAdded += added.Add;
            cart.LineAdded += line => Assert.AreSame(line, cart[cart.Count - 1]);
            cart.Add(Pen, 1);
            Assert.AreEqual(1, added.Count);
            Assert.AreSame(cart[0], added[0]);
            cart.LineAdded -= added.Add;
            cart.Add(Pad, 1);
            Assert.AreEqual(1, added.Count);
        }
    }
}
