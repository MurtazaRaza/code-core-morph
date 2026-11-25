using System;
using System.Threading;
using System.Threading.Tasks;

namespace Relay
{
    /// <summary>Where a cache reads what it doesn't have yet.</summary>
    public interface IStore
    {
        Task<string> FetchAsync(string key, CancellationToken cancellation);
    }

    /// <summary>A failure a later attempt may not have.</summary>
    public sealed class TransientException : Exception
    {
        public TransientException(string message)
            : base(message)
        {
        }
    }
}
