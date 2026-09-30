"""Phase 9 database functions against a real Supabase project.

Skipped unless SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are set in
backend/.env AND 006_staff_handover.sql has been run. Use a test project.
Creates temporary stores named "__pytest_staff_..." and deletes them after.
"""
from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.core.config import get_settings
from app.models.schemas import DraftItem, OrderDraft, StaffMessage
from app.services.conversation_service import utc_now
from app.services.supabase_service import DatabaseError, DatabaseUnavailableError, SupabaseService

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

PREFIX = "__pytest_staff_"
CUSTOMER = 900000301
STAFF_CHAT = -100123456


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


async def _insert(db: SupabaseService, table: str, row: dict) -> dict:
    return (await db._db.table(table).insert(row).execute()).data[0]


@pytest.fixture(scope="module")
async def world(anyio_backend):
    db = await SupabaseService.connect(_settings.supabase_url,
                                       _settings.supabase_service_role_key.get_secret_value())
    try:
        await db._run(db._db.table("staff_messages").select("id").limit(1))
    except DatabaseUnavailableError:
        await db.close()
        raise  # Supabase unreachable: a real failure, not "not migrated"
    except DatabaseError:
        await db.close()
        pytest.skip("migration 006_staff_handover.sql has not been run yet")

    await db._db.table("stores").delete().like("name", f"{PREFIX}%").execute()
    a = await _insert(db, "stores", {"name": f"{PREFIX}a", "status": "active", "staff_chat_id": STAFF_CHAT})
    b = await _insert(db, "stores", {"name": f"{PREFIX}b", "status": "active"})
    product = await _insert(db, "products", {"store_id": a["id"], "name": "Air Force 1", "base_price": 4500})
    variant = await _insert(db, "product_variants", {"product_id": product["id"], "color": "White",
                                                     "size": "42", "stock_quantity": 3})
    store_a, store_b = UUID(a["id"]), UUID(b["id"])
    customer = await db.get_or_create_customer(store_a, CUSTOMER, "Abebe")
    await db.get_or_create_customer(store_b, CUSTOMER, "Abebe")
    draft = OrderDraft(items=[DraftItem(variant_id=variant["id"], quantity=1, description="AF1")],
                       contact_name="Abebe", contact_phone="0911223344", fulfillment_method="pickup")
    order_id = await db.create_order(store_a, customer.id, draft, f"k-{uuid4()}")

    yield {"db": db, "a": store_a, "b": store_b, "customer": customer, "order": order_id,
           "variant": UUID(variant["id"])}

    await db._db.table("stores").delete().like("name", f"{PREFIX}%").execute()
    await db.close()


async def test_staff_messages_link_to_a_customer_per_store(world):
    db, a, b = world["db"], world["a"], world["b"]
    link = StaffMessage(store_id=a, staff_chat_id=STAFF_CHAT, message_id=555,
                        telegram_id=CUSTOMER, order_id=world["order"])
    await db.save_staff_message(link)
    await db.save_staff_message(link)  # saving twice is fine
    found = await db.find_staff_message(a, STAFF_CHAT, 555)
    assert (found.telegram_id, found.order_id) == (CUSTOMER, world["order"])
    assert await db.find_staff_message(a, STAFF_CHAT, 556) is None
    assert await db.find_staff_message(b, STAFF_CHAT, 555) is None  # other store


async def test_get_order_and_customer_are_per_store(world):
    db, a, b = world["db"], world["a"], world["b"]
    order = await db.get_order(a, world["order"])
    assert order.total_price == Decimal("4500") and order.items[0].product_name == "Air Force 1"
    assert await db.get_order(b, world["order"]) is None
    assert (await db.get_customer(a, world["customer"].id)).telegram_id == CUSTOMER
    assert await db.get_customer(b, world["customer"].id) is None


async def test_paused_chats_are_found_after_two_hours_idle(world):
    db, a = world["db"], world["a"]
    conversation = await db.get_or_create_conversation(a, CUSTOMER)
    conversation.bot_paused = True
    conversation.paused_at = utc_now() - timedelta(hours=3)
    conversation.staff_active_at = utc_now() - timedelta(minutes=10)  # staff replied recently
    conversation = await db.save_conversation(conversation)
    assert conversation.staff_active_at is not None

    cutoff = utc_now() - timedelta(hours=2)
    assert (a, CUSTOMER) not in await db.find_paused_before(cutoff)

    conversation.staff_active_at = None  # no staff reply since the handover 3 hours ago
    conversation = await db.save_conversation(conversation)
    assert (a, CUSTOMER) in await db.find_paused_before(cutoff)

    conversation.bot_paused, conversation.paused_at = False, None
    await db.save_conversation(conversation)
    assert (a, CUSTOMER) not in await db.find_paused_before(cutoff)


async def test_payment_confirmer_is_recorded_only_in_its_store(world):
    db, a, b = world["db"], world["a"], world["b"]
    payment_id = await db.record_payment(a, world["order"], Decimal("4500"), None, None)
    with pytest.raises(DatabaseError):
        await db.note_payment_confirmer(b, payment_id, 1, "Mallory")  # other store
    await db.note_payment_confirmer(a, payment_id, 777, "Sara")
    rows = (await db._db.table("payments").select("confirmed_by_telegram_id, confirmed_by_name")
            .eq("id", str(payment_id)).execute()).data
    assert rows == [{"confirmed_by_telegram_id": 777, "confirmed_by_name": "Sara"}]


async def test_verify_staff_refuses_a_bad_token(world):
    assert await world["db"].verify_staff(world["a"], "not-a-real-login-token") is None
