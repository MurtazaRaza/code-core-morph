using System;

namespace OrderBook
{
    /// <summary>One product of an order: so many units at a price.</summary>
    [Serializable]
    public class OrderLine
    {
        private readonly string sku;
        private readonly int quantity;
        private readonly Money unitPrice;

        public OrderLine(string sku, int quantity, Money unitPrice)
        {
            if (quantity <= 0)
            {
                throw new ArgumentOutOfRangeException("quantity", "a line has at least one unit");
            }
            this.sku = sku;
            this.quantity = quantity;
            this.unitPrice = unitPrice;
        }

        public string Sku
        {
            get { return sku; }
        }

        public int Quantity
        {
            get { return quantity; }
        }

        public Money UnitPrice
        {
            get { return unitPrice; }
        }

        public Money Total
        {
            get { return unitPrice.Times(quantity); }
        }

        public override bool Equals(object obj)
        {
            OrderLine other = obj as OrderLine;
            if (other == null)
            {
                return false;
            }
            return sku == other.sku && quantity == other.quantity && unitPrice.Equals(other.unitPrice);
        }

        public override int GetHashCode()
        {
            int hash = sku == null ? 0 : sku.GetHashCode();
            hash = hash * 31 + quantity;
            return hash * 31 + unitPrice.GetHashCode();
        }
    }
}
