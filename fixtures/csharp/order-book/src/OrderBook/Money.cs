using System;
using System.Globalization;

namespace OrderBook
{
    /// <summary>An amount in one currency, in minor units (cents).</summary>
    [Serializable]
    public sealed class Money
    {
        private readonly long cents;
        private readonly string currency;

        public Money(long cents, string currency)
        {
            if (currency == null)
            {
                throw new ArgumentNullException("currency");
            }
            this.cents = cents;
            this.currency = currency;
        }

        public long Cents
        {
            get { return cents; }
        }

        public string Currency
        {
            get { return currency; }
        }

        public Money Plus(Money other)
        {
            if (other.currency != currency)
            {
                throw new InvalidOperationException(
                    "currency mismatch: " + currency + " and " + other.currency);
            }
            return new Money(cents + other.cents, currency);
        }

        public Money Times(int quantity)
        {
            return new Money(cents * quantity, currency);
        }

        public override bool Equals(object obj)
        {
            if (!(obj is Money))
            {
                return false;
            }
            Money other = (Money)obj;
            return cents == other.cents && currency == other.currency;
        }

        public override int GetHashCode()
        {
            return cents.GetHashCode() * 31 + currency.GetHashCode();
        }

        public override string ToString()
        {
            return string.Format(
                CultureInfo.InvariantCulture, "{0}.{1:00} {2}", cents / 100, cents % 100, currency);
        }
    }
}
