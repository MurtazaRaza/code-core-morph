using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using Microsoft.Extensions.Logging;
using NUnit.Framework;

namespace Stash.Tests
{
    [TestFixture]
    public class StoreTests
    {
        private static byte[] Bytes(string text)
        {
            return Encoding.UTF8.GetBytes(text);
        }

        [Test]
        public void WhatIsPutComesBack()
        {
            var store = new Store();
            store.Put("greeting", Bytes("hello, hello, hello, hello"));
            store.Put("empty", new byte[0]);
            Assert.AreEqual(Bytes("hello, hello, hello, hello"), store.Get("greeting"));
            Assert.IsEmpty(store.Get("empty"));
            Assert.AreEqual(2, store.Count);
        }

        [Test]
        public void PackedBytesFollowPutsAndRemoves()
        {
            var store = new Store();
            store.Put("a", Enumerable.Repeat((byte)'a', 64).ToArray());
            Assert.AreEqual(15, store.PackedBytes);
            store.Put("a", new byte[0]);
            Assert.AreEqual(4, store.PackedBytes);
            Assert.IsTrue(store.Remove("a"));
            Assert.AreEqual(0, store.PackedBytes);
            Assert.IsFalse(store.Remove("a"));
        }

        [Test]
        public void AMissingKeyThrows()
        {
            var store = new Store();
            var e = Assert.Throws<KeyNotFoundException>(() => store.Get("nope"));
            Assert.AreEqual("No blob is stored under 'nope'.", e.Message);
            Assert.AreEqual("key", Assert.Throws<ArgumentNullException>(() => store.Put(null, new byte[1])).ParamName);
        }

        [Test]
        public void EachPutIsLoggedAtDebug()
        {
            var logger = new ListLogger<Store>();
            var store = new Store(logger);
            store.Put("a", Enumerable.Repeat((byte)'a', 64).ToArray());
            store.Put("b", Bytes("b"));
            Assert.AreEqual(
                new[] { "Debug 0 Stored a: 64 bytes in 15", "Debug 0 Stored b: 1 bytes in 6" },
                logger.Sink.Select(l => l.ToString()).ToArray());
            Assert.AreEqual("Stash.Store", logger.Sink[0].Category);
        }

        [Test]
        public void AFailedChecksumIsLoggedAndThrown()
        {
            var logger = new ListLogger<Store>();
            var store = new Store(logger, Effort.High);
            store.Put("x", Bytes("some bytes, some bytes"));
            ulong sum;
            byte[] packed = store.Export("x", out sum);
            store.Import("y", packed, sum + 1);
            var e = Assert.Throws<InvalidDataException>(() => store.Get("y"));
            Assert.AreEqual("The blob under 'y' fails its checksum.", e.Message);
            Logged warning = logger.Sink.Last();
            Assert.AreEqual(LogLevel.Warning, warning.Level);
            Assert.AreEqual(7, warning.EventId.Id);
            Assert.AreEqual("ChecksumMismatch", warning.EventId.Name);
            Assert.AreEqual("Checksum mismatch for y", warning.Message);
            Assert.AreEqual("Trace 0 Imported y (" + packed.Length + " bytes)", logger.Sink[1].ToString());
        }

        [Test]
        public void RemovalsAreLoggedOnlyWhenInformationIs()
        {
            var logger = new ListLogger<Store> { MinLevel = LogLevel.Warning };
            var store = new Store(logger);
            store.Put("a", Bytes("a"));
            store.Remove("a");
            Assert.IsEmpty(logger.Sink);
            logger.MinLevel = LogLevel.Information;
            store.Put("a", Bytes("a"));
            store.Put("b", Bytes("b"));
            store.Remove("a");
            Assert.AreEqual(new[] { "Information 0 Removed a; 1 left" }, logger.Sink.Select(l => l.ToString()).ToArray());
        }

        [Test]
        public void AFactoryNamesItsLoggersCategory()
        {
            var factory = new ListLoggerFactory { MinLevel = LogLevel.Debug };
            var store = Store.Create(factory, Effort.Max);
            store.Put("k", Bytes("value value value value value"));
            Assert.AreEqual(new[] { "Stash.Store" }, factory.Categories.ToArray());
            Assert.AreEqual("Debug 0 Stored k: 29 bytes in 19", factory.Sink.Single().ToString());
            Assert.AreEqual("factory", Assert.Throws<ArgumentNullException>(() => Store.Create(null, Effort.Fast)).ParamName);
        }

        [Test]
        public void ABlobThatDoesntUnpackIsLoggedWithItsException()
        {
            var logger = new ListLogger<Store> { MinLevel = LogLevel.Error };
            var store = new Store(logger);
            store.Put("x", Bytes("some bytes, some bytes, some bytes"));
            ulong sum;
            byte[] packed = store.Export("x", out sum);
            Array.Resize(ref packed, packed.Length - 3);
            store.Import("cut", packed, sum);
            Assert.Throws<InvalidDataException>(() => store.Get("cut"));
            Logged error = logger.Sink.Single();
            Assert.AreEqual(LogLevel.Error, error.Level);
            Assert.AreEqual("Blob cut doesn't unpack", error.Message);
            Assert.IsInstanceOf<InvalidDataException>(error.Exception);
            Assert.AreEqual("The blob's LZ4 block is corrupt.", error.Exception.Message);
        }

        [Test]
        public void ExportsAreCopies()
        {
            var store = new Store();
            store.Put("a", Bytes("abcabcabcabc"));
            ulong sum;
            byte[] packed = store.Export("a", out sum);
            packed[4] ^= 0xff;
            Assert.AreEqual(Bytes("abcabcabcabc"), store.Get("a"));
            Assert.AreEqual(Codec.Checksum(Bytes("abcabcabcabc")), sum);
        }
    }
}
