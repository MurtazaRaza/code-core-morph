using System;
using System.IO;
using K4os.Compression.LZ4;
using K4os.Hash.xxHash;

namespace Stash
{
    /// <summary>How hard <see cref="Codec.Pack(byte[], Effort)"/> works at a blob.</summary>
    public enum Effort
    {
        Fast = 0,
        High = 1,
        Max = 2,
    }

    /// <summary>Packs blobs as LZ4 blocks behind their length, and checksums them.</summary>
    public static class Codec
    {
        /// <summary>The bytes before a packed blob's LZ4 block: its length, little-endian.</summary>
        public const int HeaderSize = 4;

        public static byte[] Pack(byte[] data)
        {
            return Pack(data, Effort.Fast);
        }

        public static byte[] Pack(byte[] data, Effort effort)
        {
            if (data == null)
            {
                throw new ArgumentNullException(nameof(data));
            }
            var packed = new byte[HeaderSize + LZ4Codec.MaximumOutputSize(data.Length)];
            WriteLength(packed, data.Length);
            int written = LZ4Codec.Encode(
                data, 0, data.Length, packed, HeaderSize, packed.Length - HeaderSize, Level(effort));
            if (written < 0)
            {
                throw new InvalidOperationException("LZ4 couldn't encode the blob.");
            }
            Array.Resize(ref packed, HeaderSize + written);
            return packed;
        }

        public static byte[] Unpack(byte[] packed)
        {
            if (packed == null)
            {
                throw new ArgumentNullException(nameof(packed));
            }
            if (packed.Length < HeaderSize)
            {
                throw new InvalidDataException("A packed blob starts with its length.");
            }
            int length = ReadLength(packed);
            if (length < 0)
            {
                throw new InvalidDataException("A packed blob's length can't be negative.");
            }
            var data = new byte[length];
            int read = LZ4Codec.Decode(packed, HeaderSize, packed.Length - HeaderSize, data, 0, length);
            if (read < 0)
            {
                throw new InvalidDataException("The blob's LZ4 block is corrupt.");
            }
            if (read != length)
            {
                throw new InvalidDataException(
                    string.Format("The blob holds {0} bytes, not the {1} its header says.", read, length));
            }
            return data;
        }

        /// <summary>The most bytes <see cref="Pack(byte[])"/> can make of <paramref name="length"/>.</summary>
        public static int PackedBound(int length)
        {
            if (length < 0)
            {
                throw new ArgumentOutOfRangeException(nameof(length), "A length can't be negative.");
            }
            return HeaderSize + LZ4Codec.MaximumOutputSize(length);
        }

        public static ulong Checksum(byte[] data)
        {
            if (data == null)
            {
                throw new ArgumentNullException(nameof(data));
            }
            return XXH64.DigestOf(data, 0, data.Length);
        }

        /// <summary>A 32-bit checksum of <paramref name="data"/>, seeded.</summary>
        public static uint ShortChecksum(byte[] data, uint seed)
        {
            if (data == null)
            {
                throw new ArgumentNullException(nameof(data));
            }
            var hash = new XXH32(seed);
            hash.Update(data, 0, data.Length);
            return hash.Digest();
        }

        private static LZ4Level Level(Effort effort)
        {
            switch (effort)
            {
                case Effort.Fast:
                    return LZ4Level.L00_FAST;
                case Effort.High:
                    return LZ4Level.L09_HC;
                case Effort.Max:
                    return LZ4Level.L12_MAX;
                default:
                    throw new ArgumentOutOfRangeException(nameof(effort));
            }
        }

        private static void WriteLength(byte[] target, int length)
        {
            target[0] = (byte)length;
            target[1] = (byte)(length >> 8);
            target[2] = (byte)(length >> 16);
            target[3] = (byte)(length >> 24);
        }

        private static int ReadLength(byte[] source)
        {
            return source[0] | (source[1] << 8) | (source[2] << 16) | (source[3] << 24);
        }
    }
}
