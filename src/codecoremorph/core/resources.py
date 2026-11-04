"""The engine process's own resource use (NFR-1.2: resident memory stays at or below 4 GB).

Sandboxed toolchain processes and model servers don't count; they are other processes.
"""

from __future__ import annotations

import sys

MIB = 1 << 20


def peak_rss_bytes() -> int | None:
    """The most resident memory this process has used so far, in bytes; None where the
    operating system doesn't say."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class _Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        # A private instance with declared types: the process pseudo-handle is pointer-sized,
        # and ctypes would pass an undeclared one as a 32-bit int.
        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.GetCurrentProcess.argtypes = []
        memory_info = kernel32.K32GetProcessMemoryInfo
        memory_info.restype = wintypes.BOOL
        memory_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Counters), wintypes.DWORD]
        counters = _Counters()
        counters.cb = ctypes.sizeof(counters)
        if not memory_info(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return None
        return int(counters.PeakWorkingSetSize)
    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak) if sys.platform == "darwin" else int(peak) * 1024  # Linux reports KiB
