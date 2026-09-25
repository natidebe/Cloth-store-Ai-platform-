"""Structured (JSON) logging with per-request context.

Usage:
    with log_context(store_id=store.id, telegram_id=msg.telegram_id):
        logger.info("message received")   # includes store_id and telegram_id

    logger.info("llm call", extra={"model": "gpt-5-mini", "tokens": 812})
"""
import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Iterator

_context: ContextVar[dict[str, Any]] = ContextVar("log_context", default={})

# Attributes every LogRecord has; anything else came from `extra=`
_STANDARD_ATTRS = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        entry.update(_context.get())
        entry.update(
            {k: v for k, v in vars(record).items() if k not in _STANDARD_ATTRS}
        )
        if record.exc_info:
            entry["error"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str, ensure_ascii=False)


@contextmanager
def log_context(**fields: Any) -> Iterator[None]:
    """Attach fields (store_id, telegram_id, ...) to every log line inside the block."""
    token = _context.set({**_context.get(), **fields})
    try:
        yield
    finally:
        _context.reset(token)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
