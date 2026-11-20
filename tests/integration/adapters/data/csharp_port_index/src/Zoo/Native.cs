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
        // Platform checks, which a port built for its platform drops (D-94): still only
        // declarations.
        internal static bool Windows { get; } = RuntimeInformation.IsOSPlatform(OSPlatform.Windows);

        internal static bool Elsewhere => !RuntimeInformation.IsOSPlatform(OSPlatform.Windows);

        [DllImport("kernel32.dll")]
        internal static extern uint GetTickCount();
    }

    // A setting beside the declaration is no platform check: the type is ported.
    internal static class Settings
    {
        internal static readonly bool Verbose = System.Environment.GetEnvironmentVariable("ZOO_VERBOSE") != null;

        [DllImport("libc", EntryPoint = "getppid")]
        internal static extern int GetParent();
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
