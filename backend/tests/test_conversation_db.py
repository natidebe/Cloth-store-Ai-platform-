"""Tests for the migration 003 functions against a real Supabase project.

Skipped unless SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are set in
backend/.env AND 003_conversations.sql has been run. Use a test project,
not a live store's project.

Each run creates temporary stores named "__pytest_..." and deletes them at
the end (deleting a store deletes its inbox, conversations, and messages).
"""
from datetime import timedelta
from uuid import UUID, uuid4

import pytest

from app.core.config import get_settings
from app.models.schemas import ChatMessage, DraftItem, OrderDraft
from app.services.conversation_service import utc_now
from app.services.supabase_service import DatabaseError, SupabaseService, VersionConflictError

_settings = get_settings()
_configured = bool(
    _settings.supabase_url
    and _settings.supabase_service_role_key.get_secret_value()
    and "your-project-ref" not in _settings.supabase_url
)

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(not _configured, reason="Supabase not configured in backend/.env"),
]

TEST_PREFIX = "__pytest_"
CUSTOMER = 900000101


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


async def _insert(service: SupabaseService, table: str, row: dict) -> dict:
    return (await service._db.table(table).insert(row).execute()).data[0]


async def _delete_test_stores(service: SupabaseService) -> None:
    await service._db.table("stores").delete().like("name", f"{TEST_PREFIX}conv%").execute()


@pytest.fixture(scope="module")
async def world(anyio_backend):
    service = await SupabaseService.connect(
        _settings.supabase_url, _settings.supabase_service_role_key.get_secret_value()
    )
    try:
        await service._run(service._db.table("inbox").select("id").limit(1))
    except DatabaseError:
        await service.close()
        pytest.skip("migration 003_conversations.sql has not been run yet")

    await _delete_test_stores(service)
    store_a = UUID((await _insert(service, "stores", {"name": f"{TEST_PREFIX}conv_a"}))["id"])
    store_b = UUID((await _insert(service, "stores", {"name": f"{TEST_PREFIX}conv_b"}))["id"])
    for store in (store_a, store_b):
        await service.get_or_create_customer(store, CUSTOMER, "Abebe")

    yield {"service": service, "a": store_a, "b": store_b}

    await _delete_test_stores(service)
    await service.close()


def _payload(update_id: int) -> dict:
    return {"update_id": update_id, "message": {"message_id": 1, "text": "hi"}}


async def _inbox_status(world, store_id, update_id) -> str:
    rows = (await world["service"]._db.table("inbox").select("status")
            .eq("store_id", str(store_id)).eq("update_id", update_id).execute()).data
    return rows[0]["status"]


# --- Inbox ------------------------------------------------------------------

async def test_inbox_duplicates_and_stores(world):
    db, a, b = world["service"], world["a"], world["b"]
    assert await db.save_to_inbox(a, 1, CUSTOMER, _payload(1)) is True
    assert await db.save_to_inbox(a, 1, CUSTOMER, _payload(1)) is False  # resent
    assert await db.save_to_inbox(b, 1, CUSTOMER, _payload(1)) is True   # other bot, same number

    claimed = await db.claim_inbox(a, CUSTOMER)
    assert [(i.update_id, i.attempts, i.status) for i in claimed] == [(1, 1, "processing")]
    assert await db.claim_inbox(a, CUSTOMER) == []  # can't be claimed twice

    # Store B can't finish or fail store A's row.
    await db.finish_inbox(b, [claimed[0].id])
    assert await db.release_inbox(b, [claimed[0].id], "x", 3) == []
    assert await _inbox_status(world, a, 1) == "processing"

    await db.finish_inbox(a, [claimed[0].id])
    assert await _inbox_status(world, a, 1) == "done"
    assert await _inbox_status(world, b, 1) == "received"


async def test_has_waiting_inbox(world):
    db, a, b = world["service"], world["a"], world["b"]
    other_customer = CUSTOMER + 1
    await db.get_or_create_customer(a, other_customer, "Sara")
    assert await db.has_waiting_inbox(a, other_customer) is False
    await db.save_to_inbox(a, 50, other_customer, _payload(50))
    assert await db.has_waiting_inbox(a, other_customer) is True
    assert await db.has_waiting_inbox(b, other_customer) is False  # other store
    [item] = await db.claim_inbox(a, other_customer)
    assert await db.has_waiting_inbox(a, other_customer) is False  # processing, not waiting
    await db.finish_inbox(a, [item.id])


