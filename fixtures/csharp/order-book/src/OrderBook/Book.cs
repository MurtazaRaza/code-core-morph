using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Runtime.Serialization.Formatters.Binary;

namespace OrderBook
{
    /// <summary>Every order of a shop, by ID.</summary>
    [Serializable]
    public class Book
    {
        private readonly Dictionary<string, Order> orders = new Dictionary<string, Order>();

        public int Count
        {
            get { return orders.Count; }
        }

        public Order Open(string id)
        {
            if (orders.ContainsKey(id))
            {
                throw new ArgumentException("order " + id + " exists", "id");
            }
            Order order = new Order(id);
            orders.Add(id, order);
            return order;
        }

        /// <summary>The order, or null when there is none with that ID.</summary>
        public Order Get(string id)
        {
            Order order;
            return orders.TryGetValue(id, out order) ? order : null;
        }

        public string[] Ids()
        {
            if (orders.Count == 0)
            {
                return new string[0];
            }
            List<string> ids = new List<string>(orders.Keys);
            ids.Sort(StringComparer.Ordinal);
            return ids.ToArray();
        }

        /// <summary>A copy of the book that later changes to this one don't reach.</summary>
        public Book Snapshot()
        {
            using (MemoryStream stream = new MemoryStream())
            {
                BinaryFormatter formatter = new BinaryFormatter();
                formatter.Serialize(stream, this);
                stream.Position = 0;
                return (Book)formatter.Deserialize(stream);
            }
        }

        /// <summary>One line per order line: id,status,sku,quantity,cents,currency.</summary>
        public string Export()
        {
            using (StringWriter writer = new StringWriter(CultureInfo.InvariantCulture))
            {
                foreach (string id in Ids())
                {
                    Order order = orders[id];
                    foreach (OrderLine line in order.Lines)
                    {
                        writer.Write(id + "," + order.StatusText() + "," + line.Sku + ",");
                        writer.Write(line.Quantity.ToString(CultureInfo.InvariantCulture) + ",");
                        writer.Write(line.UnitPrice.Cents.ToString(CultureInfo.InvariantCulture));
                        writer.Write("," + line.UnitPrice.Currency + "\n");
                    }
                }
                return writer.ToString();
            }
        }

        /// <summary>The open orders of an <see cref="Export"/>.</summary>
        public static Book Import(string text)
        {
            Book book = new Book();
            using (StringReader reader = new StringReader(text))
            {
                string row;
                while ((row = reader.ReadLine()) != null)
                {
                    if (row.Length == 0)
                    {
                        continue;
                    }
                    string[] fields = row.Split(',');
                    Order order = book.Get(fields[0]) ?? book.Open(fields[0]);
                    Money price = new Money(long.Parse(fields[4], CultureInfo.InvariantCulture), fields[5]);
                    order.Add(new OrderLine(fields[2], int.Parse(fields[3], CultureInfo.InvariantCulture), price));
                }
            }
            return book;
        }
    }
}
