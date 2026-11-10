using System.Collections.Generic;
using Xunit;

namespace Zoo.Xunit
{
    // The cases' arguments cover how xUnit names them in its report.
    public class AnimalFacts
    {
        public static IEnumerable<object[]> Rows()
        {
            yield return new object[] { 1 };
        }

        [Fact]
        public void ADogSaysWoof()
        {
            Assert.Equal("woof", new Dog("Rex").Sound());
        }

        [Fact(DisplayName = "a bird has two legs")]
        public void Birds()
        {
            Assert.Equal(2, new Bird("Tweety").Legs);
        }

        [Theory]
        [InlineData(1, "a")]
        [InlineData(-2, "with \"quotes\"")]
        [InlineData(0, null)]
        public void Ints(int number, string text)
        {
            Assert.True(number < 2);
        }

        [Theory]
        [InlineData(4.50)]
        [InlineData(7.90)]
        [InlineData(21.00)]
        public void Doubles(double value)
        {
            Assert.True(value > 0);
        }

        [Theory]
        [InlineData(1.5f)]
        public void Floats(float value)
        {
            Assert.True(value > 0);
        }

        [Theory]
        [InlineData('x')]
        [InlineData('\'')]
        public void Chars(char value)
        {
            Assert.NotEqual('\0', value);
        }

        [Theory]
        [InlineData(true)]
        public void Bools(bool value)
        {
            Assert.True(value);
        }

        [Theory]
        [InlineData(Ticket.Kind.Child)]
        public void Enums(Ticket.Kind kind)
        {
            Assert.Equal(6.25m, new Ticket(kind).Price());
        }

        [Theory]
        [InlineData(5L)]
        public void Longs(long value)
        {
            Assert.Equal(5L, value);
        }

        [Theory]
        [MemberData(nameof(Rows))]
        public void FromMembers(int value)
        {
            Assert.Equal(1, value);
        }

        public class Nested
        {
            [Fact]
            public void Inner()
            {
                Assert.True(true);
            }
        }
    }
}
