"""Tests for supabase_service against a real Supabase project.

Skipped unless SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are set in
backend/.env. Use a test project, not a live store's project.

Each run creates temporary stores named "__pytest_..." with products and a
customer, and deletes them at the end (deleting a store deletes everything
that belongs to it).
"""
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.core.config import get_settings
from app.models.schemas import DraftItem, OrderDraft
from app.services.supabase_service import (
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

TEST_PREFIX = "__pytest_"


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


async def _insert(service: SupabaseService, table: str, row: dict) -> dict:
    # Tests may write directly to set up data; app code goes through the service.
    return (await service._db.table(table).insert(row).execute()).data[0]


async def _delete_test_stores(service: SupabaseService) -> None:
    await service._db.table("stores").delete().like("name", f"{TEST_PREFIX}%").execute()


@pytest.fixture(scope="module")
async def world(anyio_backend):
    """Two stores with products, and a customer in store A."""
    service = await SupabaseService.connect(
        _settings.supabase_url, _settings.supabase_service_role_key.get_secret_value()
    )
    await _delete_test_stores(service)  # leftovers from an interrupted run

    store_a = await _insert(service, "stores", {"name": f"{TEST_PREFIX}selam_shoes"})
    store_b = await _insert(service, "stores", {"name": f"{TEST_PREFIX}other_store"})
    store_off = await _insert(service, "stores", {"name": f"{TEST_PREFIX}inactive", "is_active": False})

    af1 = await _insert(service, "products", {
        "store_id": store_a["id"], "name": "Air Force 1", "brand": "Nike",
        "category": "sneakers", "base_price": 4500,
    })
    variants = {}
    for color, size, stock, override in [
        ("White", "40", 3, None), ("White", "41", 1, None), ("White", "42", 2, 5000),
        ("White", "43", 0, None), ("Black", "42", 5, None),
    ]:
        variants[(color, size)] = await _insert(service, "product_variants", {
            "product_id": af1["id"], "color": color, "size": size,
            "stock_quantity": stock, "price_override": override, "cost_price": 2000,
        })

    # Store B also sells an "Air Force 1" — store A must never see it.
    other_af1 = await _insert(service, "products", {
        "store_id": store_b["id"], "name": "Air Force 1", "base_price": 100,
    })
    other_variant = await _insert(service, "product_variants", {
        "product_id": other_af1["id"], "color": "White", "size": "42", "stock_quantity": 10,
    })

    customer = await service.get_or_create_customer(UUID(store_a["id"]), 900000001, "Abebe")

    yield {
        "service": service,
        "store_a": UUID(store_a["id"]),
        "store_b": UUID(store_b["id"]),
        "store_off": UUID(store_off["id"]),
        "variants": {key: UUID(v["id"]) for key, v in variants.items()},
        "other_variant": UUID(other_variant["id"]),
        "customer": customer,
    }

    await _delete_test_stores(service)
    await service.close()


def _draft(*items: tuple[UUID, int], delivery: bool = True) -> OrderDraft:
    return OrderDraft(
        items=[DraftItem(variant_id=v, quantity=q, description="test item") for v, q in items],
        contact_name="Abebe Kebede",
        contact_phone="0911 22 33 44",
        fulfillment_method="delivery" if delivery else "pickup",
        delivery_address="Bole, Addis Ababa" if delivery else None,
        customer_confirmed=True,
    )


async def _stock(world, variant_id: UUID) -> int:
    rows = (await world["service"]._db.table("product_variants")
            .select("stock_quantity").eq("id", str(variant_id)).execute()).data
    return rows[0]["stock_quantity"]


# --- Stores -----------------------------------------------------------------

async def test_get_store(world):
    service = world["service"]
    store = await service.get_store(world["store_a"])
    assert store.name == f"{TEST_PREFIX}selam_shoes"
    assert store.is_active
    assert await service.get_store(uuid4()) is None
    assert await service.get_store(world["store_off"]) is None  # switched off


# --- Search -----------------------------------------------------------------

async def test_search_finds_only_this_stores_products(world):
    results = await world["service"].search_variants(world["store_a"], "air force")
    assert len(results) == 5
    assert all(r.product_name == "Air Force 1" for r in results)
    assert world["other_variant"] not in {r.variant_id for r in results}
    # Most stock first
    assert [r.stock_quantity for r in results] == sorted((r.stock_quantity for r in results), reverse=True)


async def test_search_by_color_and_size_uses_database_price(world):
    results = await world["service"].search_variants(world["store_a"], "AF1 nike", color="white", size="42")
    # "AF1" isn't in the name, so nothing matches every word
    assert results == []

    results = await world["service"].search_variants(world["store_a"], "nike", color="white", size="42")
    assert len(results) == 1
    match = results[0]
    assert match.price == Decimal("5000")  # price_override beats base_price
    assert match.in_stock
    assert not hasattr(match, "cost_price")


async def test_catalog_for_the_order_flow(world):
    # Phase 8c: category buttons, product buttons, and a product's sizes/colors.
    service = world["service"]
    assert await service.list_categories(world["store_a"]) == ["sneakers"]
    products = await service.list_products_in_stock(world["store_a"], "sneakers")
    assert [p.name for p in products] == ["Air Force 1"]
    assert await service.list_products_in_stock(world["store_a"], "clothing") == []

    variants = await service.get_product_variants(world["store_a"], products[0].id)
    assert len(variants) == 5  # all of them, sold-out ones included (the flow filters)
    sold_out = next(v for v in variants if (v.color, v.size) == ("White", "43"))
    assert sold_out.available == 0
    assert all(v.price is not None for v in variants)

    # Store B has its own "Air Force 1": it never shows up for store A, and
    # store A's product can't be read through store B.
    assert [p.name for p in await service.list_products_in_stock(world["store_b"])] == ["Air Force 1"]
    assert await service.get_product_variants(world["store_b"], products[0].id) == []


async def test_search_shows_sold_out_and_base_price(world):
    results = await world["service"].search_variants(world["store_a"], "sneakers", color="white", size="43")
    assert len(results) == 1
    assert results[0].stock_quantity == 0 and not results[0].in_stock
    assert results[0].price == Decimal("4500")


async def test_list_products_is_per_store(world):
    names = [p.name for p in await world["service"].list_products(world["store_a"])]
    assert "Air Force 1" in names
    other = await world["service"].list_products(world["store_b"])
    assert all(p.store_id == world["store_b"] for p in other)


@pytest.mark.parametrize("query", ["ጫማ", "air,force)", "'; drop table stores; --", "*", ""])
async def test_search_handles_any_text_safely(world, query):
    results = await world["service"].search_variants(world["store_a"], query)
    assert all(r.product_name == "Air Force 1" for r in results)


# --- Customers --------------------------------------------------------------

async def test_get_or_create_customer_is_per_store(world):
    service = world["service"]
    again = await service.get_or_create_customer(world["store_a"], 900000001)
    assert again.id == world["customer"].id
    in_b = await service.get_or_create_customer(world["store_b"], 900000001)
    assert in_b.id != world["customer"].id
    assert in_b.store_id == world["store_b"]


async def test_update_customer(world):
    service = world["service"]
    updated = await service.update_customer(
        world["store_a"], world["customer"].id, phone="+251 91-122 3344", address="Bole"
    )
    assert updated.phone == "+251911223344"
    assert updated.address == "Bole"
    assert updated.name == "Abebe"  # untouched
    with pytest.raises(NotFoundError):
        await service.update_customer(world["store_b"], world["customer"].id, name="x")


# --- Orders -----------------------------------------------------------------

async def test_create_order_uses_database_prices_and_is_idempotent(world):
    service, v = world["service"], world["variants"]
    before = await _stock(world, v[("White", "42")])
    draft = _draft((v[("White", "42")], 2), (v[("Black", "42")], 1))
    key = f"test-{uuid4()}"

    order_id = await service.create_order(world["store_a"], world["customer"].id, draft, key)
    again = await service.create_order(world["store_a"], world["customer"].id, draft, key)
    assert again == order_id

    orders = await service.get_customer_orders(world["store_a"], world["customer"].id)
    order = next(o for o in orders if o.id == order_id)
    assert order.total_price == Decimal("14500")  # 2 x 5000 + 1 x 4500
    assert order.status == "pending" and order.payment_status == "unpaid"
    assert order.currency == "ETB"
    assert order.contact_phone == "0911223344"
    assert {(i.color, i.size, i.quantity) for i in order.items} == {("White", "42", 2), ("Black", "42", 1)}
    assert all(i.product_name == "Air Force 1" for i in order.items)
    # Stock is not reduced when ordering (D3)
    assert await _stock(world, v[("White", "42")]) == before


async def test_create_order_refusals(world):
    service, v = world["service"], world["variants"]
    store, customer = world["store_a"], world["customer"].id

    with pytest.raises(OrderRejectedError) as incomplete:
        await service.create_order(store, customer, OrderDraft(), "k1")
    assert "items" in incomplete.value.detail

    with pytest.raises(OutOfStockError):
        await service.create_order(store, customer, _draft((v[("White", "43")], 1)), f"k-{uuid4()}")

    with pytest.raises(NotFoundError):  # another store's variant
        await service.create_order(store, customer, _draft((world["other_variant"], 1)), f"k-{uuid4()}")

    with pytest.raises(NotFoundError):  # customer belongs to store A
        await service.create_order(world["store_b"], customer, _draft((world["other_variant"], 1)), f"k-{uuid4()}")


async def test_customer_orders_are_private(world):
    assert await world["service"].get_customer_orders(world["store_b"], world["customer"].id) == []


# --- Stock and payments -----------------------------------------------------

async def test_update_stock(world):
    service, variant = world["service"], world["variants"][("White", "40")]
    start = await _stock(world, variant)
    assert await service.update_stock(world["store_a"], variant, 2) == start + 2
    assert await service.update_stock(world["store_a"], variant, -2) == start
    with pytest.raises(OutOfStockError):
        await service.update_stock(world["store_a"], variant, -(start + 1))
    with pytest.raises(NotFoundError):
        await service.update_stock(world["store_a"], world["other_variant"], 1)


async def test_record_payment(world):
    service, v = world["service"], world["variants"]
    store, customer = world["store_a"], world["customer"].id
    last_one = v[("White", "41")]  # only 1 in stock

    first = await service.create_order(store, customer, _draft((last_one, 1), delivery=False), f"k-{uuid4()}")
    # The first order holds the last pair (D19): a second order is refused.
    with pytest.raises(OutOfStockError):
        await service.create_order(store, customer, _draft((last_one, 1)), f"k-{uuid4()}")

    with pytest.raises(NotFoundError):  # another store can't confirm it
        await service.record_payment(world["store_b"], first, Decimal("4500"), "Telebirr", None)

    await service.record_payment(store, first, Decimal("4500.00"), "Telebirr", None)
    assert await _stock(world, last_one) == 0

    with pytest.raises(OrderRejectedError) as paid_twice:
        await service.record_payment(store, first, Decimal("4500"), "Telebirr", None)
    assert paid_twice.value.code == "already_paid"

    orders = {o.id: o for o in await service.get_customer_orders(store, customer, limit=20)}
    assert orders[first].payment_status == "paid" and orders[first].status == "confirmed"
    assert orders[first].reserved_until is None  # paid: the stock itself went down


# --- Holding stock (D19) ----------------------------------------------------

async def _new_variant(world, stock: int) -> UUID:
    product = await _insert(world["service"], "products", {
        "store_id": str(world["store_a"]), "name": f"Hold test {uuid4()}", "base_price": 1000,
    })
    variant = await _insert(world["service"], "product_variants", {
        "product_id": product["id"], "color": "Red", "size": "M", "stock_quantity": stock,
    })
    return UUID(variant["id"])


async def test_an_order_holds_its_items(world):
    service, store, customer = world["service"], world["store_a"], world["customer"].id
    variant = await _new_variant(world, stock=2)

    await service.create_order(store, customer, _draft((variant, 2)), f"k-{uuid4()}", hold_minutes=5)
    [match] = await service.get_variants(store, [variant])
    assert (match.stock_quantity, match.held, match.in_stock) == (2, 2, False)
    assert await _stock(world, variant) == 2  # held, not taken (D3)

    with pytest.raises(OutOfStockError):
        await service.create_order(store, customer, _draft((variant, 1)), f"k-{uuid4()}")
    # Another store never sees this store's variants or holds.
    assert await service.get_variants(world["store_b"], [variant]) == []


async def test_an_ended_hold_frees_the_items(world):
    service, store, customer = world["service"], world["store_a"], world["customer"].id
    variant = await _new_variant(world, stock=1)

    old = await service.create_order(store, customer, _draft((variant, 1)), f"k-{uuid4()}", hold_minutes=0)
    new = await service.create_order(store, customer, _draft((variant, 1)), f"k-{uuid4()}", hold_minutes=5)

    # The new order holds the last one, so the old order can't be paid for.
    with pytest.raises(OutOfStockError):
        await service.record_payment(store, old, Decimal("1000"), "Telebirr", None)
    await service.record_payment(store, new, Decimal("1000"), "Telebirr", None)
    assert await _stock(world, variant) == 0


async def test_same_variant_on_two_lines_is_added_up(world):
    service, store, customer = world["service"], world["store_a"], world["customer"].id
    variant = await _new_variant(world, stock=3)
    with pytest.raises(OutOfStockError):  # 2 + 2 > 3
        await service.create_order(store, customer, _draft((variant, 2), (variant, 2)), f"k-{uuid4()}")
    order_id = await service.create_order(store, customer, _draft((variant, 2), (variant, 1)), f"k-{uuid4()}")
    order = next(o for o in await service.get_customer_orders(store, customer, limit=50) if o.id == order_id)
    assert [(i.quantity, i.price) for i in order.items] == [(3, Decimal("1000"))]
