namespace Hostkit
{
    /// <summary>A monotonic clock in nanoseconds, from the operating system's own.</summary>
    public static class Clock
    {
        private const int Monotonic = 1; // CLOCK_MONOTONIC

        /// <summary>Nanoseconds since some fixed point; never goes back.</summary>
        public static long Nanoseconds()
        {
            if (Native.Windows)
            {
                Native.QueryPerformanceCounter(out long count);
                Native.QueryPerformanceFrequency(out long frequency);
                return (long)(count * (1_000_000_000.0 / frequency));
            }
            Native.ClockGetTime(Monotonic, out Native.Timespec now);
            return now.Seconds * 1_000_000_000 + now.Nanoseconds;
        }

        /// <summary>Milliseconds from <paramref name="start"/> (a Nanoseconds value) to now.</summary>
        public static double MillisecondsSince(long start)
        {
            return (Nanoseconds() - start) / 1_000_000.0;
        }
    }
}
