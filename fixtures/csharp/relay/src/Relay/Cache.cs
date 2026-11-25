using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;

namespace Relay
{
    /// <summary>Values by key, fetched from a store once and kept.</summary>
    public sealed class Cache
    {
        private readonly IStore store;
        private readonly Dictionary<string, string> entries =
            new Dictionary<string, string>(StringComparer.Ordinal);
        private readonly object gate = new object();
        private int fetches;

        public Cache(IStore store)
        {
            this.store = store ?? throw new ArgumentNullException(nameof(store));
        }

        public int Count
        {
            get
            {
                lock (gate)
                {
                    return entries.Count;
                }
            }
        }

        /// <summary>How many values the store has given.</summary>
        public int Fetches
        {
            get
            {
                lock (gate)
                {
                    return fetches;
                }
            }
        }

        public async Task<string> GetAsync(string key, CancellationToken cancellation = default(CancellationToken))
        {
            if (key == null)
            {
                throw new ArgumentNullException(nameof(key));
            }
            lock (gate)
            {
                if (entries.TryGetValue(key, out string hit))
                {
                    return hit;
                }
            }
            string value = await store.FetchAsync(key, cancellation).ConfigureAwait(false);
            if (value == null)
            {
                throw new InvalidOperationException($"The store has no value for '{key}'.");
            }
            lock (gate)
            {
                entries[key] = value;
                fetches++;
            }
            return value;
        }

        /// <summary>Each key's value, fetched at once, in the keys' order.</summary>
        public async Task<string[]> GetManyAsync(string[] keys, CancellationToken cancellation = default(CancellationToken))
        {
            if (keys == null)
            {
                throw new ArgumentNullException(nameof(keys));
            }
            Task<string>[] pending = keys.Select(k => GetAsync(k, cancellation)).ToArray();
            return await Task.WhenAll(pending).ConfigureAwait(false);
        }

        public void Clear()
        {
            lock (gate)
            {
                entries.Clear();
            }
        }
    }
}
