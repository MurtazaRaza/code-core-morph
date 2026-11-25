using System;
using System.IO;
using System.Linq;
using System.Text;
using NUnit.Framework;

namespace Stash.Tests
{
    [TestFixture]
    public class CodecTests
    {
        private static byte[] Sentences(int length)
        {
            var text = new StringBuilder();
            while (text.Length < length)
            {
                text.Append("the quick brown fox jumps over the lazy dog. ");
            }
            return Encoding.ASCII.GetBytes(text.ToString(0, length));
        }

        private static byte[] Noise(int length, int seed)
        {
            var random = new Random(seed);
            var data = new byte[length];
            random.NextBytes(data);
            return data;
        }

        [TestCase(0, Effort.Fast)]
        [TestCase(1, Effort.Fast)]
        [TestCase(100, Effort.High)]
        [TestCase(5000, Effort.Max)]
        [TestCase(70000, Effort.Fast)]
        public void SentencesRoundTrip(int length, Effort effort)
        {
            byte[] data = Sentences(length);
            Assert.AreEqual(data, Codec.Unpack(Codec.Pack(data, effort)));
        }

        [Test]
        public void NoiseRoundTripsAtEveryEffort()
        {
            byte[] data = Noise(3000, 17);
            foreach (Effort effort in new[] { Effort.Fast, Effort.High, Effort.Max })
            {
                byte[] packed = Codec.Pack(data, effort);
                Assert.That(packed.Length, Is.LessThanOrEqualTo(Codec.PackedBound(data.Length)));
                Assert.AreEqual(data, Codec.Unpack(packed));
            }
        }

        [Test]
        public void ARunOfOneBytePacksSmall()
        {
            byte[] packed = Codec.Pack(Enumerable.Repeat((byte)'a', 64).ToArray());
            var expected = new byte[]
            {
                0x40, 0x00, 0x00, 0x00, 0x1f, 0x61, 0x01, 0x00, 0x27, 0x50, 0x61, 0x61, 0x61, 0x61, 0x61,
            };
            Assert.AreEqual(expected, packed);
        }

        [Test]
        public void HigherEffortPacksTighter()
        {
            byte[] data = Sentences(5000);
            Assert.AreEqual(80, Codec.Pack(data, Effort.Fast).Length);
            Assert.AreEqual(78, Codec.Pack(data, Effort.High).Length);
            Assert.AreEqual(78, Codec.Pack(data, Effort.Max).Length);
        }

        [Test]
        public void NothingPacksToItsHeaderAlone()
        {
            Assert.AreEqual(new byte[] { 0, 0, 0, 0 }, Codec.Pack(new byte[0]));
            Assert.IsEmpty(Codec.Unpack(new byte[] { 0, 0, 0, 0 }));
        }

        [Test]
        public void TheBoundCoversTheHeader()
        {
            Assert.AreEqual(20, Codec.PackedBound(0));
            Assert.AreEqual(5039, Codec.PackedBound(5000));
            var e = Assert.Throws<ArgumentOutOfRangeException>(() => Codec.PackedBound(-1));
            Assert.AreEqual("length", e.ParamName);
        }

        [Test]
        public void AShortHeaderIsRefused()
        {
            var e = Assert.Throws<InvalidDataException>(() => Codec.Unpack(new byte[] { 1, 0, 0 }));
            Assert.AreEqual("A packed blob starts with its length.", e.Message);
        }

        [Test]
        public void ATruncatedBlockIsRefused()
        {
            byte[] packed = Codec.Pack(Sentences(2000));
            Array.Resize(ref packed, packed.Length - 3);
            Assert.Throws<InvalidDataException>(() => Codec.Unpack(packed));
        }

        [Test]
        public void AHeaderThatClaimsTooMuchIsRefused()
        {
            byte[] packed = Codec.Pack(Sentences(10));
            packed[0] = 12;
            var e = Assert.Throws<InvalidDataException>(() => Codec.Unpack(packed));
            Assert.AreEqual("The blob holds 10 bytes, not the 12 its header says.", e.Message);
        }

        [Test]
        public void NullIsRefused()
        {
            Assert.AreEqual("data", Assert.Throws<ArgumentNullException>(() => Codec.Pack(null)).ParamName);
            Assert.AreEqual("packed", Assert.Throws<ArgumentNullException>(() => Codec.Unpack(null)).ParamName);
            Assert.AreEqual("data", Assert.Throws<ArgumentNullException>(() => Codec.Checksum(null)).ParamName);
        }

        [Test]
        public void ChecksumsAreXxHash()
        {
            Assert.AreEqual(0xEF46DB3751D8E999UL, Codec.Checksum(new byte[0]));
            Assert.AreEqual(0x13099D40D095B684UL, Codec.Checksum(new byte[] { 0x41 }));
            Assert.AreEqual(0x02CC5D05u, Codec.ShortChecksum(new byte[0], 0));
            Assert.AreEqual(0xE4FDA1C1u, Codec.ShortChecksum(Enumerable.Repeat((byte)'a', 16).ToArray(), 0x9E3779B1));
        }

        [Test]
        public void ChecksumsTellBlobsApart()
        {
            byte[] data = Noise(500, 3);
            ulong before = Codec.Checksum(data);
            data[250] ^= 1;
            Assert.AreNotEqual(before, Codec.Checksum(data));
        }
    }
}
