using System;
using System.Collections.Generic;

namespace Shop
{
    /// <summary>Thrown when the shop has fewer of an item than a cart asks for.</summary>
    public class OutOfStockException : Exception
    {
        public OutOfStockException(string sku, int wanted, int available)
            : base("only " + available + " of " + sku + " left, " + wanted + " wanted")
        {
            Sku = sku;
        }

        public string Sku { get; private set; }
    }

    /// <summary>A quantity of one item in a cart.</summary>
    public class CartLine
    {
        public CartLine(Item item, int quantity)
        {
            Item = item;
            Quantity = quantity;
        }

        public Item Item { get; private set; }

        public int Quantity { get; private set; }

        public Money Total
        {
            get { return Item.Price.Times(Quantity); }
        }
    }

    /// <summary>A shopping cart that takes items from the shop's stock.</summary>
    public class Cart
    {
        private readonly List<CartLine> lines = new List<CartLine>();
        private readonly Dictionary<string, int> stock;
        private readonly string currency;

        public Cart(string currency, IDictionary<string, int> stock)
        {
            if (stock == null)
            {
                throw new ArgumentNullException("stock");
            }
            this.currency = currency;
            this.stock = new Dictionary<string, int>(stock);
        }

        /// <summary>Raised after a line is added.</summary>
        public event Action<CartLine> LineAdded;

        public string Currency
        {
            get { return currency; }
        }

        public int Count
        {
            get { return lines.Count; }
        }

        public CartLine this[int index]
        {
            get { return lines[index]; }
        }

        public void Add(Item item, int quantity)
        {
            if (item == null)
            {
                throw new ArgumentNullException("item");
            }
            if (quantity <= 0)
            {
                throw new ArgumentOutOfRangeException("quantity", "a quantity is positive");
            }
            if (item.Price.Currency != currency)
            {
                throw new InvalidOperationException("this cart takes " + currency);
            }
            int available;
            if (!stock.TryGetValue(item.Sku, out available) || available < quantity)
            {
                throw new OutOfStockException(item.Sku, quantity, available);
            }
            stock[item.Sku] = available - quantity;
            var line = new CartLine(item, quantity);
            lines.Add(line);
            var handler = LineAdded;
            if (handler != null)
            {
                handler(line);
            }
        }

        public void Remove(string sku)
        {
            int index = lines.FindIndex(l => l.Item.Sku == sku);
            if (index < 0)
            {
                throw new KeyNotFoundException("no line for " + sku);
            }
            stock[sku] += lines[index].Quantity;
            lines.RemoveAt(index);
        }

        public int InStock(string sku)
        {
            return stock[sku];
        }

        public Item Find(Func<Item, bool> predicate)
        {
            foreach (var line in lines)
            {
                if (predicate(line.Item))
                {
                    return line.Item;
                }
            }
            return null;
        }

        public Money Subtotal()
        {
            var total = Money.Zero(currency);
            foreach (var line in lines)
            {
                total += line.Total;
            }
            return total;
        }

        public Money Total(IDiscountPolicy policy)
        {
            var total = Money.Zero(currency);
            foreach (var line in lines)
            {
                total += line.Total - policy.Discount(line);
            }
            return total;
        }
    }
}
