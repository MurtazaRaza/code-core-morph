"""A terminal prompt that gives way when the question is answered elsewhere.

A run's gate prompt waits for a line on the terminal, but the same gate can be answered from
the dashboard or ``codecoremorph approve`` in another terminal (FR-11.4). ``ask_line`` reads
keys without blocking (``msvcrt`` on Windows, ``select`` elsewhere) and checks ``answered``
between them, so whichever answer comes first wins.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable

POLL_S = 0.5  # how often ``answered`` is checked (it reads the state database)
_KEY_WAIT_S = 0.05


def ask_line(
    question: str, *, default: str, answered: Callable[[], bool], poll_s: float = POLL_S
) -> str | None:
    """The line typed (``default`` if empty), or None once ``answered()`` is true."""
    out = sys.stdout
    out.write(f"{question} [{default}]: ")
    out.flush()
    if sys.platform == "win32":
        line = _windows_line(answered, poll_s)
    else:
        line = _posix_line(answered, poll_s)
    if line is None:
        out.write("\n")
        out.flush()
        return None
    return line.strip() or default


def _posix_line(answered: Callable[[], bool], poll_s: float) -> str | None:
    import select

    while True:
        ready, _, _ = select.select([sys.stdin], [], [], poll_s)
        if ready:
            line = sys.stdin.readline()
            if not line:
                raise EOFError
            return line
        if answered():
            return None


def _windows_line(answered: Callable[[], bool], poll_s: float) -> str | None:
    import msvcrt

    typed: list[str] = []
    last_check = time.monotonic()
    while True:
        while msvcrt.kbhit():
            key = msvcrt.getwch()
            if key in ("\r", "\n"):
                sys.stdout.write("\n")
                sys.stdout.flush()
                return "".join(typed)
            if key == "\x03":
                raise KeyboardInterrupt
            if key in ("\x00", "\xe0"):  # an arrow or function key: its second code follows
                msvcrt.getwch()
                continue
            if key == "\x08":
                if typed:
                    typed.pop()
                    sys.stdout.write("\b \b")
            else:
                typed.append(key)
                sys.stdout.write(key)
            sys.stdout.flush()
        now = time.monotonic()
        if now - last_check >= poll_s:
            last_check = now
            if answered():
                return None
        time.sleep(_KEY_WAIT_S)


__all__ = ["ask_line"]
