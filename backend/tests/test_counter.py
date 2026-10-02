"""Phase 12: counter sales and price negotiation in the Mini App (fake database).

The endpoint, the role rule handed to the database (owner: no limit; staff:
the store's limit), the error messages, the hold warning, and the staff
group notes. The database's own rules (prices, stock, holds, all or
nothing) are checked in test_counter_db.py against a real Supabase.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agents.onboarding import Onboarding
from app.agents.orchestrator import Orchestrator
from app.api.v1.catalog import get_catalog
from app.api.v1.miniapp import get_onboarding
from app.api.v1.webhook import get_db, get_orchestrator
from app.core.config import get_settings
from app.main import app
from app.models.schemas import VariantMatch
from app.services.conversation_service import InMemoryConversationStore
from app.services.supabase_service import NotFoundError, OrderRejectedError, OutOfStockError
from app.services.telegram_service import TELEGRAM_API, TelegramService
from tests.test_conversation import EchoFlow
from tests.test_miniapp import (
    ADMIN,
    GROUP_A,
    MEMBER,
    OWNER,
    PUBLIC_URL,
    STORE_A,
    STORE_B,
    STRANGER,
    FakeCatalog,
    FakeDb,
    FakeTelegram,
    headers,
    url,
)


def _match(variant: dict, product: dict) -> VariantMatch:
    price = variant["price_override"] if variant["price_override"] is not None else product["base_price"]
    return VariantMatch(variant_id=variant["id"], product_id=product["id"], product_name=product["name"],
                        brand=product.get("brand"), category=product.get("category"),
                        color=variant["color"], size=variant["size"], stock_quantity=variant["stock_quantity"],
                        price=Decimal(str(price)) if price is not None else None)


class CounterDb(FakeDb):
    """FakeDb plus counter sales, with the database's rules in short."""

    def __init__(self):
        super().__init__()
        self.sales = []
        self.holds: dict[UUID, list[UUID]] = {}  # variant -> online orders holding it
        self.saved_keys: dict[str, dict] = {}

    async def get_variants(self, store_id, variant_ids):
        found = []
        for product in self._of(store_id):
            for variant in product["product_variants"]:
                if UUID(variant["id"]) in variant_ids:
                    match = _match(variant, product)
                    match.held = len(self.holds.get(match.variant_id, []))
                    found.append(match)
        return found

    async def holds_on_variant(self, store_id, variant_id):
        until = (datetime.now(timezone.utc) + timedelta(minutes=3)).isoformat()
        return [{"order_id": str(o), "quantity": 1, "reserved_until": until} for o in self.holds.get(variant_id, [])]

    async def get_order(self, store_id, order_id):
        return None

    async def record_counter_sale(self, store_id, items, **sale):
        if sale["idempotency_key"] in self.saved_keys:
            return {**self.saved_keys[sale["idempotency_key"]], "already_saved": True}
        variants = {v.variant_id: v for v in await self.get_variants(store_id, [i["variant_id"] for i in items])}
        total = list_total = Decimal(0)
        held = []
        for item in items:
            variant = variants.get(item["variant_id"])
            if variant is None:  # another store's, or gone
                raise NotFoundError("variant_not_found")
            price = Decimal(str(item["price"]))
            if price > variant.price:
                raise OrderRejectedError("price_above_list")
            limit = sale["max_discount_percent"]
            if limit is not None and price < variant.price * (100 - limit) / 100:
                raise OrderRejectedError("discount_too_large")
            if variant.stock_quantity < item["quantity"]:
                raise OutOfStockError("insufficient_stock", str(variant.variant_id))
            if variant.held and variant.stock_quantity - variant.held < item["quantity"]:
                if not sale["allow_held"]:
                    raise OrderRejectedError("held_by_online_order", str(variant.variant_id))
                held += [str(o) for o in self.holds[variant.variant_id]]
            total += price * item["quantity"]
            list_total += variant.price * item["quantity"]
        self.sales.append({"store_id": store_id, "items": items, **sale})
        result = {"order_id": str(uuid4()), "total": total, "list_total": list_total,
                  "already_saved": False, "held_orders": held}
        self.saved_keys[sale["idempotency_key"]] = result
        return result


@pytest.fixture
def world(monkeypatch):
    db, telegram, catalog = CounterDb(), FakeTelegram(), FakeCatalog()
    service = TelegramService(httpx.AsyncClient(base_url=TELEGRAM_API,
                                                transport=httpx.MockTransport(telegram.handler)))
    orchestrator = Orchestrator(db, InMemoryConversationStore(), service, None, burst_wait=0, flow=EchoFlow())
    monkeypatch.setattr(get_settings(), "public_base_url", PUBLIC_URL)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_orchestrator] = lambda: orchestrator
    app.dependency_overrides[get_catalog] = lambda: catalog
    app.dependency_overrides[get_onboarding] = lambda: Onboarding(db, service, PUBLIC_URL)
    yield db, telegram, TestClient(app)
    app.dependency_overrides.clear()


def blue_m(db):
    jacket = next(p for p in db.products.values() if p["name"] == "Denim Jacket")
    return next(v for v in jacket["product_variants"] if v["size"] == "M")  # 5 in stock, listed 3500


def sell(client, who, variant_id, price, quantity=1, **extra):
    body = {"items": [{"variant_id": variant_id, "quantity": quantity, "price": price}],
            "payment_method": "Cash", "request_id": str(uuid4()), **extra}
    return client.post(url("/counter-sales"), json=body, headers=headers(who))


