"""Structured logging.

Every log line is `timestamp level logger message key=value ...`, so it's
readable in a terminal and still easy to search/filter by field.

Two ways to attach fields:

    logger.info("order created", extra={"order_id": order.id})

    with log_context(store_id=store_id, telegram_id=telegram_id):
        ...  # every log line inside here (including in called functions)
             # automatically gets store_id=... telegram_id=...
"""
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

_context: ContextVar[dict[str, Any]] = ContextVar("log_context", default={})

# Attributes every LogRecord has; anything else came from `extra=`.
_STANDARD_ATTRS = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}


@contextmanager
def log_context(**fields: Any) -> Iterator[None]:
    """Attach fields to every log line emitted inside this block."""
    token = _context.set({**_context.get(), **fields})
    try:
        yield
    finally:
        _context.reset(token)


def _format_value(value: Any) -> str:
    text = str(value)
    if not text or any(c in text for c in ' ="'):
        text = '"' + text.replace('"', '\\"') + '"'
    return text


class KeyValueFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = dict(_context.get())
        fields.update(
            {k: v for k, v in vars(record).items() if k not in _STANDARD_ATTRS}
        )
        line = (
            f"{self.formatTime(record, '%Y-%m-%d %H:%M:%S')} "
            f"{record.levelname:<8} {record.name} {record.getMessage()}"
        )
        if fields:
            line += " " + " ".join(f"{k}={_format_value(v)}" for k, v in fields.items())
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(KeyValueFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    # httpx logs every request URL at INFO — that would include Telegram bot
    # tokens (they're part of the URL), so keep it quiet.
    logging.getLogger("httpx").setLevel(logging.WARNING)
