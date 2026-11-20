namespace Hostkit
{
    /// <summary>ANSI colors on a terminal.</summary>
    public static class Terminal
    {
        private const int StdOutput = -11; // STD_OUTPUT_HANDLE
        private const uint VirtualTerminal = 0x0004; // ENABLE_VIRTUAL_TERMINAL_PROCESSING

        /// <summary>Lets the console show ANSI colors: Windows' console needs to be asked, other
        /// terminals show them as they are. False when the output is no console.</summary>
        public static bool EnableColors()
        {
            if (!Native.Windows)
            {
                return true;
            }
            var handle = Native.GetStdHandle(StdOutput);
            if (!Native.GetConsoleMode(handle, out uint mode))
            {
                return false;
            }
            return (mode & VirtualTerminal) != 0 || Native.SetConsoleMode(handle, mode | VirtualTerminal);
        }

        /// <summary><paramref name="text"/> in one of the eight ANSI colors (0 to 7).</summary>
        public static string Paint(string text, int color)
        {
            return "\u001b[3" + (color & 7) + "m" + text + "\u001b[0m";
        }
    }
}
