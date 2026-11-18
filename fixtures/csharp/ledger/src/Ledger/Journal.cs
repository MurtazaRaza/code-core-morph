using System.Collections.Generic;

namespace Ledger
{
    /// <summary>Sales and invoices, in the order they came.</summary>
    public sealed class Journal
    {
        private readonly List<dynamic> lines = new List<dynamic>();

        public void Record(Sale sale)
        {
            lines.Add(sale);
        }

        public void Record(Invoice invoice)
        {
            lines.Add(invoice);
        }

        public int Count
        {
            get { return lines.Count; }
        }

        public decimal Sum()
        {
            decimal sum = 0m;
            foreach (var line in lines)
            {
                sum += line.Amount;
            }
            return sum;
        }

        public string Last()
        {
            if (lines.Count == 0)
            {
                return "";
            }
            return Labels.Line(lines[lines.Count - 1]);
        }
    }
}
