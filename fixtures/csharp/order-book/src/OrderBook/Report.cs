using System;
using System.Collections.Generic;
using System.Text;

namespace OrderBook
{
    /// <summary>Plain-text summaries of orders.</summary>
    public static class Report
    {
        private static readonly string[] Headings = new string[] { "id", "status", "total" };

        public static string Describe(object value)
        {
            string text = value as string;
            if (text != null)
            {
                return "text: " + text;
            }
            if (value is Order)
            {
                Order order = (Order)value;
                return "order " + order.Id + " (" + order.StatusText() + ")";
            }
            if (value is Money)
            {
                return "amount " + value;
            }
            return "unknown";
        }

        public static bool IsFinal(OrderStatus status)
        {
            return status == OrderStatus.Shipped || status == OrderStatus.Cancelled;
        }

        public static string Table(Book book)
        {
            StringBuilder builder = new StringBuilder();
            builder.Append(string.Join("\t", Headings)).Append('\n');
            foreach (string id in book.Ids())
            {
                Order order = book.Get(id);
                Money total = order.Total();
                builder.Append(id).Append('\t').Append(order.StatusText()).Append('\t');
                builder.Append(total == null ? "-" : total.ToString()).Append('\n');
            }
            return builder.ToString();
        }

        public static IList<string> Skus(Order order)
        {
            if (order.Lines.Count == 0)
            {
                return Array.Empty<string>();
            }
            List<string> skus = new List<string>();
            foreach (OrderLine line in order.Lines)
            {
                skus.Add(line.Sku);
            }
            return skus;
        }
    }
}
