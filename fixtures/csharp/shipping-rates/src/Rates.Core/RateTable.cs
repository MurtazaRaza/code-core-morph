using System;
using System.Collections.Generic;

namespace Rates
{
    /// <summary>Prices by zone and weight band: the first band a parcel fits sets its price.</summary>
    public class RateTable
    {
        private readonly Dictionary<Zone, List<Band>> bands = new Dictionary<Zone, List<Band>>();

        public RateTable Add(Zone zone, int upToGrams, decimal price)
        {
            List<Band> list;
            if (!bands.TryGetValue(zone, out list))
            {
                list = new List<Band>();
                bands.Add(zone, list);
            }
            list.Add(new Band(upToGrams, price));
            list.Sort((a, b) => a.UpToGrams.CompareTo(b.UpToGrams));
            return this;
        }

        /// <summary>The price of a parcel, or null when no band of its zone takes it.</summary>
        public decimal? Price(Parcel parcel)
        {
            List<Band> list;
            if (!bands.TryGetValue(parcel.Zone, out list))
            {
                return null;
            }
            foreach (Band band in list)
            {
                if (parcel.Grams <= band.UpToGrams)
                {
                    return band.Price;
                }
            }
            return null;
        }

        public static string ZoneCode(Zone zone)
        {
            switch (zone)
            {
                case Zone.Domestic:
                    return "D";
                case Zone.Europe:
                    return "EU";
                default:
                    return "W";
            }
        }

        public static Zone ParseZone(string code)
        {
            if (code == "D")
            {
                return Zone.Domestic;
            }
            if (code == "EU")
            {
                return Zone.Europe;
            }
            if (code == "W")
            {
                return Zone.World;
            }
            throw new FormatException("unknown zone code " + code);
        }

        public static RateTable Standard()
        {
            return new RateTable()
                .Add(Zone.Domestic, 1000, 4.50m)
                .Add(Zone.Domestic, 5000, 7.90m)
                .Add(Zone.Europe, 1000, 9.00m)
                .Add(Zone.Europe, 5000, 15.50m)
                .Add(Zone.World, 2000, 21.00m);
        }

        private sealed class Band
        {
            public Band(int upToGrams, decimal price)
            {
                UpToGrams = upToGrams;
                Price = price;
            }

            public int UpToGrams { get; private set; }

            public decimal Price { get; private set; }
        }
    }
}
