using System.Globalization;

namespace Ledger
{
    public static class Labels
    {
        /// <summary>Anything with a memo and an amount, as a line of text.</summary>
        public static string Line(dynamic item)
        {
            return item.Memo + ": " + item.Amount.ToString("0.00", CultureInfo.InvariantCulture);
        }
    }
}
