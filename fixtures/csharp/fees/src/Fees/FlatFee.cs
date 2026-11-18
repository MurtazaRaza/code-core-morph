using System;

namespace Fees
{
    /// <summary>The same fee on every amount.</summary>
    public class FlatFee : FeeRule
    {
        private readonly decimal fee;

        public FlatFee(decimal fee)
            : base("flat")
        {
            if (fee < 0m)
            {
                throw new ArgumentOutOfRangeException(nameof(fee), "a fee can't be negative");
            }
            this.fee = fee;
        }

        public decimal Amount
        {
            get { return fee; }
        }

        public override decimal Fee(decimal amount)
        {
            return fee;
        }
    }
}
