"""The determinism lint for C++ characterization tests (FR-7.3, SPEC §7.11 step 4).

A characterization test must give the same result on every run and every machine, so it may
not read the wall clock, use randomness, touch the filesystem or the network, or depend on
thread timing. The lint finds the standard and POSIX/Windows APIs for those in the test's own
text (comments and string literals blanked out); code the test calls is the project's.
"""

from __future__ import annotations

import re

from codecoremorph.rules.detectors import mask_c_family

# (pattern, what it does). A name is matched as a call or a use, not as part of another name;
# ``(?<![\w.>:])`` keeps member calls (``x.time()``) and qualified project names out.
_FREE = r"(?<![\w.>:])"
_CHECKS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(p), why)
    for p, why in [
        (r"\b(system_clock|steady_clock|high_resolution_clock|utc_clock|file_clock)\b",
         "reads the clock"),
        (_FREE + r"(std::)?(time|clock|gettimeofday|clock_gettime|localtime|gmtime|ctime"
         r"|localtime_r|gmtime_r|timespec_get|GetTickCount|GetTickCount64"
         r"|QueryPerformanceCounter|GetSystemTime|GetSystemTimeAsFileTime|GetLocalTime)\s*\(",
         "reads the clock"),
        (_FREE + r"(std::)?(rand|srand|random|srandom|drand48|lrand48|rand_r|arc4random)\s*\(",
         "uses randomness"),
        (r"\b(random_device|mt19937|mt19937_64|default_random_engine|minstd_rand0?"
         r"|ranlux24|ranlux48|knuth_b|random_shuffle)\b", "uses randomness"),
        (r"#\s*include\s*<random>", "uses randomness"),
        (r"#\s*include\s*<(filesystem|fstream|experimental/filesystem|dirent\.h|unistd\.h"
         r"|sys/stat\.h|io\.h|direct\.h)>", "uses the filesystem"),
        (r"\b(ifstream|ofstream|fstream|filesystem)\b", "uses the filesystem"),
        (_FREE + r"(std::)?(fopen|freopen|fopen_s|tmpfile|tmpnam|mkstemp|mkdtemp|unlink"
         r"|opendir|CreateFileA|CreateFileW|CreateFile|DeleteFileA|DeleteFileW|DeleteFile)\s*\(",
         "uses the filesystem"),
        (r"#\s*include\s*<(sys/socket\.h|netinet/\w+\.h|arpa/inet\.h|netdb\.h|winsock2?\.h"
         r"|ws2tcpip\.h|curl/curl\.h)>", "uses the network"),
        # Only names no project would use for its own functions; the headers catch the rest.
        (_FREE + r"(socket|getaddrinfo|gethostbyname|sendto|recvfrom|WSAStartup)\s*\(",
         "uses the network"),
        (r"\b(sleep_for|sleep_until|this_thread)\b", "depends on thread timing"),
        (_FREE + r"(sleep|usleep|nanosleep|Sleep|SleepEx)\s*\(", "depends on thread timing"),
        (r"\bstd::(thread|jthread|async)\b", "depends on thread timing"),
        (r"#\s*include\s*<(thread|future|pthread\.h|condition_variable)>",
         "depends on thread timing"),
        (_FREE + r"(pthread_create|CreateThread|_beginthreadex)\s*\(",
         "depends on thread timing"),
    ]
]  # fmt: skip


def nondeterminism(path: str, text: str) -> list[str]:
    """``path:line: what`` for each line of ``text`` that breaks the lint, once per reason."""
    masked = mask_c_family(text)
    # #include targets in angle brackets aren't literals, so the mask keeps them.
    found: list[str] = []
    for number, line in enumerate(masked.splitlines(), start=1):
        reasons: list[str] = []
        for pattern, why in _CHECKS:
            match = pattern.search(line)
            if match is not None and why not in reasons:
                reasons.append(why)
                found.append(f"{path}:{number}: {why} ({match.group(0).strip()})")
    return found
