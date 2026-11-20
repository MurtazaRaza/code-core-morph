using System;
using System.Runtime.InteropServices;
using System.Text;

namespace Hostkit
{
    /// <summary>The operating systems' own functions the library calls.</summary>
    internal static class Native
    {
        public static readonly bool Windows = RuntimeInformation.IsOSPlatform(OSPlatform.Windows);

        // Windows: kernel32.
        [DllImport("kernel32.dll")]
        public static extern bool QueryPerformanceCounter(out long count);

        [DllImport("kernel32.dll")]
        public static extern bool QueryPerformanceFrequency(out long frequency);

        [DllImport("kernel32.dll")]
        public static extern uint GetCurrentProcessId();

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern IntPtr GetStdHandle(int which);

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool GetConsoleMode(IntPtr handle, out uint mode);

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool SetConsoleMode(IntPtr handle, uint mode);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
        public static extern int FormatMessageW(
            uint flags, IntPtr source, uint messageId, uint languageId, StringBuilder buffer,
            int size, IntPtr arguments);

        // Elsewhere: the C library.
        [StructLayout(LayoutKind.Sequential)]
        public struct Timespec
        {
            public long Seconds;
            public long Nanoseconds;
        }

        [DllImport("libc", EntryPoint = "clock_gettime")]
        public static extern int ClockGetTime(int clock, out Timespec time);

        [DllImport("libc", EntryPoint = "getpid")]
        public static extern int GetPid();

        [DllImport("libc", EntryPoint = "strerror")]
        public static extern IntPtr StrError(int code);
    }
}