def test_owner_sells_at_any_price_up_to_the_listed_one(world):
    db, telegram, client = world
    variant = blue_m(db)["id"]
    response = sell(client, OWNER, variant, 2000, payment_method="Telebirr", customer_name="Abebe")
    assert response.status_code == 201
    body = response.json()
    assert body["total"] == "2000" and body["list_total"] == "3500" and body["discount"] == "1500"
    assert db.sales[-1]["max_discount_percent"] is None  # the owner: no limit
    assert db.sales[-1]["sold_by_telegram_id"] == OWNER and db.sales[-1]["payment_method"] == "Telebirr"
    # The staff group sees it, with the listed price and the discount.
    note = telegram.sent(GROUP_A)[-1]["text"]
    assert note.startswith("🏪 User1 sold in the shop") and "listed 3,500 ETB, −43%" in note
    assert "Total: 2,000 ETB, Telebirr" in note and "Customer: Abebe" in note
    assert sell(client, OWNER, variant, 3600).status_code == 422  # never above the listed price


def test_staff_stay_within_the_limit(world):
    db, _, client = world
    variant = blue_m(db)["id"]
    assert sell(client, MEMBER, variant, 3150).status_code == 201  # exactly 10% off
    assert db.sales[-1]["max_discount_percent"] == Decimal("10")  # the store's limit for staff
    refused = sell(client, MEMBER, variant, 3000)  # 14% off
    assert refused.status_code == 403 and "at most 10% off" in refused.json()["detail"]
    assert refused.headers["X-Error-Code"] == "discount_too_large"
    # The owner raises the limit in Settings; now it's allowed.
    client.put(url("/settings"), json={"staff_discount_percent": 20}, headers=headers(ADMIN))
    assert db.stores[STORE_A.id].staff_discount_percent == Decimal("20")
    assert sell(client, MEMBER, variant, 3000).status_code == 201


def test_held_by_an_online_order_warns_then_sells_and_tells_staff(world):
    db, telegram, client = world
    jacket = next(p for p in db.products.values() if p["name"] == "Denim Jacket")
    last_xl = next(v for v in jacket["product_variants"] if v["size"] == "XL")["id"]  # 1 in stock
    online = uuid4()
    db.holds[UUID(last_xl)] = [online]
    info = client.get(url(f"/variants/{last_xl}/availability"), headers=headers(MEMBER)).json()
    assert info["stock"] == 1 and info["available"] == 0 and info["holds"][0]["minutes_left"] in (2, 3)
    first = sell(client, MEMBER, last_xl, 3800)
    assert first.status_code == 409 and first.headers["X-Error-Code"] == "held_by_online_order"
    sold = sell(client, MEMBER, last_xl, 3800, allow_held=True)
    assert sold.status_code == 201 and len(sold.json()["held_orders"]) == 1
    assert telegram.sent(GROUP_A)[-1]["text"].startswith("📞 Online order #")


def test_not_enough_stock_and_bad_requests(world):
    db, _, client = world
    variant = blue_m(db)["id"]
    short = sell(client, MEMBER, variant, 3500, quantity=9)
    assert short.status_code == 409 and "not enough in stock" in short.json()["detail"]
    assert client.post(url("/counter-sales"), json={"items": [], "payment_method": "Cash",
                                                    "request_id": str(uuid4())},
                       headers=headers(MEMBER)).status_code == 422
    assert sell(client, MEMBER, variant, -1).status_code == 422
    assert sell(client, MEMBER, variant, 3500, payment_method=" ").status_code == 422


def test_the_same_sale_twice_is_saved_once(world):
    db, telegram, client = world
    variant = blue_m(db)["id"]
    body = {"items": [{"variant_id": variant, "quantity": 1, "price": 3500}], "payment_method": "Cash",
            "request_id": str(uuid4())}
    first = client.post(url("/counter-sales"), json=body, headers=headers(MEMBER)).json()
    notes = len(telegram.sent(GROUP_A))
    again = client.post(url("/counter-sales"), json=body, headers=headers(MEMBER)).json()
    assert again["already_saved"] and again["order_id"] == first["order_id"]
    assert len(db.sales) == 1 and len(telegram.sent(GROUP_A)) == notes  # no second note


def test_only_this_stores_team(world):
    db, _, client = world
    variant = blue_m(db)["id"]
    assert sell(client, STRANGER, variant, 3500).status_code == 403
    b_variant = next(p for p in db.products.values() if p["store_id"] == str(STORE_B.id))["product_variants"][0]
    assert sell(client, OWNER, b_variant["id"], 100).status_code == 404  # another store's item
    assert client.get(url(f"/variants/{b_variant['id']}/availability"), headers=headers(OWNER)).status_code == 404


def test_me_has_the_staff_limit_and_payment_methods(world):
    db, _, client = world
    db.stores[STORE_A.id] = STORE_A.model_copy(update={"payment_accounts": [
        {"name": "Telebirr", "number": "0911"}, {"name": "CBE", "number": "1000"}, {"name": "cash", "number": "-"}]})
    store = client.get(url("/me"), headers=headers(MEMBER)).json()["store"]
    assert store["payment_methods"] == ["Cash", "Telebirr", "CBE"] and store["staff_discount_percent"] == "10"
    assert client.put(url("/settings"), json={"staff_discount_percent": 120},
                      headers=headers(ADMIN)).status_code == 422
    assert client.put(url("/settings"), json={"staff_discount_percent": 5},
                      headers=headers(MEMBER)).status_code == 403
