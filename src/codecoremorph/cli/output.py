"""Console output for the CLI. The only layer that prints.

Output stays ASCII so it survives legacy Windows code pages and redirected pipes. User data is
printed with markup disabled, so text like ``rules[0]`` is never read as Rich markup.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Literal

import typer
from rich.console import Console
from rich.text import Text

from codecoremorph.config import ConfigValidationError, IssueSeverity
from codecoremorph.core.errors import CodeCoreMorphError

Status = Literal["ok", "warn", "fail", "info"]
_STATUS_STYLE: dict[Status, tuple[str, str]] = {
    "ok": ("OK", "green"),
    "warn": ("WARN", "yellow"),
    "fail": ("FAIL", "bold red"),
    "info": ("INFO", "cyan"),
}


def stdout() -> Console:
    # Created per call: Rich binds color detection at construction, and tests swap streams.
    return Console(highlight=False, soft_wrap=True)


def stderr() -> Console:
    return Console(stderr=True, highlight=False, soft_wrap=True)


def status_label(status: Status) -> Text:
    label, style = _STATUS_STYLE[status]
    return Text(f"{label:<4}", style=style)


def print_error(exc: CodeCoreMorphError) -> None:
    console = stderr()
    if isinstance(exc, ConfigValidationError):
        for issue in exc.issues:
            style = "red" if issue.severity is IssueSeverity.ERROR else "yellow"
            console.print(Text(issue.render(exc.source), style=style))
    console.print(Text.assemble(("error: ", "bold red"), exc.message))
    if exc.reason:
        console.print(Text(f"  why:  {exc.reason}"))
    if exc.hint:
        console.print(Text(f"  next: {exc.hint}"))


def handle_errors[**P, R](command: Callable[P, R]) -> Callable[P, R]:
    """Render CodeCoreMorph errors and exit with their exit code instead of a traceback."""

    @functools.wraps(command)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return command(*args, **kwargs)
        except CodeCoreMorphError as exc:
            print_error(exc)
            raise typer.Exit(int(exc.exit_code)) from exc

    return wrapper
