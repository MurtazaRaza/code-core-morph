using System;
using System.Threading;
using System.Threading.Tasks;

namespace Relay
{
    /// <summary>Runs an action again after a transient failure.</summary>
    public static class Retry
    {
        public static async Task<string> RunAsync(
            Func<CancellationToken, Task<string>> action, int attempts, TimeSpan pause,
            CancellationToken cancellation = default(CancellationToken))
        {
            if (action == null)
            {
                throw new ArgumentNullException(nameof(action));
            }
            if (attempts < 1)
            {
                throw new ArgumentOutOfRangeException(nameof(attempts), "At least one attempt is needed.");
            }
            for (int attempt = 1; ; attempt++)
            {
                try
                {
                    return await action(cancellation).ConfigureAwait(false);
                }
                catch (TransientException) when (attempt < attempts)
                {
                    await Task.Delay(pause, cancellation).ConfigureAwait(false);
                }
            }
        }
    }
}