async def test_inbox_retry_then_fail(world):
    db, a = world["service"], world["a"]
    await db.save_to_inbox(a, 2, CUSTOMER, _payload(2))
    for attempt in (1, 2):
        [item] = await db.claim_inbox(a, CUSTOMER)
        [released] = await db.release_inbox(a, [item.id], "boom", max_attempts=2)
        expected = "received" if attempt == 1 else "failed"
        assert (released.attempts, released.status, released.last_error) == (attempt, expected, "boom")


async def test_recovery_finds_stuck_and_waiting(world):
    db, a = world["service"], world["a"]
    await db.save_to_inbox(a, 3, CUSTOMER, _payload(3))
    await db.claim_inbox(a, CUSTOMER)  # the run "crashed"

    assert await db.reset_stuck_inbox(utc_now() - timedelta(minutes=10)) == 0  # not stuck long enough
    assert await db.reset_stuck_inbox(utc_now() + timedelta(seconds=5)) >= 1
    assert await _inbox_status(world, a, 3) == "received"
    waiting = await db.find_waiting_inbox(utc_now() + timedelta(seconds=5))
    assert (a, CUSTOMER) in waiting

    for item in await db.claim_inbox(a, CUSTOMER):  # tidy up for the other tests
        await db.finish_inbox(a, [item.id])


# --- Conversations and messages ---------------------------------------------

async def test_conversation_version_check_and_draft(world):
    db, a, b = world["service"], world["a"], world["b"]
    first = await db.get_or_create_conversation(a, CUSTOMER)
    again = await db.get_or_create_conversation(a, CUSTOMER)
    assert first.id == again.id and first.version == 0
    assert (await db.get_or_create_conversation(b, CUSTOMER)).id != first.id

    first.order_draft = OrderDraft(items=[DraftItem(variant_id=uuid4(), quantity=2, description="AF1 white 42")],
                                   contact_phone="0911 22 33 44")
    first.last_message_at = utc_now()
    saved = await db.save_conversation(first)
    assert saved.version == 1
    assert saved.order_draft == first.order_draft  # survives the round trip

    with pytest.raises(VersionConflictError):  # `again` is an old copy
        await db.save_conversation(again)
    # A copy claiming to be in store B can't touch store A's conversation.
    with pytest.raises(VersionConflictError):
        await db.save_conversation(saved.model_copy(update={"store_id": b}))


async def test_conversation_needs_a_customer_of_the_same_store(world):
    with pytest.raises(DatabaseError):
        await world["service"].get_or_create_conversation(world["a"], 123)  # no such customer


async def test_messages_history(world):
    db, a, b = world["service"], world["a"], world["b"]
    conversation = await db.get_or_create_conversation(a, CUSTOMER)
    await db.add_messages(a, conversation.id, [
        ChatMessage(role="customer", content="white?", update_id=100, telegram_message_id=5),
        ChatMessage(role="assistant", content="Yes, in 42."),
    ])
    # A retry saves the customer message again: skipped.
    await db.add_messages(a, conversation.id, [ChatMessage(role="customer", content="white?", update_id=100)])

    history = await db.get_recent_messages(a, conversation.id, limit=20)
    assert [(m.role, m.content) for m in history] == [("customer", "white?"), ("assistant", "Yes, in 42.")]
    assert [m.content for m in await db.get_recent_messages(a, conversation.id, limit=1)] == ["Yes, in 42."]
    assert await db.get_recent_messages(a, conversation.id, limit=20,
                                        since=utc_now() + timedelta(minutes=1)) == []

    # Store B can't read it, and can't write into store A's conversation.
    assert await db.get_recent_messages(b, conversation.id, limit=20) == []
    with pytest.raises(DatabaseError):
        await db.add_messages(b, conversation.id, [ChatMessage(role="staff", content="x")])
