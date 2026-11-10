using System;

namespace Shop
{
    /// <summary>Decides how much of a line's total is taken off.</summary>
    public interface IDiscountPolicy
    {
        Money Discount(CartLine line);
    }

    /// <summary>Takes a fixed percentage off every line.</summary>
    public class PercentOff : IDiscountPolicy
    {
        private readonly int percent;

        public PercentOff(int percent)
        {
            if (percent < 0 || percent > 100)
            {
                throw new ArgumentOutOfRangeException("percent", "a percentage is from 0 to 100");
            }
            this.percent = percent;
        }

        public Money Discount(CartLine line)
        {
            return line.Total.Percent(percent);
        }
    }
}
