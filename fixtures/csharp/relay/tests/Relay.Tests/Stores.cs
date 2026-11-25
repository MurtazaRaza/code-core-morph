using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;

namespace Relay.Tests
{
    /// <summary>A store of fixed values that answers at once; a key it lacks fails the fetch.</summary>
    public sealed class DictionaryStore : IStore
    {
        private readonly Dictionary<string, string> values;
        private int calls;

        public DictionaryStore(Dictionary<string, string> values)
        {
            this.values = values;
        }

        public int Calls => calls;

        public Exception LastThrown { get; private set; }

        public async Task<string> FetchAsync(string key, CancellationToken cancellation)
        {
            Interlocked.Increment(ref calls);
            await Task.Yield();
            if (values.TryGetValue(key, out string value))
            {
                return value;
            }
            var missing = new KeyNotFoundException($"The store has nothing under '{key}'.");
            LastThrown = missing;
            throw missing;
        }
    }

    /// <summary>A store whose fetches wait until the test lets each one go.</summary>
    public sealed class GatedStore : IStore
    {
        public ConcurrentDictionary<string, TaskCompletionSource<string>> Pending { get; } =
            new ConcurrentDictionary<string, TaskCompletionSource<string>>();

        public Task<string> FetchAsync(string key, CancellationToken cancellation)
        {
            var gate = new TaskCompletionSource<string>(TaskCreationOptions.RunContinuationsAsynchronously);
            Pending[key] = gate;
            return gate.Task;
        }

        /// <summary>Waits until <paramref name="count"/> fetches wait.</summary>
        public void WaitFor(int count)
        {
            var deadline = DateTime.UtcNow.AddSeconds(10);
            while (Pending.Count < count)
            {
                if (DateTime.UtcNow > deadline)
                {
                    throw new TimeoutException($"{Pending.Count} of {count} fetches came.");
                }
                Thread.Sleep(5);
            }
        }
    }

    /// <summary>A store that never answers: only cancellation ends a fetch.</summary>
    public sealed class SilentStore : IStore
    {
        public CancellationToken Seen { get; private set; }

        public async Task<string> FetchAsync(string key, CancellationToken cancellation)
        {
            Seen = cancellation;
            await Task.Delay(Timeout.Infinite, cancellation);
            return "never";
        }
    }
}
