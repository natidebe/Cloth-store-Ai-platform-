"""Phase 7: conversation memory, the inbox, one customer at a time, recovery.

Everything is faked: the in-memory conversation store, a fake database for
stores/customers, and a fake Telegram. No network, no cost.
"""
import asyncio
import json
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agents.messages import both, t
from app.agents.orchestrator import Orchestrator
from app.api.v1.webhook import SECRET_HEADER, get_db, get_orchestrator
from app.main import app
from app.models.schemas import ChatMessage, Customer, DraftItem, OrderDraft, Store
from app.services.conversation_service import (
    MAX_ATTEMPTS,
    CustomerLocks,
    InMemoryConversationStore,
    utc_now,
)
from app.services.llm_service import LLMProvider, LLMResponse
from app.services.supabase_service import DatabaseUnavailableError, VersionConflictError
from app.services.telegram_service import TELEGRAM_API, TelegramService

pytestmark = pytest.mark.anyio

BOT_TOKEN = "123456:TEST-TOKEN"
SECRET = "correct-secret"
STAFF_CHAT = -100555
STORE = Store(id=uuid4(), name="Selam Shoes", telegram_bot_token=BOT_TOKEN,
              webhook_secret=SECRET, staff_chat_id=STAFF_CHAT)
OTHER_STORE = Store(id=uuid4(), name="Other Store", telegram_bot_token="999:OTHER",
                    webhook_secret="other-secret")
CUSTOMER = 42


class EchoLLM(LLMProvider):
    """Stands in for the AI: repeats the customer's last message."""
    model = "echo"

    def __init__(self):
        self.requests = []  # the messages the AI was shown, per call

    async def _complete(self, system_prompt, messages, tools):
        self.requests.append(list(messages))
        last = next(m.content for m in reversed(messages) if m.role == "user")
        return LLMResponse(text=f"You said: {last}", model=self.model, stop_reason="stop")


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakeDb:
    def __init__(self, stores=(STORE, OTHER_STORE), customer_error=None):
        self.stores = {s.id: s for s in stores}
        self.customer_error = customer_error

    async def get_store(self, store_id):
        return self.stores.get(store_id)

    async def list_products(self, store_id, limit=100):
        return []

    async def get_or_create_customer(self, store_id, telegram_id, name=None):
        if self.customer_error:
            raise self.customer_error
        return Customer(id=uuid4(), store_id=store_id, telegram_id=telegram_id, name=name)


class FakeTelegram:
    def __init__(self, status=200, reply=None):
        self.sent = []  # (bot token, chat_id, text)
        self.status = status
        self.reply = reply or {"ok": True, "result": {}}

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        token = request.url.path.split("/")[1].removeprefix("bot")
        self.sent.append((token, body.get("chat_id"), body.get("text")))
        return httpx.Response(self.status, json=self.reply)

    def service(self) -> TelegramService:
        return TelegramService(httpx.AsyncClient(
            base_url=TELEGRAM_API, transport=httpx.MockTransport(self.handler)))

    def texts_to(self, chat_id):
        return [text for _, chat, text in self.sent if chat == chat_id]


def _update(update_id, text=None, customer=CUSTOMER, **fields):
    message = {
        "message_id": update_id, "date": 1790000000,
        "chat": {"id": customer, "type": "private"},
        "from": {"id": customer, "is_bot": False, "first_name": "Abebe"},
        **fields,
    }
    if text is not None:
        message["text"] = text
    return {"update_id": update_id, "message": message}


def _world(db=None, telegram=None, store=None, burst_wait=0.0):
    store = store or InMemoryConversationStore()
    telegram = telegram or FakeTelegram()
    orchestrator = Orchestrator(db or FakeDb(), store, telegram.service(), EchoLLM(),
                                burst_wait=burst_wait)
    return orchestrator, store, telegram


async def _receive(orchestrator, update, shop=STORE):
    """What the webhook does: save to the inbox."""
    telegram_id = update["message"]["from"]["id"]
    return await orchestrator.conversations.save_to_inbox(shop.id, update["update_id"], telegram_id, update)


