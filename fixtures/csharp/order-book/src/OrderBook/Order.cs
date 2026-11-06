using System;
using System.Collections.Generic;

namespace OrderBook
{
    public enum OrderStatus
    {
        Open,
        Paid,
        Shipped,
        Cancelled,
    }

    /// <summary>An order: its lines, and where it is between open and shipped.</summary>
    [Serializable]
    public class Order
    {
        private readonly string id;
        private readonly List<OrderLine> lines = new List<OrderLine>();
        private OrderStatus status;
        private string note;

        public Order(string id)
        {
            this.id = id;
            status = OrderStatus.Open;
        }

        public string Id
        {
            get { return id; }
        }

        public OrderStatus Status
        {
            get { return status; }
        }

        /// <summary>A free-text note; null until someone writes one.</summary>
        public string Note
        {
            get { return note; }
            set { note = value; }
        }

        public IList<OrderLine> Lines
        {
            get { return lines.AsReadOnly(); }
        }

        public void Add(OrderLine line)
        {
            if (status != OrderStatus.Open)
            {
                throw new InvalidOperationException("order " + id + " is " + StatusText());
            }
            lines.Add(line);
        }

        /// <summary>The order's total, or null for an order without lines.</summary>
        public Money Total()
        {
            if (lines.Count == 0)
            {
                return null;
            }
            Money total = lines[0].Total;
            for (int i = 1; i < lines.Count; i++)
            {
                total = total.Plus(lines[i].Total);
            }
            return total;
        }

        public OrderLine Find(string sku)
        {
            foreach (OrderLine line in lines)
            {
                if (line.Sku == sku)
                {
                    return line;
                }
            }
            return null;
        }

        public void Pay()
        {
            Move(OrderStatus.Open, OrderStatus.Paid);
        }

        public void Ship()
        {
            Move(OrderStatus.Paid, OrderStatus.Shipped);
        }

        public void Cancel()
        {
            if (status == OrderStatus.Shipped)
            {
                throw new InvalidOperationException("order " + id + " has shipped");
            }
            status = OrderStatus.Cancelled;
        }

        public string StatusText()
        {
            switch (status)
            {
                case OrderStatus.Open:
                    return "open";
                case OrderStatus.Paid:
                    return "paid";
                case OrderStatus.Shipped:
                    return "shipped";
                default:
                    return "cancelled";
            }
        }

        private void Move(OrderStatus from, OrderStatus to)
        {
            if (status != from)
            {
                throw new InvalidOperationException("order " + id + " is " + StatusText());
            }
            status = to;
        }
    }
}
