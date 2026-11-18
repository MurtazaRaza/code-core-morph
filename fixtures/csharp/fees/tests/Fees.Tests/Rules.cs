using System;
using Fees;

namespace Fees.Tests
{
    /// <summary>A C# rule over the abstract base: a rate up to a cap, overriding the abstract
    /// fee, the virtual test (calling the base's) and the protected label (calling the base's),
    /// and using the protected rounding.</summary>
    internal sealed class CappedFee : FeeRule
    {
        private readonly decimal rate;
        private readonly decimal cap;

        public CappedFee(string name, decimal rate, decimal cap)
            : base(name)
        {
            this.rate = rate;
            this.cap = cap;
        }

        public int Calls { get; private set; }

        public override decimal Fee(decimal amount)
        {
            Calls++;
            return Math.Min(amount * rate, cap);
        }

        public override bool Applies(decimal amount)
        {
            return base.Applies(amount) && amount < 10000m;
        }

        protected override string Label()
        {
            return "capped " + base.Label();
        }

        public decimal Rounded(decimal value)
        {
            return Round(value);
        }
    }

    /// <summary>A C# rule over a ported one: twice the flat fee.</summary>
    internal sealed class DoubledFlat : FlatFee
    {
        public DoubledFlat(decimal fee)
            : base(fee)
        {
        }

        public override decimal Fee(decimal amount)
        {
            return base.Fee(amount) * 2m;
        }
    }

    /// <summary>A C# rule over a ported override that calls its own base: even amounts only.</summary>
    internal sealed class EvenPercent : PercentFee
    {
        public EvenPercent()
            : base(10m, 50m)
        {
        }

        public override bool Applies(decimal amount)
        {
            return base.Applies(amount) && amount % 2m == 0m;
        }
    }

    /// <summary>A C# rule whose fee throws an exception the bridge doesn't know.</summary>
    internal sealed class FailingFee : FeeRule
    {
        public FailingFee()
            : base("failing")
        {
        }

        public override decimal Fee(decimal amount)
        {
            throw new TimeZoneNotFoundException("no fee today");
        }
    }
}
