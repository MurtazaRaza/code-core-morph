using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;

namespace Rates.App
{
    /// <summary>A priced line of a quote, or why it couldn't be priced.</summary>
    public class Quote
    {
        public string Reference { get; set; }

        public decimal? Price { get; set; }

        public string Problem { get; set; }
    }

    /// <summary>Prices a batch of parcels given as lines "reference,grams,zone".</summary>
    public class Quoter
    {
        private readonly RateTable table;

        public Quoter(RateTable table)
        {
            this.table = table;
        }

        public IList<Quote> QuoteAll(string batch)
        {
            List<Quote> quotes = new List<Quote>();
            using (StringReader reader = new StringReader(batch))
            {
                string line;
                while ((line = reader.ReadLine()) != null)
                {
                    if (line.Trim().Length == 0)
                    {
                        continue;
                    }
                    quotes.Add(QuoteLine(line));
                }
            }
            return quotes;
        }

        public static string Summary(IList<Quote> quotes)
        {
            decimal total = 0m;
            int failed = 0;
            foreach (Quote quote in quotes)
            {
                if (quote.Price.HasValue)
                {
                    total += quote.Price.Value;
                }
                else
                {
                    failed++;
                }
            }
            return string.Format(
                CultureInfo.InvariantCulture, "{0} parcel(s), {1:0.00} total, {2} unpriced", quotes.Count, total, failed);
        }

        private Quote QuoteLine(string line)
        {
            string[] fields = line.Split(',');
            Quote quote = new Quote { Reference = fields[0].Trim() };
            if (fields.Length != 3)
            {
                quote.Problem = "expected reference,grams,zone";
                return quote;
            }
            int grams;
            if (!int.TryParse(fields[1].Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out grams) || grams <= 0)
            {
                quote.Problem = "bad weight " + fields[1].Trim();
                return quote;
            }
            Zone zone;
            try
            {
                zone = RateTable.ParseZone(fields[2].Trim());
            }
            catch (FormatException error)
            {
                quote.Problem = error.Message;
                return quote;
            }
            quote.Price = table.Price(new Parcel(grams, zone));
            if (!quote.Price.HasValue)
            {
                quote.Problem = "too heavy for " + RateTable.ZoneCode(zone);
            }
            return quote;
        }
    }
}
