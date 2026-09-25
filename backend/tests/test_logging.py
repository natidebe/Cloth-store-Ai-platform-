import json
import logging

from app.utils.logging import JsonFormatter, log_context


def _format(record_msg: str, **extra) -> dict:
    logger = logging.getLogger("test")
    record = logger.makeRecord("test", logging.INFO, __file__, 1, record_msg, (), None, extra=extra)
    return json.loads(JsonFormatter().format(record))


def test_json_line_has_message_and_extra_fields():
    entry = _format("llm call", model="gpt-5-mini", tokens=812)
    assert entry["msg"] == "llm call"
    assert entry["level"] == "INFO"
    assert entry["model"] == "gpt-5-mini"
    assert entry["tokens"] == 812


def test_log_context_is_added_and_removed():
    with log_context(store_id="store-1", telegram_id=42):
        inside = _format("inside")
    outside = _format("outside")
    assert inside["store_id"] == "store-1"
    assert inside["telegram_id"] == 42
    assert "store_id" not in outside
