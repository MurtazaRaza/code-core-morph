namespace Ledger
{
    /// <summary>A running count: whole units until an amount comes in.</summary>
    public sealed class Tally
    {
        private dynamic total = 0;

        public void Add(int units)
        {
            total += units;
        }

        public void Add(decimal amount)
        {
            total += amount;
        }

        public dynamic Total
        {
            get { return total; }
        }
    }
}
