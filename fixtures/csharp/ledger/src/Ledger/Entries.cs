using System;

namespace Ledger
{
    /// <summary>A line of the ledger.</summary>
    public abstract class Entry
    {
        protected Entry(string memo)
        {
            if (memo == null)
            {
                throw new ArgumentNullException(nameof(memo));
            }
            Memo = memo;
        }

        public string Memo { get; }
    }

    public sealed class Sale : Entry
    {
        public Sale(string memo, decimal amount, int units) : base(memo)
        {
            Amount = amount;
            Units = units;
        }

        public decimal Amount { get; }

        public int Units { get; }
    }

    public class Refund : Entry
    {
        public Refund(string memo, decimal amount) : base(memo)
        {
            Amount = amount;
        }

        public decimal Amount { get; }
    }

    /// <summary>A refund of a share of the amount.</summary>
    public sealed class PartialRefund : Refund
    {
        public PartialRefund(string memo, decimal amount, decimal share) : base(memo, amount)
        {
            Share = share;
        }

        public decimal Share { get; }
    }

    /// <summary>A line with no amount.</summary>
    public sealed class Note : Entry
    {
        public Note(string memo) : base(memo)
        {
        }
    }

    /// <summary>Not an entry, but it has a memo and an amount too.</summary>
    public sealed class Invoice
    {
        public Invoice(string memo, decimal amount, int days)
        {
            Memo = memo;
            Amount = amount;
            Days = days;
        }

        public string Memo { get; }

        public decimal Amount { get; }

        public int Days { get; }
    }
}
