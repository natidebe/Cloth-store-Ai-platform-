"""Phase 15b against a real Supabase project (migration 016): a delivery
order paid on arrival takes its stock at On the way, records the payment at
Delivered & paid, puts the stock back at Not delivered; a payment confirmed
while it's on the way doesn't take the stock twice; other stores can't touch
it. Skipped unless SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are set in
backend/.env AND 016_delivery_flow.sql has been run. Use a test project.
Creates temporary stores named "__pytest_delivery_..." and deletes them after.
"""
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.core.config import get_settings
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

PREFIX = "__pytest_delivery_"


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


async def _cleanup(db: SupabaseService) -> None:
    await db._db.table("stores").delete().like("name", f"{PREFIX}%").execute()


@pytest.fixture(scope="module")
async def db(anyio_backend):
    service = await SupabaseService.connect(_settings.supabase_url,
                                            _settings.supabase_service_role_key.get_secret_value())
    try:
        await service.dispatch_order(uuid4(), uuid4(), None, None)
    except NotFoundError:
        pass  # the function exists
    except DatabaseUnavailableError:
        await service.close()
        raise
    except DatabaseError:
        await service.close()
        pytest.skip("migration 016_delivery_flow.sql has not been run yet")
    await _cleanup(service)
    yield service
    await _cleanup(service)
    await service.close()


async def _shop(db: SupabaseService, name: str, stock: int = 3) -> tuple[UUID, UUID]:
    """A store with one product and one variant; returns (store, variant)."""
    store = (await db._db.table("stores").insert({"name": f"{PREFIX}{name}", "status": "active"})
             .execute()).data[0]["id"]
    product = (await db._db.table("products").insert({"store_id": store, "name": "Air Force 1",
                                                      "base_price": 5000}).execute()).data[0]["id"]
    variant = (await db._db.table("product_variants").insert({
        "store_id": store, "product_id": product, "color": "White", "size": "42", "stock_quantity": stock,
    }).execute()).data[0]["id"]
    return UUID(store), UUID(variant)


async def _order(db: SupabaseService, store: UUID, variant: UUID, quantity: int = 1) -> UUID:
    order = (await db._db.table("orders").insert({
        "store_id": str(store), "status": "pending", "payment_status": "unpaid",
        "fulfillment_method": "delivery", "delivery_address": "to be arranged", "total_price": 5000 * quantity,
    }).execute()).data[0]["id"]
    await db._db.table("order_items").insert({"order_id": order, "variant_id": str(variant),
                                              "quantity": quantity, "price": 5000}).execute()
    return UUID(order)


async def _stock(db: SupabaseService, variant: UUID) -> int:
    return (await db._db.table("product_variants").select("stock_quantity").eq("id", str(variant))
            .execute()).data[0]["stock_quantity"]


async def _state(db: SupabaseService, order: UUID) -> tuple[str, str]:
    row = (await db._db.table("orders").select("status, payment_status").eq("id", str(order))
           .execute()).data[0]
    return row["status"], row["payment_status"]


async def test_on_the_way_then_delivered_and_paid(db):
    store, variant = await _shop(db, "deliver")
    order = await _order(db, store, variant, quantity=2)
    with pytest.raises(OrderRejectedError, match="not_on_the_way"):
        await db.deliver_order(store, order, Decimal("10000"), None, 7, "Sara")  # On the way first

    await db.dispatch_order(store, order, 7, "Sara")
    assert await _state(db, order) == ("out_for_delivery", "unpaid")
    assert await _stock(db, variant) == 1  # D77: taken when it leaves
    with pytest.raises(OrderRejectedError, match="already_dispatched"):
        await db.dispatch_order(store, order, 7, "Sara")
    assert await _stock(db, variant) == 1

    payment = await db.deliver_order(store, order, Decimal("10000"), None, 7, "Sara")
    assert payment is not None
    assert await _state(db, order) == ("delivered", "paid")
    paid = (await db._db.table("payments").select("amount, confirmed_by_name").eq("order_id", str(order))
            .execute()).data
    assert [(Decimal(str(p["amount"])), p["confirmed_by_name"]) for p in paid] == [(Decimal("10000"), "Sara")]
    assert await _stock(db, variant) == 1  # not taken again
    with pytest.raises(OrderRejectedError, match="already_delivered"):
        await db.deliver_order(store, order, Decimal("10000"), None, 7, "Sara")


async def test_not_delivered_puts_the_stock_back(db):
    store, variant = await _shop(db, "return")
    order = await _order(db, store, variant)
    await db.dispatch_order(store, order, 7, "Sara")
    assert await _stock(db, variant) == 2
    await db.return_order(store, order, 7, "Sara")
    assert await _state(db, order) == ("cancelled", "unpaid")
    assert await _stock(db, variant) == 3
    with pytest.raises(OrderRejectedError, match="order_cancelled"):
        await db.return_order(store, order, 7, "Sara")  # not twice
    assert await _stock(db, variant) == 3


async def test_a_payment_while_on_the_way_doesnt_take_the_stock_twice(db):
    store, variant = await _shop(db, "pay_on_the_way")
    order = await _order(db, store, variant)
    await db.dispatch_order(store, order, 7, "Sara")
    await db.record_payment(store, order, Decimal("5000"), "Telebirr", None)  # the old Confirm payment
    assert await _state(db, order) == ("out_for_delivery", "paid")
    assert await _stock(db, variant) == 2
    with pytest.raises(OrderRejectedError, match="already_paid"):
        await db.return_order(store, order, 7, "Sara")  # money taken: refund by hand first
    assert await db.deliver_order(store, order, None, None, 7, "Sara") is None  # no second payment
    assert await _state(db, order) == ("delivered", "paid")


async def test_sold_out_and_other_stores_are_refused(db):
    store, variant = await _shop(db, "sold_out", stock=0)
    order = await _order(db, store, variant)
    with pytest.raises(OutOfStockError):
        await db.dispatch_order(store, order, 7, "Sara")
    assert await _state(db, order) == ("pending", "unpaid")

    other, _ = await _shop(db, "other")
    mine, my_variant = await _shop(db, "mine")
    order = await _order(db, mine, my_variant)
    with pytest.raises(NotFoundError):
        await db.dispatch_order(other, order, 7, "Sara")
    assert await _stock(db, my_variant) == 3
