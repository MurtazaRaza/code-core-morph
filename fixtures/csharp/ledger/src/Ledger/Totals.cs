namespace Ledger
{
    /// <summary>What each kind of entry adds to the balance.</summary>
    public static class Totals
    {
        // The runtime binder picks the overload for the entry's own type.
        public static decimal Net(Entry entry)
        {
            return Signed((dynamic)entry);
        }

        private static decimal Signed(Sale sale)
        {
            return sale.Amount * sale.Units;
        }

        private static decimal Signed(Refund refund)
        {
            return -refund.Amount;
        }

        private static decimal Signed(PartialRefund refund)
        {
            return -refund.Amount * refund.Share;
        }

        private static decimal Signed(Entry other)
        {
            return 0m;
        }
    }
}