async def _message(orchestrator, update, shop=STORE):
    await _receive(orchestrator, update, shop)
    await orchestrator.process_customer(shop, update["message"]["from"]["id"])


def _statuses(store):
    return [item.status for item in store.inbox.values()]


# --- Memory -----------------------------------------------------------------

async def test_bot_remembers_the_conversation():
    orchestrator, store, telegram = _world()
    await _message(orchestrator, _update(1, "hi"))
    await _message(orchestrator, _update(2, "white AF1?"))

    assert telegram.texts_to(CUSTOMER) == ["You said: hi", "You said: white AF1?"]
    # The second time, the AI saw the whole chat so far.
    assert [(m.role, m.content) for m in orchestrator.llm.requests[1]] == [
        ("user", "hi"), ("assistant", "You said: hi"), ("user", "white AF1?"),
    ]
    conversation = store.conversations[(STORE.id, CUSTOMER)]
    history = await store.get_recent_messages(STORE.id, conversation.id, limit=20)
    assert [(m.role, m.content) for m in history] == [
        ("customer", "hi"), ("assistant", "You said: hi"),
        ("customer", "white AF1?"), ("assistant", telegram.texts_to(CUSTOMER)[1]),
    ]
    assert conversation.version == 2  # saved once per run
    assert _statuses(store) == ["done", "done"]


async def test_only_recent_messages_are_used():
    orchestrator, store, telegram = _world()
    conversation = await store.get_or_create_conversation(STORE.id, CUSTOMER)
    await store.add_messages(STORE.id, conversation.id,
                             [ChatMessage(role="customer", content=f"old {i}") for i in range(30)])
    await _message(orchestrator, _update(1, "hi"))
    # Only the 20 most recent (including the new one) go to the AI.
    shown = orchestrator.llm.requests[0]
    assert len(shown) == 20
    assert (shown[0].content, shown[-1].content) == ("old 11", "hi")


async def test_expired_conversation_starts_fresh():
    orchestrator, store, telegram = _world()
    conversation = await store.get_or_create_conversation(STORE.id, CUSTOMER)
    await store.add_messages(STORE.id, conversation.id, [ChatMessage(role="customer", content="old")])
    for message in store.messages[conversation.id]:  # make the history 2 days old
        message[1].created_at = utc_now() - timedelta(days=2)
    conversation.order_draft = OrderDraft(items=[DraftItem(variant_id=uuid4(), description="AF1 white 42")])
    conversation.last_message_at = utc_now() - timedelta(hours=25)
    await store.save_conversation(conversation)

    await _message(orchestrator, _update(1, "hello again"))

    assert [m.content for m in orchestrator.llm.requests[0]] == ["hello again"]  # nothing remembered
    saved = store.conversations[(STORE.id, CUSTOMER)]
    assert saved.order_draft.items == []  # draft cleared
    assert saved.order_draft.revision == 1  # but its revision keeps counting
    assert len(store.messages[conversation.id]) == 3  # old message still kept for staff


async def test_non_text_is_described_to_the_ai_and_saved():
    orchestrator, store, telegram = _world()
    await _message(orchestrator, _update(1, sticker={"file_id": "s", "file_unique_id": "u"}))
    assert telegram.texts_to(CUSTOMER) == ["You said: [The customer sent a sticker]"]
    conversation = store.conversations[(STORE.id, CUSTOMER)]
    assert store.messages[conversation.id][0][1].kind == "sticker"


# --- Duplicates and bursts --------------------------------------------------

async def test_resent_update_is_handled_once():
    orchestrator, store, telegram = _world()
    assert await _receive(orchestrator, _update(1, "hi")) is True
    assert await _receive(orchestrator, _update(1, "hi")) is False
    await orchestrator.process_customer(STORE, CUSTOMER)
    await orchestrator.process_customer(STORE, CUSTOMER)
    assert telegram.texts_to(CUSTOMER) == ["You said: hi"]
    assert len(store.inbox) == 1


