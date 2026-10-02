"""Phase 12 against a real Supabase project (migration 012): counter sales
saved all or nothing, the price rule, stock, online holds, and the numbers.

Skipped unless SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are set in
backend/.env AND 012_counter_sales.sql has been run. Use a test project.
Creates temporary stores named "__pytest_counter_..." and deletes them after.
"""
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.agents.analytics import store_analytics
from app.core.config import get_settings
from app.models.schemas import DraftItem, OrderDraft
from app.services.supabase_service import (
    DatabaseError,
    DatabaseUnavailableError,
    NotFoundError,
    OrderRejectedError,
    OutOfStockError,
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

PREFIX = "__pytest_counter_"
SELLER = 990000000321


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
        await db._run(db._db.table("orders").select("channel").limit(1))
    except DatabaseUnavailableError:
        await db.close()
        raise
    except DatabaseError:
        await db.close()
        pytest.skip("migration 012_counter_sales.sql has not been run yet")
    await _cleanup(db)
    stores = {}
    for name in ("a", "b"):
        row = (await db._db.table("stores").insert({"name": f"{PREFIX}{name}", "status": "active"})
               .execute()).data[0]
        stores[name] = UUID(row["id"])
    a = stores["a"]
    product = await db.create_product(a, {"name": "Airmax", "base_price": Decimal("10000")})
    black_42 = await db.add_variant(a, product, "Black", "42", 3, None)
    white_41 = await db.add_variant(a, product, "White", "41", 1, Decimal("9500"))
    yield {"db": db, "a": a, "b": stores["b"], "black_42": black_42, "white_41": white_41}
    await _cleanup(db)
    await db.close()


async def _sell(db, store, items, *, limit=None, allow_held=False, key=None, method="Cash"):
    return await db.record_counter_sale(
        store, items, payment_method=method, payment_note=None, sold_by_telegram_id=SELLER,
        sold_by_name="Abdi", contact_name=None, contact_phone=None, note=None,
        max_discount_percent=limit, allow_held=allow_held, idempotency_key=key or f"t-{uuid4()}")


async def _stock(db, variant_id):
    rows = (await db._db.table("product_variants").select("stock_quantity").eq("id", str(variant_id))
            .execute()).data
    return rows[0]["stock_quantity"]


async def test_a_counter_sale_with_a_negotiated_price(world):
    db, a, black = world["db"], world["a"], world["black_42"]
    key = f"t-{uuid4()}"
    sale = await _sell(db, a, [{"variant_id": black, "quantity": 1, "price": Decimal("9000")}],
                       limit=Decimal("10"), key=key, method="Telebirr")
    assert Decimal(str(sale["total"])) == 9000 and Decimal(str(sale["list_total"])) == 10000
    assert await _stock(db, black) == 2  # stock went down
    order = (await db._db.table("orders").select("*, order_items(price, list_price), payments(amount, method)")
             .eq("id", sale["order_id"]).execute()).data[0]
    assert order["channel"] == "in_shop" and order["status"] == "delivered" and order["payment_status"] == "paid"
    assert order["sold_by_name"] == "Abdi" and order["payment_method"] == "Telebirr"
    [line] = order["order_items"]
    assert Decimal(str(line["price"])) == 9000 and Decimal(str(line["list_price"])) == 10000
    assert Decimal(str(order["payments"][0]["amount"])) == 9000
    # The listed price itself never changed (D54).
    variant_row = (await db._db.table("product_variants").select("price_override, products(base_price)")
                   .eq("id", str(black)).execute()).data[0]
    assert variant_row["price_override"] is None and Decimal(str(variant_row["products"]["base_price"])) == 10000

    # The same sale again (Confirm pressed twice): saved once.
    again = await _sell(db, a, [{"variant_id": black, "quantity": 1, "price": Decimal("9000")}], key=key)
    assert again["already_saved"] and again["order_id"] == sale["order_id"]
    assert await _stock(db, black) == 2


async def test_the_price_rule(world):
    db, a, black = world["db"], world["a"], world["black_42"]
    with pytest.raises(OrderRejectedError) as staff_too_low:  # staff: 10% at most
        await _sell(db, a, [{"variant_id": black, "quantity": 1, "price": Decimal("8999")}], limit=Decimal("10"))
    assert staff_too_low.value.code == "discount_too_large"
    with pytest.raises(OrderRejectedError) as above:
        await _sell(db, a, [{"variant_id": black, "quantity": 1, "price": Decimal("10001")}])
    assert above.value.code == "price_above_list"
    # The owner (no limit) may go lower; the variant's own price is the listed one.
    owner = await _sell(db, a, [{"variant_id": world["white_41"], "quantity": 1, "price": Decimal("5000")}])
    assert Decimal(str(owner["list_total"])) == 9500
    assert await _stock(db, world["white_41"]) == 0


async def test_all_or_nothing_and_other_stores(world):
    db, a, black = world["db"], world["a"], world["black_42"]
    before = await _stock(db, black)
    with pytest.raises(OutOfStockError):  # the second line fails: nothing is saved
        await _sell(db, a, [{"variant_id": black, "quantity": 1, "price": Decimal("10000")},
                            {"variant_id": world["white_41"], "quantity": 5, "price": Decimal("9500")}])
    assert await _stock(db, black) == before
    with pytest.raises(NotFoundError):  # store b can't sell store a's item
        await _sell(db, world["b"], [{"variant_id": black, "quantity": 1, "price": Decimal("10000")}])
    with pytest.raises(OrderRejectedError) as twice:
        await _sell(db, a, [{"variant_id": black, "quantity": 1, "price": Decimal("10000")},
                            {"variant_id": black, "quantity": 1, "price": Decimal("10000")}])
    assert twice.value.code == "duplicate_item"


async def test_an_online_hold_warns_then_can_be_overridden(world):
    db, a, black = world["db"], world["a"], world["black_42"]
    stock = await _stock(db, black)
    customer = await db.get_or_create_customer(a, 990000000654, "Online")
    draft = OrderDraft(items=[DraftItem(variant_id=black, quantity=stock, description="Airmax")],
                       contact_name="Online", contact_phone="0911223344", fulfillment_method="pickup")
    online = await db.create_order(a, customer.id, draft, f"k-{uuid4()}")  # holds every pair
    holds = await db.holds_on_variant(a, black)
    assert [h["order_id"] for h in holds] == [str(online)]
    with pytest.raises(OrderRejectedError) as held:
        await _sell(db, a, [{"variant_id": black, "quantity": 1, "price": Decimal("10000")}])
    assert held.value.code == "held_by_online_order"
    sold = await _sell(db, a, [{"variant_id": black, "quantity": 1, "price": Decimal("10000")}], allow_held=True)
    assert sold["held_orders"] == [str(online)]


async def test_the_numbers_count_shop_sales_and_discounts(world):
    db, a = world["db"], world["a"]
    numbers = await store_analytics(db, a, "today", ai_daily_limit=300)
    assert numbers["in_shop_sales"] >= 3 and numbers["telegram_orders"] >= 1
    assert numbers["discount_total"] >= Decimal("5500")  # 1,000 (Abdi) + 4,500 (owner)
    [abdi] = [s for s in numbers["sellers"] if s["name"] == "Abdi"]
    assert abdi["sales"] >= 3 and abdi["discount"] >= Decimal("5500")
