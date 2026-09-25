import logging

from app.utils.logging import KeyValueFormatter, log_context


def _format(msg: str, **extra) -> str:
    record = logging.makeLogRecord({"name": "test", "levelname": "INFO", "msg": msg, **extra})
    return KeyValueFormatter().format(record)


def test_extra_fields_are_appended():
    line = _format("order created", order_id="abc")
    assert line.endswith("INFO     test order created order_id=abc")


def test_log_context_fields_are_included_and_removed_after():
    with log_context(store_id="s1"):
        assert "store_id=s1" in _format("hello")
    assert "store_id" not in _format("hello")


def test_values_with_spaces_are_quoted():
    assert 'customer_name="Abebe Kebede"' in _format("x", customer_name="Abebe Kebede")
