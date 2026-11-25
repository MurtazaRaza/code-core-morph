using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using NUnit.Framework;

namespace Relay.Tests
{
    [TestFixture]
    public class CacheTests
    {
        private static DictionaryStore Store()
        {
            return new DictionaryStore(new Dictionary<string, string> { ["a"] = "alpha", ["b"] = "beta", ["c"] = "gamma" });
        }

        [Test]
        public async Task AValueIsFetchedOnceThenKept()
        {
            var store = Store();
            var cache = new Cache(store);
            Assert.AreEqual("alpha", await cache.GetAsync("a"));
            Assert.AreEqual("alpha", await cache.GetAsync("a"));
            Assert.AreEqual(1, store.Calls);
            Assert.AreEqual(1, cache.Fetches);
            Assert.AreEqual(1, cache.Count);
        }

        [Test]
        public async Task ManyKeysAreFetchedAtOnceInTheirOrder()
        {
            var store = new GatedStore();
            var cache = new Cache(store);
            Task<string[]> many = cache.GetManyAsync(new[] { "x", "y", "z" });
            store.WaitFor(3);
            Assert.IsFalse(many.IsCompleted);
            store.Pending["z"].SetResult("zed");
            store.Pending["y"].SetResult("why");
            store.Pending["x"].SetResult("ex");
            Assert.AreEqual(new[] { "ex", "why", "zed" }, await many);
            Assert.AreEqual(3, cache.Count);
        }

        [Test]
        public void AFailedFetchFaultsTheTaskWithTheStoresException()
        {
            var store = Store();
            var cache = new Cache(store);
            var e = Assert.ThrowsAsync<KeyNotFoundException>(() => cache.GetAsync("nope"));
            Assert.AreSame(store.LastThrown, e);
            Assert.AreEqual("The store has nothing under 'nope'.", e.Message);
            Assert.AreEqual(0, cache.Count);
        }

        [Test]
        public void ANullValueIsRefused()
        {
            var store = new DictionaryStore(new Dictionary<string, string> { ["n"] = null });
            var cache = new Cache(store);
            var e = Assert.ThrowsAsync<InvalidOperationException>(() => cache.GetAsync("n"));
            Assert.AreEqual("The store has no value for 'n'.", e.Message);
        }

        [Test]
        public void OneFailureFaultsTheWholeBatch()
        {
            var cache = new Cache(Store());
            Assert.ThrowsAsync<KeyNotFoundException>(() => cache.GetManyAsync(new[] { "a", "nope", "b" }));
        }

        [Test]
        public void CancelingAFetchCancelsTheTask()
        {
            var store = new SilentStore();
            var cache = new Cache(store);
            using (var source = new CancellationTokenSource())
            {
                Task<string> fetch = cache.GetAsync("a", source.Token);
                Assert.IsFalse(fetch.IsCompleted);
                source.Cancel();
                Assert.ThrowsAsync<TaskCanceledException>(() => fetch);
                Assert.IsTrue(fetch.IsCanceled);
                Assert.IsTrue(store.Seen.CanBeCanceled);
                Assert.IsTrue(store.Seen.IsCancellationRequested);
            }
            Assert.AreEqual(0, cache.Count);
        }

        [Test]
        public void NullKeysFaultTheTask()
        {
            var cache = new Cache(Store());
            Task<string> fetch = cache.GetAsync(null);
            Assert.IsTrue(fetch.IsFaulted);
            var e = Assert.ThrowsAsync<ArgumentNullException>(() => fetch);
            Assert.AreEqual("key", e.ParamName);
            Assert.AreEqual("keys", Assert.ThrowsAsync<ArgumentNullException>(() => cache.GetManyAsync(null)).ParamName);
            Assert.AreEqual("store", Assert.Throws<ArgumentNullException>(() => new Cache(null)).ParamName);
        }

        [Test]
        public async Task ClearingForgetsTheValues()
        {
            var store = Store();
            var cache = new Cache(store);
            await cache.GetManyAsync(new[] { "a", "b" });
            cache.Clear();
            Assert.AreEqual(0, cache.Count);
            Assert.AreEqual("alpha", await cache.GetAsync("a"));
            Assert.AreEqual(3, store.Calls);
        }
    }
}
