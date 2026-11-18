using System;
using System.Globalization;

namespace Fees
{
    /// <summary>A rule that charges a fee on an amount.</summary>
    public abstract class FeeRule
    {
        protected FeeRule(string name)
        {
            if (name == null)
            {
                throw new ArgumentNullException(nameof(name));
            }
            Name = name;
        }

        /// <summary>The rule's name in a schedule.</summary>
        public string Name { get; }

        /// <summary>The fee before rounding.</summary>
        public abstract decimal Fee(decimal amount);

        /// <summary>Whether the rule charges anything on the amount.</summary>
        public virtual bool Applies(decimal amount)
        {
            return amount > 0m;
        }

        /// <summary>The fee charged: rounded to cents, zero where the rule doesn't apply.</summary>
        public decimal Charge(decimal amount)
        {
            return Applies(amount) ? Round(Fee(amount)) : 0m;
        }

        /// <summary>A line of a report: the rule's label and its charge.</summary>
        public string Describe(decimal amount)
        {
            return Label() + ": " + Charge(amount).ToString(CultureInfo.InvariantCulture);
        }

        /// <summary>The rule's label in a report.</summary>
        protected virtual string Label()
        {
            return Name;
        }

        /// <summary>Rounds to cents, half away from zero.</summary>
        protected static decimal Round(decimal value)
        {
            return Math.Round(value, 2, MidpointRounding.AwayFromZero);
        }
    }
}
