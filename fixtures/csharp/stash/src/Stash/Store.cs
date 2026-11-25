using System;
using System.Collections.Generic;
using System.IO;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Logging.Abstractions;

namespace Stash
{
    /// <summary>Blobs by key, kept packed with their checksums.</summary>
    public sealed class Store
    {
        private static readonly EventId Mismatch = new EventId(7, "ChecksumMismatch");

        private readonly Dictionary<string, Entry> entries =
            new Dictionary<string, Entry>(StringComparer.Ordinal);
        private readonly ILogger logger;
        private readonly Effort effort;

        public Store()
            : this(null, Effort.Fast)
        {
        }

        public Store(ILogger<Store> logger)
            : this(logger, Effort.Fast)
        {
        }

        public Store(ILogger<Store> logger, Effort effort)
            : this((ILogger)logger, effort)
        {
        }

        private Store(ILogger logger, Effort effort)
        {
            this.logger = logger ?? NullLogger.Instance;
            this.effort = effort;
        }

        /// <summary>A store that logs through a logger of <paramref name="factory"/>'s.</summary>
        public static Store Create(ILoggerFactory factory, Effort effort)
        {
            if (factory == null)
            {
                throw new ArgumentNullException(nameof(factory));
            }
            return new Store(factory.CreateLogger("Stash.Store"), effort);
        }

        public int Count => entries.Count;

        /// <summary>The packed bytes of every blob, headers included.</summary>
        public long PackedBytes { get; private set; }

        public void Put(string key, byte[] data)
        {
            if (key == null)
            {
                throw new ArgumentNullException(nameof(key));
            }
            byte[] packed = Codec.Pack(data, effort);
            ulong sum = Codec.Checksum(data);
            Keep(key, new Entry(packed, sum));
            logger.LogDebug("Stored {Key}: {Raw} bytes in {Packed}", key, data.Length, packed.Length);
        }

        public byte[] Get(string key)
        {
            Entry entry = Find(key);
            byte[] data;
            try
            {
                data = Codec.Unpack(entry.Packed);
            }
            catch (InvalidDataException e)
            {
                logger.LogError(e, "Blob {Key} doesn't unpack", key);
                throw;
            }
            if (Codec.Checksum(data) != entry.Sum)
            {
                logger.LogWarning(Mismatch, "Checksum mismatch for {Key}", key);
                throw new InvalidDataException($"The blob under '{key}' fails its checksum.");
            }
            return data;
        }

        public bool Remove(string key)
        {
            if (key == null)
            {
                throw new ArgumentNullException(nameof(key));
            }
            if (!entries.TryGetValue(key, out Entry entry))
            {
                return false;
            }
            entries.Remove(key);
            PackedBytes -= entry.Packed.Length;
            if (logger.IsEnabled(LogLevel.Information))
            {
                logger.LogInformation("Removed {Key}; {Count} left", key, entries.Count);
            }
            return true;
        }

        /// <summary>A blob as it is kept: its packed bytes, and its checksum.</summary>
        public byte[] Export(string key, out ulong checksum)
        {
            Entry entry = Find(key);
            checksum = entry.Sum;
            return (byte[])entry.Packed.Clone();
        }

        /// <summary>Keeps a blob another store exported, unchecked until it is read.</summary>
        public void Import(string key, byte[] packed, ulong checksum)
        {
            if (key == null)
            {
                throw new ArgumentNullException(nameof(key));
            }
            if (packed == null)
            {
                throw new ArgumentNullException(nameof(packed));
            }
            Keep(key, new Entry((byte[])packed.Clone(), checksum));
            logger.LogTrace("Imported {Key} ({Packed} bytes)", key, packed.Length);
        }

        private Entry Find(string key)
        {
            if (key == null)
            {
                throw new ArgumentNullException(nameof(key));
            }
            if (!entries.TryGetValue(key, out Entry entry))
            {
                throw new KeyNotFoundException($"No blob is stored under '{key}'.");
            }
            return entry;
        }

        private void Keep(string key, Entry entry)
        {
            if (entries.TryGetValue(key, out Entry old))
            {
                PackedBytes -= old.Packed.Length;
            }
            entries[key] = entry;
            PackedBytes += entry.Packed.Length;
        }

        private sealed class Entry
        {
            public Entry(byte[] packed, ulong sum)
            {
                Packed = packed;
                Sum = sum;
            }

            public byte[] Packed { get; }

            public ulong Sum { get; }
        }
    }
}
