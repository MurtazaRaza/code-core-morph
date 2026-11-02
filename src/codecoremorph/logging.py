"""Logging setup: a Rich console handler, a JSON-lines file handler and secret redaction.

Modules log through the standard library (``logging.getLogger(__name__)``). The CLI calls
:func:`configure_logging` once. Every handler installed here carries a
:class:`RedactionFilter`, so credentials never reach the console or the log file (NFR-5.3).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

from codecoremorph.core.secrets import Redactor

PACKAGE_LOGGER = "codecoremorph"
_HANDLER_MARK = "_codecoremorph_handler"
_STANDARD_ATTRS = frozenset(vars(logging.LogRecord("x", 0, "x", 0, "x", None, None))) | {
    "message",
    "asctime",
}


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


class RedactionFilter(logging.Filter):
    """Replaces credentials in a record's message, traceback and string extras.

    The message is rendered here (and ``args`` cleared), because an argument such as a URL
    with a token in it is only visible after formatting.
    """

    def __init__(self, redactor: Redactor | None = None):
        super().__init__()
        self.redactor = redactor if redactor is not None else Redactor.from_environment()

    def filter(self, record: logging.LogRecord) -> bool:
        redact = self.redactor.redact
        try:
            message = record.getMessage()
        except (TypeError, ValueError):  # bad %-format args: keep everything, redacted
            message = f"{record.msg} {record.args}"
        record.msg = redact(message)
        record.args = None
        if record.exc_info:
            if not record.exc_text:
                record.exc_text = logging.Formatter().formatException(record.exc_info)
            # Formatters fall back to exc_text; dropping exc_info stops any handler from
            # re-rendering the raw traceback (with locals) around the redaction.
            record.exc_info = None
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        for key, value in list(vars(record).items()):
            if key not in _STANDARD_ATTRS and isinstance(value, str):
                setattr(record, key, redact(value))
        return True


class JsonLinesFormatter(logging.Formatter):
    """One JSON object per line: ``ts``, ``level``, ``logger``, ``message``, ``exc``, ``extra``."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            payload["exc"] = record.exc_text
        extra = {
            key: value if isinstance(value, str | int | float | bool | None) else repr(value)
            for key, value in vars(record).items()
            if key not in _STANDARD_ATTRS
        }
        if extra:
            payload["extra"] = extra
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(
    *,
    level: int | str = logging.INFO,
    console: Console | None = None,
    json_log_path: Path | None = None,
    redactor: Redactor | None = None,
) -> None:
    """Install CodeCoreMorph's handlers on the root logger. Safe to call more than once.

    The console shows CodeCoreMorph records at ``level`` and third-party records at WARNING and
    above. The JSON file, when given, records CodeCoreMorph at DEBUG.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_MARK, False):
            root.removeHandler(handler)
            handler.close()

    redaction = RedactionFilter(redactor)
    console_handler = RichHandler(
        console=console if console is not None else Console(stderr=True),
        show_time=False,
        show_path=False,
        markup=False,
        rich_tracebacks=False,
    )
    console_handler.setLevel(level)
    handlers: list[logging.Handler] = [console_handler]

    if json_log_path is not None:
        json_log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(json_log_path, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(JsonLinesFormatter())
        handlers.append(file_handler)

    for handler in handlers:
        handler.addFilter(redaction)
        setattr(handler, _HANDLER_MARK, True)
        root.addHandler(handler)

    root.setLevel(logging.WARNING)
    package_level = logging.DEBUG if json_log_path is not None else level
    logging.getLogger(PACKAGE_LOGGER).setLevel(package_level)
