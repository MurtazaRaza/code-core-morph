using System.Runtime.InteropServices;

namespace OrderBook
{
    /// <summary>The operating system's clock, for timestamps on Windows builds.</summary>
    internal static class Platform
    {
        // Declared for the Windows service host; nothing here calls it.
        [DllImport("kernel32.dll")]
        internal static extern uint GetTickCount();
    }
}
