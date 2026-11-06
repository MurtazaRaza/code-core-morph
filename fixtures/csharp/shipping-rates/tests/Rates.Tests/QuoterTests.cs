using System.Collections.Generic;
using Rates.App;
using Xunit;

namespace Rates.Tests
{
    public class QuoterTests
    {
        private static IList<Quote> Run(string batch)
        {
            return new Quoter(RateTable.Standard()).QuoteAll(batch);
        }

        [Fact]
        public void QuotesEachLine()
        {
            IList<Quote> quotes = Run("p1,500,D\np2,3000,EU\n");
            Assert.Equal(2, quotes.Count);
            Assert.Equal("p1", quotes[0].Reference);
            Assert.Equal(4.50m, quotes[0].Price);
            Assert.Equal(15.50m, quotes[1].Price);
            Assert.Null(quotes[1].Problem);
        }

        [Fact]
        public void SkipsBlankLines()
        {
            Assert.Single(Run("\n  \np1,500,D\n"));
        }

        [Theory]
        [InlineData("p1,abc,D", "bad weight abc")]
        [InlineData("p1,-5,D", "bad weight -5")]
        [InlineData("p1,500,MARS", "unknown zone code MARS")]
        [InlineData("p1,500", "expected reference,grams,zone")]
        [InlineData("p1,9000,W", "too heavy for W")]
        public void ExplainsWhatItCantPrice(string line, string problem)
        {
            Quote quote = Run(line)[0];
            Assert.Null(quote.Price);
            Assert.Equal(problem, quote.Problem);
        }

        [Fact]
        public void SummarizesTheBatch()
        {
            Assert.Equal("3 parcel(s), 13.50 total, 1 unpriced", Quoter.Summary(Run("a,500,D\nb,900,EU\nc,1,MARS")));
        }
    }
}
