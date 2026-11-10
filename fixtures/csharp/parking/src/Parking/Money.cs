using System;
using System.Globalization;

namespace Parking
{
    /// <summary>An amount of money in the garage's currency.</summary>
    public struct Money : IEquatable<Money>
    {
        public static readonly Money Zero = new Money(0m);

        private readonly decimal amount;

        public Money(decimal amount)
        {
            if (amount < 0m)
            {
                throw new ArgumentOutOfRangeException("amount", "Money can't be negative.");
            }
            this.amount = amount;
        }

        public decimal Amount
        {
            get { return amount; }
        }

        public static Money operator +(Money a, Money b)
        {
            return new Money(a.amount + b.amount);
        }

        public static Money operator *(Money a, int times)
        {
            return new Money(a.amount * times);
        }

        public static Money operator *(Money a, decimal factor)
        {
            return new Money(a.amount * factor);
        }

        public static bool operator ==(Money a, Money b)
        {
            return a.Equals(b);
        }

        public static bool operator !=(Money a, Money b)
        {
            return !a.Equals(b);
        }

        /// <summary>The smaller of two amounts.</summary>
        public static Money Min(Money a, Money b)
        {
            return a.amount <= b.amount ? a : b;
        }

        public bool Equals(Money other)
        {
            return amount == other.amount;
        }

        public override bool Equals(object obj)
        {
            return obj is Money && Equals((Money)obj);
        }

        public override int GetHashCode()
        {
            return amount.GetHashCode();
        }

        /// <summary>The amount in cents, rounded half away from zero, and the currency.</summary>
        public override string ToString()
        {
            return amount.ToString("0.00", CultureInfo.InvariantCulture) + " " + Limits.Currency;
        }
    }
}
