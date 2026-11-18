using System.Globalization;

namespace Fees
{
    /// <summary>A percentage of the amount, from a minimum amount up.</summary>
    public class PercentFee : FeeRule
    {
        private readonly decimal percent;
        private readonly decimal minimum;

        public PercentFee(decimal percent, decimal minimum)
            : base("percent")
        {
            this.percent = percent;
            this.minimum = minimum;
        }

        public override decimal Fee(decimal amount)
        {
            return amount * percent / 100m;
        }

        public override bool Applies(decimal amount)
        {
            return base.Applies(amount) && amount >= minimum;
        }

        protected override string Label()
        {
            return Name + " " + percent.ToString(CultureInfo.InvariantCulture) + "%";
        }
    }
}
