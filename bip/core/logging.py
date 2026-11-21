"""Structured logging with mandatory redaction.

:func:`configure_logging` installs one handler on the ``bip`` logger. Every record passes
through :class:`RedactingFilter` before formatting, so secrets are removed from the message,
its arguments, extra fields and exception text. Output is JSON lines (``json``) or a
human-readable line format (``text``).
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Literal, TextIO

from bip.security.secrets import REDACTED, is_sensitive_key, redact_text, redact_value

LogFormat = Literal["json", "text"]

# Attributes present on every LogRecord; anything else was passed via ``extra=``.
_STANDARD_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__.keys() | {"message", "asctime"}
)


class RedactingFilter(logging.Filter):
    """Redacts the rendered message, extra fields and exception text of every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except (TypeError, ValueError):
            message = str(record.msg)
        record.msg = redact_text(message)
        record.args = None
        for key, value in list(record.__dict__.items()):
            if key not in _STANDARD_ATTRS:
                setattr(record, key, REDACTED if is_sensitive_key(key) else redact_value(value))
        if record.exc_info:
            formatter = logging.Formatter()
            record.exc_text = redact_text(formatter.formatException(record.exc_info))
            record.exc_info = None
        if record.stack_info:
            record.stack_info = redact_text(record.stack_info)
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line with timestamp, level, logger, message and extra fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS:
                payload[key] = value
        if record.exc_text:
            payload["exception"] = record.exc_text
        if record.stack_info:
            payload["stack"] = record.stack_info
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(
    level: str = "INFO", fmt: LogFormat = "json", stream: TextIO | None = None
) -> logging.Logger:
    """Configure and return the ``bip`` root logger. Safe to call repeatedly."""
    logger = logging.getLogger("bip")
    logger.setLevel(level)
    logger.propagate = False
    for existing in list(logger.handlers):
        logger.removeHandler(existing)
    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.addFilter(RedactingFilter())
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    return logger


def get_logger(name: str) -> logging.Logger:
    """Return a child of the ``bip`` logger, e.g. ``get_logger(__name__)``."""
    return logging.getLogger(name if name.startswith("bip") else f"bip.{name}")
