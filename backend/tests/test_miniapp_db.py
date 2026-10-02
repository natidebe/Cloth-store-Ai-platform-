"""Phase 10b against a real Supabase project (migration 010): sign-up from the
platform bot, platform admins by Telegram id, the analytics numbers, and the
inventory rules on the real tables.

Skipped unless SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are set in
backend/.env AND 010_mini_app.sql has been run. Use a test project.
Creates temporary stores named "__pytest_app_..." and deletes them after.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.agents.analytics import store_analytics
from app.agents.inventory import GridRow, Inventory, InventoryError
from app.core.config import get_settings
from app.models.schemas import DraftItem, OrderDraft
from app.services.supabase_service import (
    DatabaseError,
    DatabaseUnavailableError,
    DuplicateError,
    SupabaseService,
)

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

PREFIX = "__pytest_app_"
TG_OWNER = 990000000123
BOT_ID = 990000000777


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


async def _cleanup(db: SupabaseService) -> None:
    await db._db.table("stores").delete().like("name", f"{PREFIX}%").execute()


@pytest.fixture(scope="module")
async def world(anyio_backend):
    db = await SupabaseService.connect(_settings.supabase_url,
                                       _settings.supabase_service_role_key.get_secret_value())
    try:
        await db._run(db._db.table("platform_admin_telegram").select("telegram_id").limit(1))
    except DatabaseUnavailableError:
        await db.close()
        raise
    except DatabaseError:
        await db.close()
        pytest.skip("migration 010_mini_app.sql has not been run yet")
    await _cleanup(db)
    a = (await db._db.table("stores").insert({"name": f"{PREFIX}a", "status": "active"}).execute()).data[0]
    b = (await db._db.table("stores").insert({"name": f"{PREFIX}b", "status": "active"}).execute()).data[0]
    yield {"db": db, "a": UUID(a["id"]), "b": UUID(b["id"])}
    await _cleanup(db)
    await db.close()


async def test_sign_up_from_the_platform_bot(world):
    db = world["db"]
    store_id = await db.create_store_for_telegram(f"{PREFIX}signup", f"{BOT_ID}:{uuid4().hex}xyz",
                                                  BOT_ID, "pytest_signup_bot", "secret", TG_OWNER)
    store = await db.get_store_any_status(store_id)
    assert store.status == "pending" and store.plan == "free" and store.owner_telegram_id == TG_OWNER
    assert [s.id for s in await db.stores_created_by(TG_OWNER)] == [store_id]
    with pytest.raises(DuplicateError):  # one store per bot
        await db.create_store_for_telegram(f"{PREFIX}copy", f"{BOT_ID}:{uuid4().hex}abc",
                                           BOT_ID, "pytest_signup_bot", "s", TG_OWNER + 1)
    assert not await db.is_platform_admin_telegram(TG_OWNER)


async def test_inventory_on_the_real_tables(world):
    db, a, b = world["db"], world["a"], world["b"]
    inventory = Inventory(db)
    # Decimal, as the Mini App endpoint sends it (a bug once: Decimal isn't JSON).
    product_id = await db.create_product(a, {"name": "Polo", "category": "clothing", "base_price": Decimal("1800")})
    await db.update_product(a, product_id, {"base_price": Decimal("1850.50")})
    await inventory.save_grid(a, product_id, [GridRow("White", "M", 5), GridRow("Navy", "XL", 2, Decimal("2000"))])
    product = await inventory.product(a, product_id)
    assert product["code"] and len(product["product_variants"]) == 2
    white = next(v for v in product["product_variants"] if v["color"] == "White")

    # Stock changes, never below 0; the other store can't touch it.
    assert await inventory.change_stock(a, UUID(white["id"]), change=3) == 8
    assert await inventory.change_stock(a, UUID(white["id"]), set_to=4) == 4
    with pytest.raises(InventoryError):
        await inventory.change_stock(a, UUID(white["id"]), change=-5)
    with pytest.raises(InventoryError):
        await inventory.change_stock(b, UUID(white["id"]), set_to=0)
    with pytest.raises(InventoryError):
        await inventory.product(b, product_id)

    # Low stock: Navy XL has 2.
    assert [(r["color"], r["stock"]) for r in await db.low_stock(a, 2)] == [("Navy", 2)]

    # An ordered variant can't be deleted: it's taken off sale instead.
    customer = await db.get_or_create_customer(a, 990000000456, "Abebe")
    draft = OrderDraft(items=[DraftItem(variant_id=white["id"], quantity=1, description="Polo")],
                       contact_name="Abebe", contact_phone="0911223344", fulfillment_method="pickup")
    await db.create_order(a, customer.id, draft, f"k-{uuid4()}")
    result = await inventory.save_grid(a, product_id, [], remove=[UUID(white["id"])])
    assert result.kept_off_sale == ["White M"]
    with pytest.raises(InventoryError) as refused:
        await inventory.delete(a, product_id)
    assert refused.value.status_code == 409
    assert await inventory.take_off_sale(a, product_id) == 2

    # A never-ordered product can be deleted.
    spare = await db.create_product(a, {"name": "Spare"})
    await inventory.delete(a, spare)
    assert await db.get_product_with_variants(a, spare) is None


async def test_analytics_numbers(world):
    db, b = world["db"], world["b"]
    product_id = await db.create_product(b, {"name": "Shoe", "base_price": "1000"})
    variant_id = await db.add_variant(b, product_id, "Black", "42", 10, None)
    customer = await db.get_or_create_customer(b, 990000000789, "Kebede")

    def draft(quantity):
        return OrderDraft(items=[DraftItem(variant_id=variant_id, quantity=quantity, description="Shoe")],
                          contact_name="Kebede", contact_phone="0911223344", fulfillment_method="pickup")

    paid = await db.create_order(b, customer.id, draft(2), f"k-{uuid4()}")
    await db.create_order(b, customer.id, draft(1), f"k-{uuid4()}")  # placed, never paid
    await db.record_payment(b, paid, Decimal("2000"), "Telebirr", None)

    numbers = await store_analytics(db, b, "today", ai_daily_limit=300)
    assert numbers["revenue"] == Decimal("2000") and numbers["payments"] == 1
    assert numbers["orders_placed"] == 2 and numbers["orders_paid"] == 1 and numbers["paid_rate"] == 0.5
    assert numbers["unpaid_orders"] == 1 and numbers["pickup_orders"] == 2 and numbers["new_customers"] == 1
    assert numbers["average_order"] == Decimal("2000.00")
    assert numbers["top_products"][0]["name"] == "Shoe" and numbers["top_products"][0]["quantity"] == 2
    [today] = numbers["per_day"]
    assert today["placed"] == 2 and today["paid"] == 1 and today["revenue"] == Decimal("2000")

    # Another store's numbers never include these.
    other = await store_analytics(db, world["a"], "today", ai_daily_limit=300)
    assert all(t["name"] != "Shoe" for t in other["top_products"])

    # A period with nothing in it.
    later = datetime.now(timezone.utc) + timedelta(days=40)
    empty = await store_analytics(db, b, "7d", ai_daily_limit=300, now=later)
    assert empty["revenue"] == 0 and empty["orders_placed"] == 0 and len(empty["per_day"]) == 7


async def test_settings_change_only_profile_fields(world):
    db, a = world["db"], world["a"]
    await db.update_store_profile(a, {"delivery_info": "Bole 150 ETB", "status": "suspended", "plan": "pro"})
    store = await db.get_store_any_status(a)
    assert store.delivery_info == "Bole 150 ETB" and store.status == "active" and store.plan != "pro"
