using System;
using System.Globalization;

namespace Shop
{
    /// <summary>An amount of money in one currency.</summary>
    public struct Money : IEquatable<Money>
    {
        private readonly decimal amount;
        private readonly string currency;

        public Money(decimal amount, string currency)
        {
            if (currency == null)
            {
                throw new ArgumentNullException("currency");
            }
            if (currency.Length != 3)
            {
                throw new ArgumentException("a currency is a three-letter code", "currency");
            }
            this.amount = amount;
            this.currency = currency;
        }

        public decimal Amount
        {
            get { return amount; }
        }

        public string Currency
        {
            get { return currency; }
        }

        public static Money Zero(string currency)
        {
            return new Money(0m, currency);
        }

        public Money Add(Money other)
        {
            CheckCurrency(other);
            return new Money(amount + other.amount, currency);
        }

        public Money Subtract(Money other)
        {
            CheckCurrency(other);
            return new Money(amount - other.amount, currency);
        }

        public Money Times(int quantity)
        {
            return new Money(amount * quantity, currency);
        }

        /// <summary>The given percentage of this amount, unrounded.</summary>
        public Money Percent(int percent)
        {
            return new Money(amount * percent / 100, currency);
        }

        public static Money operator +(Money a, Money b)
        {
            return a.Add(b);
        }

        public static Money operator -(Money a, Money b)
        {
            return a.Subtract(b);
        }

        public static bool operator ==(Money a, Money b)
        {
            return a.Equals(b);
        }

        public static bool operator !=(Money a, Money b)
        {
            return !a.Equals(b);
        }

        public bool Equals(Money other)
        {
            return amount == other.amount && currency == other.currency;
        }

        public override bool Equals(object obj)
        {
            return obj is Money && Equals((Money)obj);
        }

        public override int GetHashCode()
        {
            return amount.GetHashCode() * 31 + (currency == null ? 0 : currency.GetHashCode());
        }

        public override string ToString()
        {
            return amount.ToString(CultureInfo.InvariantCulture) + " " + currency;
        }

        private void CheckCurrency(Money other)
        {
            if (other.currency != currency)
            {
                throw new InvalidOperationException(
                    "cannot combine " + other.currency + " with " + currency);
            }
        }
    }
}
