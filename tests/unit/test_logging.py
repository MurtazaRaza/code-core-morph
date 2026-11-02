from __future__ import annotations

import io
import json
import logging
from pathlib import Path

import pytest
from rich.console import Console

from codecoremorph.core.secrets import Redactor
from codecoremorph.logging import RedactionFilter, configure_logging

SECRET = "sk-ant-api03-" + "Zz9" * 10

pytestmark = pytest.mark.usefixtures("restore_logging")


def _configure(tmp_path: Path, level: int = logging.INFO) -> tuple[io.StringIO, Path]:
    buffer = io.StringIO()
    log_path = tmp_path / "logs" / "run.jsonl"
    configure_logging(
        level=level,
        console=Console(file=buffer, width=200, color_system=None),
        json_log_path=log_path,
        redactor=Redactor.with_literals(["opaque-literal-value"]),
    )
    return buffer, log_path


def _flush() -> None:
    for handler in logging.getLogger().handlers:
        handler.flush()


def test_secrets_are_redacted_in_console_and_file(tmp_path: Path) -> None:
    console, log_path = _configure(tmp_path)
    log = logging.getLogger("codecoremorph.test")
    log.info("calling with key %s and %s", SECRET, "opaque-literal-value")
    log.warning("url https://user:hunter2hunter2@example.com", extra={"detail": SECRET})
    _flush()

    text = console.getvalue() + log_path.read_text(encoding="utf-8")
    assert SECRET not in text
    assert "opaque-literal-value" not in text
    assert "hunter2hunter2" not in text
    assert "[REDACTED]" in console.getvalue()

    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert records[0]["message"] == "calling with key [REDACTED] and [REDACTED]"
    assert records[0]["level"] == "INFO"
    assert records[0]["logger"] == "codecoremorph.test"
    assert records[1]["extra"] == {"detail": "[REDACTED]"}


def test_tracebacks_are_redacted(tmp_path: Path) -> None:
    console, log_path = _configure(tmp_path)
    try:
        raise RuntimeError(f"bad key {SECRET}")
    except RuntimeError:
        logging.getLogger("codecoremorph.test").exception("request failed")
    _flush()
    text = console.getvalue() + log_path.read_text(encoding="utf-8")
    assert SECRET not in text
    assert "RuntimeError: bad key [REDACTED]" in text


def test_levels(tmp_path: Path) -> None:
    console, log_path = _configure(tmp_path, level=logging.INFO)
    logging.getLogger("codecoremorph.test").debug("debug detail")
    logging.getLogger("thirdparty").info("noisy library info")
    logging.getLogger("thirdparty").warning("library warning")
    _flush()
    assert "debug detail" not in console.getvalue()
    assert "debug detail" in log_path.read_text(encoding="utf-8")
    assert "noisy library info" not in console.getvalue()
    assert "library warning" in console.getvalue()


def test_reconfiguring_does_not_duplicate_handlers(tmp_path: Path) -> None:
    _configure(tmp_path)
    count = len(logging.getLogger().handlers)
    _configure(tmp_path)
    assert len(logging.getLogger().handlers) == count


def test_filter_survives_bad_format_arguments() -> None:
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "%d items", ("many",), None)
    assert RedactionFilter(Redactor()).filter(record)
    assert "many" in record.getMessage()