async def test_quick_burst_gets_one_reply():
    orchestrator, store, telegram = _world(burst_wait=0.05)

    async def arrive(update_id, text, delay):
        await asyncio.sleep(delay)
        await _receive(orchestrator, _update(update_id, text))
        await orchestrator.process_customer(STORE, CUSTOMER)

    # Three messages within the wait; each starts its own run, like the webhook does.
    await asyncio.gather(arrive(1, "white", 0), arrive(2, "size 42", 0.01), arrive(3, "0911223344", 0.02))

    assert telegram.texts_to(CUSTOMER) == ["You said: 0911223344"]  # one reply
    assert [m.content for m in orchestrator.llm.requests[0]] == ["white", "size 42", "0911223344"]
    assert _statuses(store) == ["done", "done", "done"]


async def test_burst_waits_only_once():
    # Each message starts its own run. Only the first should wait; the others
    # find their messages already handled and stop without waiting.
    wait = 0.3
    orchestrator, store, telegram = _world(burst_wait=wait)
    loop = asyncio.get_running_loop()

    async def arrive(update_id, text, delay):
        await asyncio.sleep(delay)
        await _receive(orchestrator, _update(update_id, text))
        await orchestrator.process_customer(STORE, CUSTOMER)

    started = loop.time()
    await asyncio.gather(*(arrive(i, f"msg {i}", i * 0.01) for i in range(1, 6)))
    elapsed = loop.time() - started

    assert telegram.texts_to(CUSTOMER) == ["You said: msg 5"]  # one reply
    assert [m.content for m in orchestrator.llm.requests[0]] == [f"msg {i}" for i in range(1, 6)]
    assert elapsed < 2 * wait  # was about 5 x wait before the fix


async def test_run_with_nothing_waiting_returns_at_once():
    orchestrator, store, telegram = _world(burst_wait=5)
    await asyncio.wait_for(orchestrator.process_customer(STORE, CUSTOMER), timeout=1)
    assert telegram.sent == []


async def test_one_customer_at_a_time_but_customers_in_parallel():
    locks = CustomerLocks()
    order = []

    async def run(telegram_id, name, hold):
        async with locks.hold(STORE.id, telegram_id):
            order.append(f"{name} start")
            await asyncio.sleep(hold)
            order.append(f"{name} end")

    await asyncio.gather(run(1, "A1", 0.05), run(1, "A2", 0), run(2, "B", 0))
    # A2 waits for A1; B (another customer) doesn't wait.
    assert order.index("A1 end") < order.index("A2 start")
    assert order.index("B end") < order.index("A1 end")
    assert not locks.is_busy(STORE.id, 1)
    assert locks._locks == {}  # cleaned up


# --- Version check ----------------------------------------------------------

class ConflictOnce(InMemoryConversationStore):
    """The first save fails as if staff changed the conversation meanwhile."""

    def __init__(self, pause=False):
        super().__init__()
        self.conflicts, self.pause = 0, pause

    async def save_conversation(self, conversation):
        if self.conflicts == 0:
            self.conflicts += 1
            if self.pause:
                self.pause_bot(conversation.store_id, conversation.telegram_id)
            raise VersionConflictError("version_conflict")
        return await super().save_conversation(conversation)


async def test_version_conflict_redoes_the_run():
    orchestrator, store, telegram = _world(store=ConflictOnce())
    await _message(orchestrator, _update(1, "hi"))
    assert store.conflicts == 1
    assert telegram.texts_to(CUSTOMER) == ["You said: hi"]  # one reply, not two


async def test_staff_taking_over_mid_run_stops_the_reply():
    orchestrator, store, telegram = _world(store=ConflictOnce(pause=True))
    await _message(orchestrator, _update(1, "hi"))
    assert telegram.texts_to(CUSTOMER) == []  # the bot doesn't answer...
    assert telegram.texts_to(STAFF_CHAT) == ["💬 Abebe (Telegram id 42):\nhi"]  # ...staff see it
    assert _statuses(store) == ["done"]


