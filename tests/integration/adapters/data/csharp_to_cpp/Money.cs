using System;
using CodeCoreMorph.Interop;

namespace Shop
{
    /// <summary>An amount of money in one currency.</summary>
    public struct Money : IEquatable<Money>
    {
        private readonly decimal amount;
        private readonly string currency;

        public Money(decimal amount, string currency)
        {
            CcmDecimal a;
            IntPtr c;
            CcmInterop.Check(CcmNative.shop_Money_new(CcmDecimal.From(amount), currency, out a, out c));
            this.amount = a.ToDecimal();
            this.currency = CcmInterop.TakeString(c);
        }

        // A Money the bridge returned; takes its currency string.
        private Money(CcmDecimal amount, IntPtr currency)
        {
            this.amount = amount.ToDecimal();
            this.currency = CcmInterop.TakeString(currency);
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
            CcmDecimal a;
            IntPtr c;
            CcmInterop.Check(CcmNative.shop_Money_zero(currency, out a, out c));
            return new Money(a, c);
        }

        public Money Add(Money other)
        {
            CcmDecimal a;
            IntPtr c;
            CcmInterop.Check(CcmNative.shop_Money_add(
                CcmDecimal.From(amount), currency, CcmDecimal.From(other.amount), other.currency, out a, out c));
            return new Money(a, c);
        }

        public Money Subtract(Money other)
        {
            CcmDecimal a;
            IntPtr c;
            CcmInterop.Check(CcmNative.shop_Money_subtract(
                CcmDecimal.From(amount), currency, CcmDecimal.From(other.amount), other.currency, out a, out c));
            return new Money(a, c);
        }

        public Money Times(int quantity)
        {
            CcmDecimal a;
            IntPtr c;
            CcmInterop.Check(CcmNative.shop_Money_times(CcmDecimal.From(amount), currency, quantity, out a, out c));
            return new Money(a, c);
        }

        /// <summary>The given percentage of this amount, unrounded.</summary>
        public Money Percent(int percent)
        {
            CcmDecimal a;
            IntPtr c;
            CcmInterop.Check(CcmNative.shop_Money_percent(CcmDecimal.From(amount), currency, percent, out a, out c));
            return new Money(a, c);
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
            bool result;
            CcmInterop.Check(CcmNative.shop_Money_equals(
                CcmDecimal.From(amount), currency, CcmDecimal.From(other.amount), other.currency, out result));
            return result;
        }

        public override bool Equals(object obj)
        {
            return obj is Money && Equals((Money)obj);
        }

        public override int GetHashCode()
        {
            int result;
            CcmInterop.Check(CcmNative.shop_Money_hash(CcmDecimal.From(amount), currency, out result));
            return result;
        }

        public override string ToString()
        {
            IntPtr result;
            CcmInterop.Check(CcmNative.shop_Money_to_string(CcmDecimal.From(amount), currency, out result));
            return CcmInterop.TakeString(result);
        }
    }
}
