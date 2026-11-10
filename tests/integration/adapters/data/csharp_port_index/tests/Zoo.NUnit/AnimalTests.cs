using System.Collections.Generic;
using NUnit.Framework;

namespace Zoo.NUnit
{
    // The cases' arguments cover how NUnit names them in its report.
    [TestFixture]
    public class AnimalTests
    {
        private static IEnumerable<int> Sources()
        {
            yield return 1;
            yield return 2;
        }

        [Test]
        public void ADogSaysWoof()
        {
            Assert.AreEqual("woof", new Dog("Rex").Sound());
        }

        [TestCase(1, "a")]
        [TestCase(-2, "with \"quotes\" and 'apostrophes'")]
        [TestCase(0, null)]
        public void Ints(int number, string text)
        {
            Assert.That(number, Is.LessThan(2));
        }

        [TestCase(1.5)]
        [TestCase(2.0)]
        [TestCase(0.1)]
        public void Doubles(double value)
        {
            Assert.That(value, Is.GreaterThan(0));
        }

        [TestCase(1.5f)]
        public void Floats(float value)
        {
            Assert.That(value, Is.GreaterThan(0));
        }

        [TestCase(5L)]
        [TestCase(7u)]
        [TestCase(9UL)]
        public void Wide(object value)
        {
            Assert.IsNotNull(value);
        }

        [TestCase('x')]
        [TestCase('\'')]
        [TestCase('"')]
        [TestCase('\n')]
        public void Chars(char value)
        {
            Assert.That(value, Is.Not.EqualTo('\0'));
        }

        [TestCase(true)]
        [TestCase(false)]
        public void Bools(bool value)
        {
            Assert.That(value || !value);
        }

        [TestCase(Ticket.Kind.Child)]
        public void Enums(Ticket.Kind kind)
        {
            Assert.That(new Ticket(kind).Price(), Is.EqualTo(6.25m));
        }

        [TestCase(int.MaxValue)]
        [TestCase(int.MinValue)]
        public void Limits(int value)
        {
            Assert.That(value, Is.Not.EqualTo(0));
        }

        [TestCase((short)3, (byte)4, (sbyte)-5)]
        public void Small(short a, byte b, sbyte c)
        {
            Assert.That(a + b + c, Is.EqualTo(2));
        }

        [TestCase("a\\b\tc")]
        [TestCase("a string that is longer than forty characters, to see whether NUnit cuts it")]
        public void Strings(string value)
        {
            Assert.That(value.Length, Is.GreaterThan(1));
        }

        [TestCase(null)]
        public void Nulls(string value)
        {
            Assert.IsNull(value);
        }

        [TestCase(3, TestName = "ANamedCase")]
        public void Named(int value)
        {
            Assert.That(value, Is.EqualTo(3));
        }

        [TestCase(4, ExpectedResult = 8)]
        public int Doubled(int value)
        {
            return value * 2;
        }

        [TestCaseSource(nameof(Sources))]
        public void FromASource(int value)
        {
            Assert.That(value, Is.GreaterThan(0));
        }

        public class Nested
        {
            [Test]
            public void Inner()
            {
                Assert.Pass();
            }
        }
    }

    public abstract class BaseTests
    {
        [Test]
        public void Inherited()
        {
            Assert.Pass();
        }
    }

    public class DerivedTests : BaseTests
    {
    }
}