async def test_stale_copy_cannot_overwrite():
    store = InMemoryConversationStore()
    first = await store.get_or_create_conversation(STORE.id, CUSTOMER)
    second = await store.get_or_create_conversation(STORE.id, CUSTOMER)
    await store.save_conversation(first)
    with pytest.raises(VersionConflictError):
        await store.save_conversation(second)


# --- Paused bot -------------------------------------------------------------

async def test_paused_bot_stays_silent_but_saves_messages():
    orchestrator, store, telegram = _world()
    conversation = await store.get_or_create_conversation(STORE.id, CUSTOMER)
    store.pause_bot(STORE.id, CUSTOMER)
    await _message(orchestrator, _update(1, "I want a discount"))
    assert telegram.texts_to(CUSTOMER) == []
    # Staff see what the customer wrote, so they can Reply to it.
    assert telegram.texts_to(STAFF_CHAT) == ["💬 Abebe (Telegram id 42):\nI want a discount"]
    assert store.messages[conversation.id][0][1].content == "I want a discount"
    assert _statuses(store) == ["done"]


# --- Failures and recovery --------------------------------------------------

async def test_failure_is_retried_then_customer_and_staff_told():
    db = FakeDb(customer_error=DatabaseUnavailableError("database_unavailable"))
    orchestrator, store, telegram = _world(db=db)
    await _message(orchestrator, _update(1, "hi"))
    assert _statuses(store) == ["received"]  # back in line for a retry
    assert telegram.sent == []

    for _ in range(MAX_ATTEMPTS - 1):  # what the recovery sweep would do
        await orchestrator.process_customer(STORE, CUSTOMER)

    item = next(iter(store.inbox.values()))
    assert (item.status, item.attempts) == ("failed", MAX_ATTEMPTS)
    assert "DatabaseUnavailableError" in item.last_error
    # "hi" doesn't tell the language, so the apology comes in both.
    assert telegram.texts_to(CUSTOMER) == [both("fallback_reply")]
    assert "could not answer a customer" in telegram.texts_to(STAFF_CHAT)[0]


async def test_customer_blocked_the_bot_is_not_retried():
    telegram = FakeTelegram(status=403, reply={"ok": False, "description": "Forbidden: bot was blocked by the user"})
    orchestrator, store, _ = _world(telegram=telegram)
    await _message(orchestrator, _update(1, "hi"))
    assert _statuses(store) == ["done"]
    assert len(telegram.sent) == 1


async def test_telegram_down_is_retried():
    telegram = FakeTelegram(status=502, reply={"ok": False, "description": "Bad Gateway"})
    orchestrator, store, _ = _world(telegram=telegram)
    await _message(orchestrator, _update(1, "hi"))
    assert _statuses(store) == ["received"]


async def test_message_survives_a_crash():
    orchestrator, store, telegram = _world()
    await _receive(orchestrator, _update(1, "hi"))
    await store.claim_inbox(STORE.id, CUSTOMER)  # a run started... and the server died
    assert _statuses(store) == ["processing"]

    # The server starts again.
    restarted, _, _ = _world(store=store, telegram=telegram)
    assert await restarted.recover(startup=True) == 1
    await asyncio.gather(*restarted._tasks)

    assert telegram.texts_to(CUSTOMER) == ["You said: hi"]
    assert _statuses(store) == ["done"]


async def test_sweep_leaves_fresh_and_busy_work_alone():
    orchestrator, store, telegram = _world()
    await _receive(orchestrator, _update(1, "hi"))
    # Just arrived: its own run will handle it, the minute sweep shouldn't.
    assert await orchestrator.recover() == 0

    store.inbox[1].received_at -= timedelta(minutes=5)
    async with orchestrator.locks.hold(STORE.id, CUSTOMER):  # a run is already going
        assert await orchestrator.recover() == 0
    assert await orchestrator.recover() == 1
    await asyncio.gather(*orchestrator._tasks)
    assert telegram.texts_to(CUSTOMER) == ["You said: hi"]


