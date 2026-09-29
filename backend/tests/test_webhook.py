"""Webhook + Telegram tests. No real Telegram or Supabase: both are faked."""
import json
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agents.orchestrator import Orchestrator
from app.api.v1.webhook import SECRET_HEADER, get_db, get_orchestrator
from app.main import app
from app.models.schemas import Customer, Store, TelegramUpdate
from app.services.conversation_service import InMemoryConversationStore
from app.services.llm_service import LLMProvider, LLMResponse
from app.services.supabase_service import DatabaseUnavailableError
from app.services.telegram_service import (
    TELEGRAM_API,
    TelegramError,
    TelegramService,
    parse_update,
)

BOT_TOKEN = "123456:TEST-TOKEN"
SECRET = "correct-secret"
STORE = Store(id=uuid4(), name="Selam Shoes", telegram_bot_token=BOT_TOKEN, webhook_secret=SECRET)


class EchoLLM(LLMProvider):
    """Stands in for the AI: repeats the customer's last message."""
    model = "echo"

    def __init__(self):
        self.requests = []  # the messages the AI was shown, per call

    async def _complete(self, system_prompt, messages, tools):
        self.requests.append(list(messages))
        last = next(m.content for m in reversed(messages) if m.role == "user")
        return LLMResponse(text=f"You said: {last}", model=self.model, stop_reason="stop")


class FakeDb:
    def __init__(self, store=STORE, error=None):
        self.store, self.error = store, error

    async def get_store(self, store_id):
        if self.error:
            raise self.error
        return self.store if self.store and store_id == self.store.id else None

    async def list_products(self, store_id, limit=100):
        return []

    async def get_or_create_customer(self, store_id, telegram_id, name=None):
        return Customer(id=uuid4(), store_id=store_id, telegram_id=telegram_id, name=name)


class FakeTelegram:
    """Records every Telegram call; answers like Telegram would."""

    def __init__(self, reply=None):
        self.calls = []
        self.reply = reply or {"ok": True, "result": {}}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.url.path, json.loads(request.content or b"{}")))
        return httpx.Response(200, json=self.reply)

    def service(self) -> TelegramService:
        return TelegramService(httpx.AsyncClient(
            base_url=TELEGRAM_API, transport=httpx.MockTransport(self.handler)))


@pytest.fixture
def setup():
    def _setup(db=None, telegram=None, conversations=None):
        telegram = telegram or FakeTelegram()
        db = db or FakeDb()
        orchestrator = Orchestrator(
            db, conversations or InMemoryConversationStore(), telegram.service(), EchoLLM(),
            burst_wait=0,
        )
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_orchestrator] = lambda: orchestrator
        return TestClient(app), telegram
    yield _setup
    app.dependency_overrides.clear()


def _update(**message_fields):
    message = {
        "message_id": 1, "date": 1790000000,
        "chat": {"id": 42, "type": "private"},
        "from": {"id": 42, "is_bot": False, "first_name": "Abebe"},
    }
    message.update(message_fields)
    return {"update_id": 1001, "message": message}


def _post(client, body, secret=SECRET, store_id=None):
    headers = {SECRET_HEADER: secret} if secret else {}
    return client.post(f"/api/v1/webhook/{store_id or STORE.id}", json=body, headers=headers)


# --- The webhook ------------------------------------------------------------

def test_text_is_echoed_with_the_stores_bot(setup):
    client, telegram = setup()
    response = _post(client, _update(text="Do you have AF1 in 42?"))
    assert response.status_code == 200
    assert telegram.calls == [
        (f"/bot{BOT_TOKEN}/sendMessage", {"chat_id": 42, "text": "You said: Do you have AF1 in 42?"}),
    ]


@pytest.mark.parametrize("secret", ["wrong-secret", None, ""])
def test_wrong_or_missing_secret_is_rejected(setup, secret):
    client, telegram = setup()
    assert _post(client, _update(text="hi"), secret=secret).status_code == 401
    assert telegram.calls == []


