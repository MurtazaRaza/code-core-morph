using System;
using Xunit;

namespace Rates.Tests
{
    public class RateTableTests
    {
        [Theory]
        [InlineData(500, Zone.Domestic, 4.50)]
        [InlineData(1000, Zone.Domestic, 4.50)]
        [InlineData(1001, Zone.Domestic, 7.90)]
        [InlineData(4200, Zone.Europe, 15.50)]
        [InlineData(1999, Zone.World, 21.00)]
        public void PricesByTheFirstBandThatFits(int grams, Zone zone, double expected)
        {
            decimal? price = RateTable.Standard().Price(new Parcel(grams, zone));
            Assert.Equal((decimal)expected, price.Value);
        }

        [Fact]
        public void TooHeavyHasNoPrice()
        {
            Assert.Null(RateTable.Standard().Price(new Parcel(2001, Zone.World)));
        }

        [Fact]
        public void AZoneWithoutBandsHasNoPrice()
        {
            Assert.Null(new RateTable().Price(new Parcel(10, Zone.Europe)));
        }

        [Fact]
        public void BandsAreSortedWhateverTheOrderAdded()
        {
            RateTable table = new RateTable().Add(Zone.Domestic, 5000, 8m).Add(Zone.Domestic, 1000, 5m);
            Assert.Equal(5m, table.Price(new Parcel(800, Zone.Domestic)));
        }

        [Theory]
        [InlineData(Zone.Domestic, "D")]
        [InlineData(Zone.Europe, "EU")]
        [InlineData(Zone.World, "W")]
        public void ZoneCodesGoBothWays(Zone zone, string code)
        {
            Assert.Equal(code, RateTable.ZoneCode(zone));
            Assert.Equal(zone, RateTable.ParseZone(code));
        }

        [Fact]
        public void AnUnknownCodeIsRefused()
        {
            FormatException error = Assert.Throws<FormatException>(() => RateTable.ParseZone("X"));
            Assert.Contains("X", error.Message);
        }

        public class Parcels
        {
            [Fact]
            public void EqualParcelsAreEqual()
            {
                Assert.Equal(new Parcel(10, Zone.World), new Parcel(10, Zone.World));
                Assert.NotEqual(new Parcel(10, Zone.World), new Parcel(10, Zone.Europe));
                Assert.Equal(new Parcel(10, Zone.World).GetHashCode(), new Parcel(10, Zone.World).GetHashCode());
            }

            [Fact]
            public void AParcelWeighsSomething()
            {
                Assert.Throws<ArgumentOutOfRangeException>(() => new Parcel(0, Zone.Domestic));
            }
        }
    }
}
