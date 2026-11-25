using System;
using System.Threading;
using System.Threading.Tasks;

namespace Zoo
{
    // Async code the tasks runtime takes over (D-96): the project's own awaits, a delegate's
    // task, Task.WhenAll, Task.Delay, a token, ConfigureAwait.
    public interface IServer
    {
        Task<int> ServeAsync(Animal animal, CancellationToken cancellation);
    }

    public class Waiter
    {
        public async Task<int> FeedAllAsync(IServer server, Animal[] animals, CancellationToken cancellation)
        {
            var fed = new Task<int>[animals.Length];
            for (int i = 0; i < animals.Length; i++)
            {
                fed[i] = server.ServeAsync(animals[i], cancellation);
            }
            int[] portions = await Task.WhenAll(fed).ConfigureAwait(false);
            await Task.Delay(TimeSpan.FromMilliseconds(1), cancellation);
            return portions.Length;
        }

        public Task<int> LaterAsync(Func<CancellationToken, Task<int>> later)
        {
            return later(CancellationToken.None);
        }

        // A sleep the token cuts short.
        public bool Nap(CancellationToken cancellation)
        {
            return cancellation.WaitHandle.WaitOne(TimeSpan.FromMilliseconds(1));
        }
    }
}
