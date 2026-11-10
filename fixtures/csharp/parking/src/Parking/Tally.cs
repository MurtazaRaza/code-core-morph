using System;
using System.Collections.Generic;
using System.Linq;

namespace Parking
{
    /// <summary>Counts how often each item was seen.</summary>
    public sealed class Tally<T>
    {
        private readonly Dictionary<T, int> counts = new Dictionary<T, int>();

        public void Add(T item)
        {
            if (item == null)
            {
                throw new ArgumentNullException("item");
            }
            int seen;
            counts.TryGetValue(item, out seen);
            counts[item] = seen + 1;
        }

        /// <summary>How often the item was seen: 0 for one never seen.</summary>
        public int this[T item]
        {
            get
            {
                int seen;
                return item != null && counts.TryGetValue(item, out seen) ? seen : 0;
            }
        }

        /// <summary>The number of different items.</summary>
        public int Count
        {
            get { return counts.Count; }
        }

        public int Total
        {
            get { return counts.Values.Sum(); }
        }

        /// <summary>The item seen most often; of those tied, the first one seen.</summary>
        public T MostCommon()
        {
            if (counts.Count == 0)
            {
                throw new InvalidOperationException("The tally is empty.");
            }
            return counts.OrderByDescending(pair => pair.Value).First().Key;
        }
    }
}
