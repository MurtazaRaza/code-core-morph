using System;
using System.Runtime.InteropServices;
using System.Text;

namespace Hostkit
{
    /// <summary>What the process can ask its operating system.</summary>
    public static class Host
    {
        private const uint FromSystem = 0x00001000; // FORMAT_MESSAGE_FROM_SYSTEM
        private const uint IgnoreInserts = 0x00000200; // FORMAT_MESSAGE_IGNORE_INSERTS

        /// <summary>The process's ID.</summary>
        public static int ProcessId()
        {
            return Native.Windows ? (int)Native.GetCurrentProcessId() : Native.GetPid();
        }

        /// <summary>The system's message for an error code (a Win32 error on Windows, an errno
        /// elsewhere), without a trailing newline or period.</summary>
        public static string ErrorMessage(int code)
        {
            string text;
            if (Native.Windows)
            {
                var buffer = new StringBuilder(512);
                int n = Native.FormatMessageW(
                    FromSystem | IgnoreInserts, IntPtr.Zero, (uint)code, 0, buffer,
                    buffer.Capacity, IntPtr.Zero);
                text = n > 0 ? buffer.ToString(0, n) : "Unknown error " + code;
            }
            else
            {
                text = Marshal.PtrToStringAnsi(Native.StrError(code)) ?? "Unknown error " + code;
            }
            return text.TrimEnd('\r', '\n', ' ', '.');
        }
    }
}