def test_store_without_secret_rejects_everything(setup):
    no_secret = STORE.model_copy(update={"webhook_secret": None})
    client, telegram = setup(db=FakeDb(store=no_secret))
    assert _post(client, _update(text="hi"), secret="anything").status_code == 401
    assert telegram.calls == []


def test_unknown_or_inactive_store_is_404(setup):
    client, telegram = setup()
    assert _post(client, _update(text="hi"), store_id=uuid4()).status_code == 404
    client, telegram = setup(db=FakeDb(store=None))  # get_store returns None for inactive
    assert _post(client, _update(text="hi")).status_code == 404
    assert telegram.calls == []


def test_database_down_asks_telegram_to_retry(setup):
    client, telegram = setup(db=FakeDb(error=DatabaseUnavailableError("database_unavailable")))
    assert _post(client, _update(text="hi")).status_code == 503
    assert telegram.calls == []


def test_non_text_is_described_to_the_ai(setup):
    client, telegram = setup()
    sticker = {"file_id": "s", "file_unique_id": "u"}
    assert _post(client, _update(sticker=sticker)).status_code == 200
    assert telegram.calls[0][1]["text"] == "You said: [The customer sent a sticker]"


@pytest.mark.parametrize("body", [
    _update(text="hi", chat={"id": -100123, "type": "supergroup"}),       # group message
    _update(text="hi", **{"from": {"id": 7, "is_bot": True, "first_name": "Bot"}}),  # another bot
    {"update_id": 5, "edited_message": _update(text="edited")["message"]},  # edit
    {"not": "an update"},                                                   # malformed
])
def test_ignored_updates_still_get_200(setup, body):
    client, telegram = setup()
    assert _post(client, body).status_code == 200
    assert telegram.calls == []


def test_telegram_failure_does_not_break_the_webhook(setup):
    client, telegram = setup(telegram=FakeTelegram(reply={"ok": False, "description": "Forbidden: bot was blocked by the user"}))
    assert _post(client, _update(text="hi")).status_code == 200
    assert len(telegram.calls) == 1


# --- parse_update -----------------------------------------------------------

def test_parse_photo_uses_largest_size_and_caption():
    update = TelegramUpdate.model_validate(_update(caption="payment", photo=[
        {"file_id": "small", "file_unique_id": "a", "width": 90, "height": 90},
        {"file_id": "large", "file_unique_id": "b", "width": 1280, "height": 1280},
    ]))
    message = parse_update(STORE.id, update)
    assert (message.kind, message.photo_file_id, message.text) == ("photo", "large", "payment")
    assert message.store_id == STORE.id and message.telegram_id == 42


# --- TelegramService --------------------------------------------------------

async def _call_with(handler, method):
    service = TelegramService(httpx.AsyncClient(base_url=TELEGRAM_API, transport=httpx.MockTransport(handler)))
    try:
        return await getattr(service, method)(BOT_TOKEN)
    finally:
        await service.close()


@pytest.mark.anyio
async def test_telegram_error_has_description_but_never_the_token():
    def refuse(request):
        return httpx.Response(401, json={"ok": False, "description": "Unauthorized"})

    def network_down(request):
        raise httpx.ConnectError(f"cannot connect to {request.url}", request=request)

    for handler, expected in [(refuse, "Unauthorized"), (network_down, "ConnectError")]:
        with pytest.raises(TelegramError) as error:
            await _call_with(handler, "get_me")
        assert error.value.description == expected
        assert BOT_TOKEN not in str(error.value)
        assert error.value.__cause__ is None  # the original error (with the URL) is dropped


@pytest.mark.anyio
async def test_long_messages_are_cut_to_telegram_limit():
    sent = []

    def record(request):
        sent.append(json.loads(request.content)["text"])
        return httpx.Response(200, json={"ok": True, "result": {}})

    service = TelegramService(httpx.AsyncClient(base_url=TELEGRAM_API, transport=httpx.MockTransport(record)))
    await service.send_message(BOT_TOKEN, 42, "x" * 5000)
    await service.close()
    assert len(sent[0]) == 4096


@pytest.fixture
def anyio_backend():
    return "asyncio"
