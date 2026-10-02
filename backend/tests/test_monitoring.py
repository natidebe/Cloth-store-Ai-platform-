"""Error tracking (Sentry): off without a DSN, and nothing secret or personal
is sent: bot tokens, our keys, Mini App login data, webhook secrets, phones."""
import json
import logging

import pytest
import sentry_sdk
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sentry_sdk.transport import Transport

from app.core.config import Settings
from app.core.monitoring import init_sentry, scrub, scrub_text

TOKEN = "1234567890:AAH" + "x" * 32
LLM_KEY = "AIzaSyFAKE-llm-key-0123456789"
# Defined here, away from the crash: Sentry also sends the source lines around it.
PHONE = "09" + "11223344"
INIT_DATA = "user=%7B%22id%22%3A42%7D&" + "hash=" + "abc"


def settings(**values) -> Settings:
    return Settings(_env_file=None, **values)


def test_off_without_a_dsn():
    assert init_sentry(settings()) is False


def test_scrub_text_removes_tokens_secrets_and_phones():
    text = (f"POST https://api.telegram.org/bot{TOKEN}/sendMessage failed; "
            f"key {LLM_KEY}; customer 0911223344 or +251911223344 or 251711223344")
    clean = scrub_text(text, [LLM_KEY])
    assert TOKEN not in clean and LLM_KEY not in clean
    assert "0911223344" not in clean and "251911223344" not in clean and "251711223344" not in clean
    assert "/bot[bot-token]/sendMessage" in clean and clean.count("[phone]") == 3


def test_scrub_keeps_ordinary_numbers():
    assert scrub_text("order AB12CD, total 3500, store 42, 2026-10-02") == \
        "order AB12CD, total 3500, store 42, 2026-10-02"


def test_scrub_removes_secret_headers_everywhere():
    event = {
        "request": {"headers": {"X-Telegram-Init-Data": "user=...&hash=abc",
                                "x-telegram-bot-api-secret-token": "s3cret",
                                "X-Webhook-Secret": "s3cret", "Content-Type": "application/json"}},
        "breadcrumbs": {"values": [{"message": f"GET /bot{TOKEN}/getMe"}]},
    }
    clean = scrub(event)
    headers = clean["request"]["headers"]
    assert headers["X-Telegram-Init-Data"] == headers["X-Webhook-Secret"] == "[Filtered]"
    assert headers["x-telegram-bot-api-secret-token"] == "[Filtered]"
    assert headers["Content-Type"] == "application/json"
    assert TOKEN not in json.dumps(clean)


class Capture(Transport):
    def __init__(self, options=None):
        super().__init__(options)
        self.events: list[dict] = []

    def capture_envelope(self, envelope):
        for item in envelope.items:
            if item.type == "event":
                self.events.append(item.payload.json)


@pytest.fixture
def sentry(monkeypatch):
    """Real Sentry, but events go to a list instead of sentry.io."""
    transport = Capture()
    real_init = sentry_sdk.init
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: real_init(**kw, transport=transport))
    yield transport
    real_init()  # back to off for the other tests


def test_a_crash_is_reported_without_secrets(sentry):
    started = init_sentry(settings(sentry_dsn=SecretStr("https://public@o1.ingest.sentry.io/1"),
                                   llm_api_key=SecretStr(LLM_KEY)))
    assert started
    app = FastAPI()

    @app.post("/boom")
    async def boom():
        logging.getLogger("test").error("Telegram said no for bot %s (customer %s)", TOKEN, PHONE)
        raise RuntimeError(f"calling with {LLM_KEY} failed")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.post("/boom", json={"phone": PHONE}, headers={"X-Telegram-Init-Data": INIT_DATA})
    assert response.status_code == 500
    sentry_sdk.flush()

    sent = json.dumps(sentry.events)
    assert len(sentry.events) == 2  # the logged error and the crash
    assert "RuntimeError" in sent and "Telegram said no" in sent
    for secret in (TOKEN, LLM_KEY, PHONE, INIT_DATA, "hash=" + "abc"):
        assert secret not in sent
