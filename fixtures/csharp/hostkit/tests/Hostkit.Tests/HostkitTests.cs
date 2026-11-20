using System;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Threading;
using NUnit.Framework;

namespace Hostkit.Tests
{
    public class ClockTests
    {
        [Test]
        public void NeverGoesBack()
        {
            long a = Clock.Nanoseconds();
            long b = Clock.Nanoseconds();
            Assert.That(b, Is.GreaterThanOrEqualTo(a));
        }

        [Test]
        public void MeasuresASleep()
        {
            long start = Clock.Nanoseconds();
            Thread.Sleep(30);
            Assert.That(Clock.MillisecondsSince(start), Is.InRange(25.0, 2000.0));
        }
    }

    public class HostTests
    {
        [Test]
        public void TheProcessId()
        {
            Assert.That(Host.ProcessId(), Is.EqualTo(Process.GetCurrentProcess().Id));
        }

        [Test]
        public void AnErrorMessage()
        {
            // ENOENT and ERROR_FILE_NOT_FOUND are both 2.
            string expected = RuntimeInformation.IsOSPlatform(OSPlatform.Windows)
                ? "The system cannot find the file specified"
                : "No such file or directory";
            Assert.That(Host.ErrorMessage(2), Is.EqualTo(expected));
        }
    }

    public class TerminalTests
    {
        [Test]
        public void PaintsInAnsiColors()
        {
            Assert.That(Terminal.Paint("ok", 2), Is.EqualTo("\u001b[32mok\u001b[0m"));
            Assert.That(Terminal.Paint("x", 9), Is.EqualTo("\u001b[31mx\u001b[0m"));
        }

        [Test]
        public void EnablingColorsDoesNotThrow()
        {
            Assert.DoesNotThrow(() => Terminal.EnableColors());
        }
    }
}
