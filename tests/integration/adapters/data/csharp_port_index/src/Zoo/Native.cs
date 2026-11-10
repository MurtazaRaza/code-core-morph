using System.Runtime.InteropServices;

namespace Zoo
{
    // What a port takes over from the C# runtime (D-82): native declarations, a type that calls
    // them and asks which platform it runs on, and an operating system's own API nothing calls.

    internal static class NativeMethods
    {
        internal const string Libc = "libc";

        [DllImport(Libc, EntryPoint = "getpid")]
        internal static extern int GetPid();
    }

    internal static class Win32
    {
        [DllImport("kernel32.dll")]
        internal static extern uint GetTickCount();
    }

    public class Clock
    {
        public int Process()
        {
            return NativeMethods.GetPid();
        }

        public bool OnWindows()
        {
            return RuntimeInformation.IsOSPlatform(OSPlatform.Windows);
        }
    }
}
