using System;
using System.Threading;
using System.Threading.Tasks;
using NUnit.Framework;

namespace Relay.Tests
{
    [TestFixture]
    public class RetryTests
    {
        [Test]
        public async Task ATransientFailureIsRetried()
        {
            int calls = 0;
            string result = await Retry.RunAsync(
                async ct =>
                {
                    await Task.Yield();
                    if (++calls < 3)
                    {
                        throw new TransientException("busy");
                    }
                    return "done after " + calls;
                },
                5, TimeSpan.FromMilliseconds(10));
            Assert.AreEqual("done after 3", result);
            Assert.AreEqual(3, calls);
        }

        [Test]
        public void ItGivesUpAfterItsLastAttempt()
        {
            int calls = 0;
            var e = Assert.ThrowsAsync<TransientException>(() => Retry.RunAsync(
                ct =>
                {
                    calls++;
                    throw new TransientException("still busy " + calls);
                },
                3, TimeSpan.FromMilliseconds(1)));
            Assert.AreEqual("still busy 3", e.Message);
            Assert.AreEqual(3, calls);
        }

        [Test]
        public void OtherFailuresAreNotRetried()
        {
            int calls = 0;
            Assert.ThrowsAsync<InvalidOperationException>(() => Retry.RunAsync(
                async ct =>
                {
                    calls++;
                    await Task.Yield();
                    throw new InvalidOperationException("broken");
                },
                5, TimeSpan.FromMilliseconds(1)));
            Assert.AreEqual(1, calls);
        }

        [Test]
        public void CancelingDuringAPauseCancelsTheTask()
        {
            int calls = 0;
            using (var source = new CancellationTokenSource())
            {
                Task<string> run = Retry.RunAsync(
                    ct =>
                    {
                        calls++;
                        return Task.FromException<string>(new TransientException("busy"));
                    },
                    5, TimeSpan.FromSeconds(30), source.Token);
                source.CancelAfter(50);
                Assert.ThrowsAsync<TaskCanceledException>(() => run);
            }
            Assert.AreEqual(1, calls);
        }

        [Test]
        public async Task TheActionGetsTheCallersCancellation()
        {
            using (var source = new CancellationTokenSource())
            {
                bool seen = false;
                await Retry.RunAsync(
                    ct =>
                    {
                        seen = ct.CanBeCanceled && !ct.IsCancellationRequested;
                        return Task.FromResult("ok");
                    },
                    1, TimeSpan.Zero, source.Token);
                Assert.IsTrue(seen);
            }
        }

        [Test]
        public void BadArgumentsFaultTheTask()
        {
            Assert.AreEqual("action", Assert.ThrowsAsync<ArgumentNullException>(
                () => Retry.RunAsync(null, 1, TimeSpan.Zero)).ParamName);
            Assert.AreEqual("attempts", Assert.ThrowsAsync<ArgumentOutOfRangeException>(
                () => Retry.RunAsync(ct => Task.FromResult("x"), 0, TimeSpan.Zero)).ParamName);
        }
    }
}
