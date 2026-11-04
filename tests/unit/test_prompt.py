"""The gate prompt that gives way to a decision made elsewhere (FR-11.4)."""

from __future__ import annotations

import io
import select
import sys
import types

import pytest

from codecoremorph.cli import prompt


class FakeConsole:
    """``msvcrt``'s key functions over a queue of keys."""

    def __init__(self, keys: str):
        self.keys = list(keys)

    def kbhit(self) -> bool:
        return bool(self.keys)

    def getwch(self) -> str:
        return self.keys.pop(0)


def _windows(monkeypatch: pytest.MonkeyPatch, keys: str) -> None:
    console = FakeConsole(keys)
    module = types.ModuleType("msvcrt")
    module.kbhit = console.kbhit  # type: ignore[attr-defined]
    module.getwch = console.getwch  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "msvcrt", module)
    monkeypatch.setattr(prompt, "_KEY_WAIT_S", 0)


def _posix(monkeypatch: pytest.MonkeyPatch, typed: str | None) -> None:
    ready = typed is not None
    monkeypatch.setattr(select, "select", lambda r, w, x, t: (r if ready else [], [], []))
    monkeypatch.setattr(sys, "stdin", io.StringIO(typed or ""))


def test_windows_keys_make_a_line(monkeypatch: pytest.MonkeyPatch) -> None:
    _windows(monkeypatch, "yx\x08\xe0Hes\r")  # backspace, then an arrow key ignored
    assert prompt._windows_line(lambda: False, 0) == "yes"  # pyright: ignore[reportPrivateUsage]


def test_windows_prompt_gives_way_and_ctrl_c_interrupts(monkeypatch: pytest.MonkeyPatch) -> None:
    _windows(monkeypatch, "")
    checks = iter([False, False, True])
    assert prompt._windows_line(lambda: next(checks), 0) is None  # pyright: ignore[reportPrivateUsage]
    _windows(monkeypatch, "\x03")
    with pytest.raises(KeyboardInterrupt):
        prompt._windows_line(lambda: False, 0)  # pyright: ignore[reportPrivateUsage]


def test_posix_line_and_giving_way(monkeypatch: pytest.MonkeyPatch) -> None:
    _posix(monkeypatch, "n\n")
    assert prompt._posix_line(lambda: False, 0) == "n\n"  # pyright: ignore[reportPrivateUsage]
    _posix(monkeypatch, None)
    checks = iter([False, True])
    assert prompt._posix_line(lambda: next(checks), 0) is None  # pyright: ignore[reportPrivateUsage]
    _posix(monkeypatch, "")
    with pytest.raises(EOFError):
        prompt._posix_line(lambda: False, 0)  # pyright: ignore[reportPrivateUsage]


def test_ask_line_uses_the_default_and_reports_giving_way(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _windows(monkeypatch, "\r")
    _posix(monkeypatch, "\n")
    assert prompt.ask_line("Approve?", default="l", answered=lambda: False, poll_s=0) == "l"
    assert "Approve? [l]: " in capsys.readouterr().out
    _windows(monkeypatch, "")
    _posix(monkeypatch, None)
    assert prompt.ask_line("Approve?", default="l", answered=lambda: True, poll_s=0) is None