async def test_sweep_fails_messages_of_a_switched_off_store():
    orchestrator, store, telegram = _world(db=FakeDb(stores=[OTHER_STORE]))
    await _receive(orchestrator, _update(1, "hi"))
    assert await orchestrator.recover(startup=True) == 0
    assert _statuses(store) == ["failed"]
    assert telegram.sent == []


# --- Stores stay separate ---------------------------------------------------

async def test_stores_are_separate():
    orchestrator, store, telegram = _world()
    # Each bot numbers its own updates, so the same update_id in two stores is fine.
    assert await _receive(orchestrator, _update(1, "for Selam")) is True
    assert await _receive(orchestrator, _update(1, "for Other"), shop=OTHER_STORE) is True

    # Store B can't claim, finish, or fail store A's messages.
    claimed_b = await store.claim_inbox(OTHER_STORE.id, CUSTOMER)
    assert [i.store_id for i in claimed_b] == [OTHER_STORE.id]
    a_row = next(i for i in store.inbox.values() if i.store_id == STORE.id)
    await store.finish_inbox(OTHER_STORE.id, [a_row.id])
    assert await store.release_inbox(OTHER_STORE.id, [a_row.id], "x", 1) == []
    assert a_row.status == "received"

    await orchestrator.process_customer(STORE, CUSTOMER)
    conv_a = store.conversations[(STORE.id, CUSTOMER)]
    assert await store.get_recent_messages(OTHER_STORE.id, conv_a.id, limit=20) == []
    # Replies go out with each store's own bot.
    assert telegram.sent[0][0] == BOT_TOKEN


# --- Through the webhook ----------------------------------------------------

@pytest.fixture
def client():
    def _client(conversations=None):
        telegram = FakeTelegram()
        orchestrator = Orchestrator(FakeDb(), conversations or InMemoryConversationStore(),
                                    telegram.service(), EchoLLM(), burst_wait=0)
        app.dependency_overrides[get_db] = lambda: orchestrator.db
        app.dependency_overrides[get_orchestrator] = lambda: orchestrator
        return TestClient(app), telegram, orchestrator.conversations
    yield _client
    app.dependency_overrides.clear()


def _post(client, body):
    return client.post(f"/api/v1/webhook/{STORE.id}", json=body, headers={SECRET_HEADER: SECRET})


def test_webhook_saves_before_answering_and_ignores_resends(client):
    http, telegram, store = client()
    assert _post(http, _update(7, "hi")).status_code == 200
    assert _post(http, _update(7, "hi")).status_code == 200  # Telegram resent it
    assert telegram.texts_to(CUSTOMER) == ["You said: hi"]
    assert [(i.update_id, i.status) for i in store.inbox.values()] == [(7, "done")]


def test_webhook_asks_for_a_resend_if_the_inbox_is_down(client):
    class InboxDown(InMemoryConversationStore):
        async def save_to_inbox(self, *args):
            raise DatabaseUnavailableError("database_unavailable")

    http, telegram, _ = client(InboxDown())
    assert _post(http, _update(7, "hi")).status_code == 503  # Telegram will retry
    assert telegram.sent == []


def test_webhook_does_not_store_ignored_updates(client):
    http, telegram, store = client()
    group = _update(8, "hi", chat={"id": -100123, "type": "supergroup"})
    assert _post(http, group).status_code == 200
    assert store.inbox == {}


async def test_failure_apology_is_in_the_customers_language():
    db = FakeDb(customer_error=DatabaseUnavailableError("database_unavailable"))
    orchestrator, store, telegram = _world(db=db)
    await _message(orchestrator, _update(1, "ጫማ አላችሁ?"))
    for _ in range(MAX_ATTEMPTS - 1):
        await orchestrator.process_customer(STORE, CUSTOMER)
    assert telegram.texts_to(CUSTOMER) == [t("fallback_reply", "am")]
