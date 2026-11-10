using System;

namespace Shop
{
    /// <summary>Something a shop sells.</summary>
    public class Item
    {
        private readonly string sku;
        private readonly string name;
        private readonly Money price;

        public Item(string sku, string name, Money price)
        {
            if (string.IsNullOrEmpty(sku))
            {
                throw new ArgumentException("an item needs a SKU", "sku");
            }
            this.sku = sku;
            this.name = name ?? sku;
            this.price = price;
        }

        public string Sku
        {
            get { return sku; }
        }

        public string Name
        {
            get { return name; }
        }

        public Money Price
        {
            get { return price; }
        }

        public override string ToString()
        {
            return name + " (" + sku + ") at " + price;
        }
    }
}
