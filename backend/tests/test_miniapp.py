"""Phase 10b: the Telegram Mini App backend, with a fake database and Telegram.

Logins (Telegram's signed initData), access from the staff group (D42),
/dashboard, analytics, products & stock (inventory), settings, and the
platform bot (sign-up and platform admin, D44–D45). The database's own
rules are checked in test_miniapp_db.py.
"""
import json
import time
from copy import deepcopy
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agents.miniapp import MiniAppAccess
from app.agents.onboarding import Onboarding
from app.agents.orchestrator import Orchestrator
from app.agents.tools import ADDRESS_TO_ARRANGE
from app.api.v1.catalog import get_catalog
from app.api.v1.miniapp import get_onboarding
from app.api.v1.platform_app import platform_webhook_secret
from app.api.v1.webhook import SECRET_HEADER, get_db, get_orchestrator
from app.core.config import get_settings
from app.core.telegram_auth import INIT_DATA_HEADER, check_init_data, sign_init_data
from app.main import app
from app.models.schemas import Customer, Store, StoreSummary
from app.services.conversation_service import InMemoryConversationStore
from app.services.supabase_service import DatabaseError, NotFoundError, OutOfStockError
from app.services.telegram_service import TELEGRAM_API, TelegramService
from tests.test_conversation import EchoFlow

TOKEN_A = "111111111:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
TOKEN_B = "222222222:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
PLATFORM_TOKEN = "999999999:PPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPP"
GROUP_A, GROUP_B = -500, -600
OWNER, ADMIN, MEMBER, STRANGER, LEFT, CREATOR = 1, 2, 3, 4, 5, 6
STATUS_IN_A = {OWNER: "creator", ADMIN: "administrator", MEMBER: "member", LEFT: "left"}
STATUS_IN_B = {STRANGER: "member"}
PUBLIC_URL = "https://example.ngrok-free.dev"

STORE_A = Store(id=uuid4(), name="Selam Shoes", telegram_bot_token=TOKEN_A, webhook_secret="sa",
                staff_chat_id=GROUP_A, telegram_bot_username="selam_bot", status="active",
                channel_id=-100500, payment_instructions="Telebirr 0911", owner_telegram_id=CREATOR)
STORE_B = Store(id=uuid4(), name="Other shop", telegram_bot_token=TOKEN_B, webhook_secret="sb",
                staff_chat_id=GROUP_B, telegram_bot_username="other_bot", status="active")


def init_data(user_id, token=TOKEN_A, age=0, **user):
    fields = {"auth_date": str(int(time.time()) - age), "query_id": "q1",
              "user": json.dumps({"id": user_id, "first_name": f"User{user_id}", **user})}
    return sign_init_data(fields, token)


def headers(user_id, token=TOKEN_A, **kwargs):
    return {INIT_DATA_HEADER: init_data(user_id, token, **kwargs)}


# --- Fakes ------------------------------------------------------------------------

def _variant(color, size, stock, price=None):
    return {"id": str(uuid4()), "color": color, "size": size, "stock_quantity": stock, "price_override": price}


class FakeDb:
    def __init__(self):
        self.stores = {STORE_A.id: STORE_A, STORE_B.id: STORE_B}
        jacket = {"id": str(uuid4()), "store_id": str(STORE_A.id), "code": "P101", "name": "Denim Jacket",
                  "brand": "Levi's", "category": "clothing", "base_price": 3500, "photo_url": None,
                  "description": None, "search_keywords": "jacket, ጃኬት",
                  "product_variants": [_variant("Blue", "M", 5), _variant("Blue", "XL", 1, 3800)]}
        other = {"id": str(uuid4()), "store_id": str(STORE_B.id), "code": "P101", "name": "B's shoe",
                 "brand": None, "category": "sneakers", "base_price": 100, "photo_url": None,
                 "description": None, "search_keywords": None, "product_variants": [_variant("Red", "40", 3)]}
        self.products = {UUID(jacket["id"]): jacket, UUID(other["id"]): other}
        self.ordered_variants: set[UUID] = set()  # variants with order lines (can't be deleted)
        self.platform_admins = {OWNER}
        self.uploads = []
        self.analytics_calls = []
        self.links = {}
        self.order_queries = []
        self.orders = [
            {"id": str(uuid4()), "store_id": str(STORE_A.id), "status": "pending", "payment_status": "unpaid",
             "total_price": 7300, "currency": "ETB", "fulfillment_method": "delivery",
             "contact_name": "Abebe", "contact_phone": "0911223344",
             "delivery_address": ADDRESS_TO_ARRANGE, "created_at": "2026-10-02T09:00:00+00:00",
             "order_items": [{"quantity": 2, "price": 3650,
                              "product_variants": {"color": "Blue", "size": "M",
                                                   "products": {"name": "Denim Jacket", "code": "P101"}}}]},
            {"id": str(uuid4()), "store_id": str(STORE_B.id), "status": "confirmed", "payment_status": "paid",
             "total_price": 100, "currency": "ETB", "fulfillment_method": "pickup", "contact_name": "B",
             "contact_phone": "0900000000", "delivery_address": None,
             "created_at": "2026-10-02T08:00:00+00:00", "order_items": []},
        ]

    # stores
    async def get_store(self, store_id):
        store = self.stores.get(store_id)
        return store if store and store.status == "active" else None

    async def get_store_any_status(self, store_id):
        return self.stores.get(store_id)

    async def update_store_profile(self, store_id, fields):
        self.stores[store_id] = self.stores[store_id].model_copy(update=fields)

    async def set_link_code(self, store_id, code, expires_at):
        self.links[store_id] = code

    async def get_or_create_customer(self, store_id, telegram_id, name=None):
        return Customer(id=uuid4(), store_id=store_id, telegram_id=telegram_id, name=name)

    async def list_products(self, store_id, limit=100):
        return []

    # analytics
    async def store_analytics(self, store_id, start, end):
        self.analytics_calls.append((store_id, start, end))
        return {"revenue": 9000, "payments": 2, "orders_placed": 4, "orders_paid": 2, "unpaid_orders": 2,
                "delivery_orders": 1, "pickup_orders": 3, "new_customers": 3,
                "per_day": [{"day": "2026-10-02", "placed": 4, "paid": 2, "revenue": 9000}],
                "top_products": [{"product_id": "p", "name": "Denim Jacket", "code": "P101",
                                  "quantity": 2, "revenue": 7000}]}

    async def low_stock(self, store_id, at_most=2, limit=50):
        return [{"variant_id": v["id"], "stock": v["stock_quantity"]}
                for p in self._of(store_id) for v in p["product_variants"] if v["stock_quantity"] <= at_most]

    async def ai_calls_today(self, store_id, today):
        return 7

    # products
    def _of(self, store_id):
        return [p for p in self.products.values() if p["store_id"] == str(store_id)]

    def _variant_row(self, store_id, variant_id):
        for p in self._of(store_id):
            for v in p["product_variants"]:
                if v["id"] == str(variant_id):
                    return p, v
        raise NotFoundError("variant_not_found")

    async def list_products_with_variants(self, store_id):
        return deepcopy(self._of(store_id))

    async def get_product_with_variants(self, store_id, product_id):
        p = self.products.get(product_id)
        return deepcopy(p) if p and p["store_id"] == str(store_id) else None

    async def create_product(self, store_id, fields):
        pid = uuid4()
        self.products[pid] = {"id": str(pid), "store_id": str(store_id), "code": f"P{100 + len(self.products)}",
                              "brand": None, "category": None, "base_price": None, "photo_url": None,
                              "description": None, "search_keywords": None, **fields, "product_variants": []}
        return pid

    async def update_product(self, store_id, product_id, fields):
        p = self.products.get(product_id)
        if not p or p["store_id"] != str(store_id):
            raise NotFoundError("product_not_found")
        p.update(fields)

    async def delete_product(self, store_id, product_id):
        p = self.products.get(product_id)
        if not p or p["store_id"] != str(store_id):
            raise NotFoundError("product_not_found")
        if any(UUID(v["id"]) in self.ordered_variants for v in p["product_variants"]):
            raise DatabaseError("database_error", "23503: foreign key")
        del self.products[product_id]

    async def add_variant(self, store_id, product_id, color, size, stock, price_override):
        v = _variant(color, size, stock, float(price_override) if price_override is not None else None)
        self.products[product_id]["product_variants"].append(v)
        return UUID(v["id"])

    async def update_variant(self, store_id, variant_id, fields):
        _, v = self._variant_row(store_id, variant_id)
        v.update({k: (float(val) if k == "price_override" and val is not None else val) for k, val in fields.items()})
        return dict(v)

    async def delete_variant(self, store_id, variant_id):
        p, v = self._variant_row(store_id, variant_id)
        if variant_id in self.ordered_variants:
            raise DatabaseError("database_error", "23503: foreign key")
        p["product_variants"].remove(v)

    async def update_stock(self, store_id, variant_id, delta):
        _, v = self._variant_row(store_id, variant_id)
        if v["stock_quantity"] + delta < 0:
            raise OutOfStockError("insufficient_stock")
        v["stock_quantity"] += delta
        return v["stock_quantity"]

    async def take_off_sale(self, store_id, product_id):
        variants = self.products[product_id]["product_variants"]
        for v in variants:
            v["stock_quantity"] = 0
        return len(variants)

    async def upload_photo(self, store_id, name, data, content_type):
        self.uploads.append((store_id, name, len(data), content_type))
        return f"https://storage.example/product-photos/{store_id}/{name}"

    async def list_orders(self, store_id, payment_status=None, limit=30, before=None, channel=None):
        self.order_queries.append((store_id, payment_status, limit, before, channel))
        rows = [o for o in self.orders if o["store_id"] == str(store_id)
                and (payment_status is None or o["payment_status"] == payment_status)
                and (channel is None or o.get("channel", "telegram") == channel)]
        return rows[:limit]

    # platform
    async def is_platform_admin_telegram(self, telegram_id):
        return telegram_id in self.platform_admins

    async def stores_created_by(self, telegram_id):
        return [s for s in self.stores.values() if s.owner_telegram_id == telegram_id]

    async def find_store_by_bot(self, bot_id):
        return next((s for s in self.stores.values() if s.telegram_bot_id == bot_id), None)

    async def create_store_for_telegram(self, name, token, bot_id, username, secret, owner):
        store = Store(id=uuid4(), name=name, telegram_bot_token=token, telegram_bot_id=bot_id,
                      telegram_bot_username=username, webhook_secret=secret, status="pending",
                      plan="free", owner_telegram_id=owner)
        self.stores[store.id] = store
        return store.id

    async def list_all_stores(self):
        return [StoreSummary(id=s.id, name=s.name, status=s.status, plan=s.plan or "free")
                for s in self.stores.values()]

    async def set_store_status(self, store_id, status, reason=None):
        self.stores[store_id] = self.stores[store_id].model_copy(
            update={"status": status, "suspended_reason": reason if status == "suspended" else None})

    async def set_plan_end(self, store_id, ends_at):
        self.stores[store_id] = self.stores[store_id].model_copy(update={"plan_ends_at": ends_at})

    async def set_store_plan(self, store_id, plan):
        self.stores[store_id] = self.stores[store_id].model_copy(update={"plan": plan})


class FakeTelegram:
    def __init__(self):
        self.calls = []
        self.member_checks = 0
        self.down = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        _, bot, method = request.url.path.split("/")
        token = bot.removeprefix("bot")
        body = json.loads(request.content or b"{}")
        self.calls.append((token, method, body))
        if method == "getChatMember":
            self.member_checks += 1
            if self.down:
                return httpx.Response(502, json={"ok": False, "description": "Bad Gateway"})
            statuses = {(TOKEN_A, GROUP_A): STATUS_IN_A, (TOKEN_B, GROUP_B): STATUS_IN_B}[(token, body["chat_id"])]
            status = statuses.get(body["user_id"])
            if status is None:
                return httpx.Response(400, json={"ok": False, "description": "Bad Request: user not found"})
            return httpx.Response(200, json={"ok": True, "result": {"status": status}})
        if method == "getChat":
            titles = {GROUP_A: "Selam staff", -100500: "Selam Shoes channel"}
            if body["chat_id"] not in titles:
                return httpx.Response(400, json={"ok": False, "description": "Bad Request: chat not found"})
            return httpx.Response(200, json={"ok": True, "result": {"title": titles[body["chat_id"]]}})
        if method == "getMe":
            return httpx.Response(200, json={"ok": True, "result": {
                "id": 333, "is_bot": True, "first_name": "New", "username": "new_shop_bot"}})
        if method == "sendMessage":
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 900}})
        return httpx.Response(200, json={"ok": True, "result": True})

    def sent(self, chat_id):
        return [b for _, m, b in self.calls if m == "sendMessage" and b["chat_id"] == chat_id]


class FakeCatalog:
    def __init__(self):
        self.published = []

    async def publish(self, store, product_id):
        from app.agents.catalog import PublishResult
        self.published.append((store.id, product_id))
        return PublishResult(True, "P101 is posted in the channel.")


@pytest.fixture
def world(monkeypatch):
    db, telegram, catalog = FakeDb(), FakeTelegram(), FakeCatalog()
    service = TelegramService(httpx.AsyncClient(base_url=TELEGRAM_API,
                                                transport=httpx.MockTransport(telegram.handler)))
    orchestrator = Orchestrator(db, InMemoryConversationStore(), service, None, burst_wait=0, flow=EchoFlow())
    settings = get_settings()
    monkeypatch.setattr(settings, "public_base_url", PUBLIC_URL)
    monkeypatch.setattr(settings, "platform_bot_token", type(settings.platform_bot_token)(PLATFORM_TOKEN))
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_orchestrator] = lambda: orchestrator
    app.dependency_overrides[get_catalog] = lambda: catalog
    app.dependency_overrides[get_onboarding] = lambda: Onboarding(db, service, PUBLIC_URL)
    yield db, telegram, TestClient(app), catalog, orchestrator
    app.dependency_overrides.clear()


def url(path, store=STORE_A):
    return f"/api/v1/app/stores/{store.id}{path}"


def jacket(db):
    return next(p for p in db.products.values() if p["name"] == "Denim Jacket")


# --- Telegram's signature ------------------------------------------------------------

def test_signed_init_data_is_accepted_and_anything_else_refused():
    good = init_data(MEMBER)
    assert check_init_data(good, TOKEN_A).id == MEMBER
    assert check_init_data(good, TOKEN_B) is None  # signed by another bot
    assert check_init_data(good.replace("User3", "User4"), TOKEN_A) is None  # altered
    assert check_init_data(init_data(MEMBER, age=25 * 3600), TOKEN_A) is None  # older than a day
    assert check_init_data("user=%7B%7D&auth_date=1", TOKEN_A) is None  # no signature
    assert check_init_data("%%%", TOKEN_A) is None and check_init_data(None, TOKEN_A) is None
    assert check_init_data(good, None) is None


# --- Who gets in (D42) ---------------------------------------------------------------

@pytest.mark.parametrize("user, role", [(OWNER, "owner"), (ADMIN, "owner"), (MEMBER, "staff"),
                                        (CREATOR, "owner")])
def test_staff_group_decides_the_role(world, user, role):
    _, _, client, _, _ = world
    response = client.get(url("/me"), headers=headers(user))
    assert response.status_code == 200 and response.json()["role"] == role
    assert response.json()["store"]["name"] == "Selam Shoes"


@pytest.mark.parametrize("user", [STRANGER, LEFT, 777])
def test_outsiders_are_refused(world, user):
    _, _, client, _, _ = world
    assert client.get(url("/me"), headers=headers(user)).status_code == 403


def test_a_login_from_another_stores_bot_is_refused(world):
    _, _, client, _, _ = world
    assert client.get(url("/me"), headers=headers(OWNER, TOKEN_B)).status_code == 401
    assert client.get(url("/me")).status_code == 401
    # A member of store B's group, with store B's bot, can't open store A...
    assert client.get(url("/me", STORE_A), headers=headers(STRANGER, TOKEN_A)).status_code == 403
    # ...and store A's owner can't open store B.
    assert client.get(url("/me", STORE_B), headers=headers(OWNER, TOKEN_B)).status_code == 403


def test_membership_is_remembered_for_a_while(world):
    _, telegram, client, _, _ = world
    for _ in range(3):
        assert client.get(url("/me"), headers=headers(MEMBER)).status_code == 200
    assert telegram.member_checks == 1


@pytest.mark.anyio
async def test_membership_check_falls_back_when_telegram_is_down():
    telegram = FakeTelegram()
    service = TelegramService(httpx.AsyncClient(base_url=TELEGRAM_API,
                                                transport=httpx.MockTransport(telegram.handler)))
    now = [0.0]
    access = MiniAppAccess(service, clock=lambda: now[0])
    assert await access.role(STORE_A, MEMBER) == "staff"
    telegram.down, now[0] = True, 1000.0  # remembered answer expired, Telegram down
    assert await access.role(STORE_A, MEMBER) == "staff"  # the last known answer
    assert await access.role(STORE_A, ADMIN) is None  # never known: no access
    assert await access.role(STORE_A, CREATOR) == "owner"  # the creator needs no Telegram call


# --- /dashboard ----------------------------------------------------------------------------

def _private(user_id, text):
    return {"update_id": 1, "message": {"message_id": 1, "date": 1790000000, "text": text,
                                        "chat": {"id": user_id, "type": "private"},
                                        "from": {"id": user_id, "first_name": "U"}}}


def _webhook(client, update, store=STORE_A):
    return client.post(f"/api/v1/webhook/{store.id}", json=update,
                       headers={SECRET_HEADER: store.webhook_secret.get_secret_value()})


def test_dashboard_command_gives_staff_the_mini_app_button(world):
    _, telegram, client, _, _ = world
    _webhook(client, _private(MEMBER, "/dashboard"))
    [sent] = telegram.sent(MEMBER)
    button = sent["reply_markup"]["inline_keyboard"][0][0]
    assert button["web_app"]["url"] == f"{PUBLIC_URL}/app/?store={STORE_A.id}"
    _webhook(client, _private(ADMIN, "/start dashboard"))
    assert "web_app" in telegram.sent(ADMIN)[0]["reply_markup"]["inline_keyboard"][0][0]


def test_customers_dont_get_the_dashboard(world):
    _, telegram, client, _, _ = world
    _webhook(client, _private(STRANGER, "/dashboard"))
    [sent] = telegram.sent(STRANGER)
    assert "reply_markup" not in sent and "staff group" in sent["text"]


def test_dashboard_in_the_staff_group_links_to_the_private_chat(world):
    _, telegram, client, _, _ = world
    group = {"update_id": 2, "message": {"message_id": 9, "date": 1790000000, "text": "/dashboard@selam_bot",
                                         "chat": {"id": GROUP_A, "type": "group"},
                                         "from": {"id": MEMBER, "first_name": "U"}}}
    _webhook(client, group)
    [sent] = telegram.sent(GROUP_A)
    assert sent["reply_markup"]["inline_keyboard"][0][0]["url"] == "https://t.me/selam_bot?start=dashboard"


# --- Analytics ----------------------------------------------------------------------------

def test_analytics_for_staff(world):
    db, _, client, _, _ = world
    body = client.get(url("/analytics?period=7d"), headers=headers(MEMBER)).json()
    assert body["period"] == "7d" and body["revenue"] == "9000" and body["average_order"] == "4500.00"
    assert body["paid_rate"] == 0.5 and body["orders_placed"] == 4 and body["unpaid_orders"] == 2
    assert body["top_products"][0]["name"] == "Denim Jacket" and body["ai_calls_today"] == 7
    assert [s["stock"] for s in body["low_stock"]] == [1]  # Blue XL, only store A's
    _, start, end = db.analytics_calls[-1]
    assert (end - start).days == 7 and start.utcoffset().total_seconds() == 3 * 3600
    assert client.get(url("/analytics?period=year"), headers=headers(MEMBER)).status_code == 422


# --- Products and stock (inventory) ---------------------------------------------------------

def test_product_list_search_and_categories(world):
    _, _, client, _, _ = world
    body = client.get(url("/products"), headers=headers(MEMBER)).json()
    [item] = body["products"]  # never store B's products
    assert item["name"] == "Denim Jacket" and item["total_stock"] == 6 and item["low_stock"]
    assert item["price_min"] == "3500" and item["price_max"] == "3800" and body["categories"] == ["clothing"]
    assert client.get(url("/products?search=ጃኬት"), headers=headers(MEMBER)).json()["products"]
    assert client.get(url("/products?search=boots"), headers=headers(MEMBER)).json()["products"] == []
    assert client.get(url("/products?category=sneakers"), headers=headers(MEMBER)).json()["products"] == []


def test_owner_adds_a_product_with_its_grid(world):
    db, _, client, _, _ = world
    body = {"product": {"name": " Polo  T-Shirt ", "category": "Clothing", "base_price": 1800,
                        "photo_url": "https://cdn.example/polo.jpg"},
            "variants": [{"color": "White", "size": "M", "stock": 8},
                         {"color": "Navy", "size": "XL", "stock": 3, "price": 2000}]}
    response = client.post(url("/products"), json=body, headers=headers(OWNER))
    assert response.status_code == 201
    product = response.json()
    assert product["name"] == "Polo T-Shirt" and product["category"] == "clothing"
    assert product["total_stock"] == 11 and product["variant_count"] == 2
    assert {v["price"] for v in product["variants"]} == {"1800", "2000"}


def test_staff_manage_products_and_stock_but_not_prices(world):
    db, _, client, _, _ = world
    pid = jacket(db)["id"]
    # Staff add a product and its sizes without prices (the owner sets them).
    added = client.post(url("/products"), json={"product": {"name": "Scarf"},
                                                "variants": [{"color": "Red", "size": "One", "stock": 3}]},
                        headers=headers(MEMBER))
    assert added.status_code == 201 and added.json()["total_stock"] == 3
    # An owner's product without colors or sizes still shows its price.
    bare = client.post(url("/products"), json={"product": {"name": "Cap", "base_price": 900}},
                       headers=headers(OWNER)).json()
    assert bare["price_min"] == bare["price_max"] == "900" and bare["variant_count"] == 0
    assert client.post(url("/products"), json={"product": {"name": "X", "base_price": 10}},
                       headers=headers(MEMBER)).status_code == 403
    assert client.post(url("/products"), json={"product": {"name": "X"},
                                               "variants": [{"color": "R", "size": "S", "stock": 1, "price": 9}]},
                       headers=headers(MEMBER)).status_code == 403
    assert client.patch(url(f"/products/{pid}"), json={"base_price": 1}, headers=headers(MEMBER)).status_code == 403
    # Staff edit the details (design: "Staff price locked"), even sending the unchanged price.
    edited = client.patch(url(f"/products/{pid}"), json={"description": "Soft denim", "base_price": 3500},
                          headers=headers(MEMBER))
    assert edited.status_code == 200 and edited.json()["description"] == "Soft denim"
    # The grid: stock changes and a new size are fine; a price change isn't.
    blue_m, blue_xl = jacket(db)["product_variants"]
    grid = {"variants": [{"id": blue_m["id"], "color": "Blue", "size": "M", "stock": 9},
                         {"id": blue_xl["id"], "color": "Blue", "size": "XL", "stock": 2, "price": 3800},
                         {"color": "Blue", "size": "L", "stock": 4}]}
    assert client.put(url(f"/products/{pid}/variants"), json=grid, headers=headers(MEMBER)).status_code == 200
    grid["variants"][1]["price"] = 3000
    assert client.put(url(f"/products/{pid}/variants"), json=grid, headers=headers(MEMBER)).status_code == 403
    assert client.post(url(f"/products/{pid}/off-sale"), headers=headers(MEMBER)).status_code == 403
    assert client.get(url("/settings"), headers=headers(MEMBER)).status_code == 403
    assert jacket(db)["base_price"] == 3500


def test_bad_product_details_are_refused(world):
    _, _, client, _, _ = world
    for product in ({"name": ""}, {"name": "X", "base_price": -1}, {"name": "X", "photo_url": "data:image/png;base64,AA"},
                    {"name": "X", "description": "x" * 701}):
        assert client.post(url("/products"), json={"product": product}, headers=headers(OWNER)).status_code == 422
    twice = {"product": {"name": "X"}, "variants": [{"color": "Red", "size": "M", "stock": 1},
                                                    {"color": "red", "size": "m", "stock": 2}]}
    assert client.post(url("/products"), json=twice, headers=headers(OWNER)).status_code == 400


def test_staff_change_stock_never_below_zero(world):
    db, _, client, _, _ = world
    variant = jacket(db)["product_variants"][0]  # Blue M, 5
    stock = lambda body: client.post(url(f"/variants/{variant['id']}/stock"), json=body, headers=headers(MEMBER))
    assert stock({"change": 4}).json()["stock"] == 9  # new stock arrived
    assert stock({"change": -1}).json()["stock"] == 8  # sold at the counter
    assert stock({"set": 6}).json()["stock"] == 6  # after counting
    assert stock({"change": -7}).status_code == 409
    assert stock({"set": -1}).status_code == 422
    assert stock({}).status_code == 400 and stock({"change": 1, "set": 2}).status_code == 400
    assert jacket(db)["product_variants"][0]["stock_quantity"] == 6


def test_another_stores_variant_cant_be_touched(world):
    db, _, client, _, _ = world
    b_variant = next(p for p in db.products.values() if p["store_id"] == str(STORE_B.id))["product_variants"][0]
    response = client.post(url(f"/variants/{b_variant['id']}/stock"), json={"set": 0}, headers=headers(OWNER))
    assert response.status_code == 404 and b_variant["stock_quantity"] == 3
    b_product = next(p for p in db.products.values() if p["store_id"] == str(STORE_B.id))["id"]
    assert client.get(url(f"/products/{b_product}"), headers=headers(OWNER)).status_code == 404
    assert client.patch(url(f"/products/{b_product}"), json={"name": "Mine"}, headers=headers(OWNER)).status_code == 404


def test_saving_the_grid_updates_adds_and_removes(world):
    db, _, client, _, _ = world
    product = jacket(db)
    blue_m, blue_xl = product["product_variants"]
    db.ordered_variants.add(UUID(blue_xl["id"]))  # XL was ordered before
    grid = {"variants": [{"color": "Blue", "size": "M", "stock": 10},  # matched by color + size
                         {"color": "Black", "size": "L", "stock": 4, "price": 3600}],  # new
            "remove": [blue_xl["id"]]}
    body = client.put(url(f"/products/{product['id']}/variants"), json=grid, headers=headers(OWNER)).json()
    assert (body["added"], body["updated"], body["removed"]) == (1, 1, 0)
    assert "Blue XL" in body["note"]  # ordered before: kept with stock 0
    stocks = {(v["color"], v["size"]): v["stock"] for v in body["product"]["variants"]}
    assert stocks == {("Blue", "M"): 10, ("Blue", "XL"): 0, ("Black", "L"): 4}


def test_off_sale_delete_and_publish(world):
    db, _, client, catalog, _ = world
    pid = jacket(db)["id"]
    assert client.post(url(f"/products/{pid}/off-sale"), headers=headers(OWNER)).json()["variants"] == 2
    assert all(v["stock_quantity"] == 0 for v in jacket(db)["product_variants"])
    db.ordered_variants.add(UUID(jacket(db)["product_variants"][0]["id"]))
    refused = client.delete(url(f"/products/{pid}"), headers=headers(OWNER))
    assert refused.status_code == 409 and "off sale" in refused.json()["detail"]
    assert client.post(url(f"/products/{pid}/publish"), headers=headers(OWNER)).json()["ok"]
    assert catalog.published == [(STORE_A.id, UUID(pid))]
    new = client.post(url("/products"), json={"product": {"name": "Never ordered"}}, headers=headers(OWNER)).json()
    assert client.delete(url(f"/products/{new['id']}"), headers=headers(OWNER)).json() == {"ok": True}


def test_photo_upload(world):
    db, _, client, _, _ = world
    upload = lambda name, data, kind, who=OWNER: client.post(
        url("/photos"), files={"file": (name, data, kind)}, headers=headers(who))
    response = upload("p.jpg", b"\xff\xd8 jpeg bytes", "image/jpeg")
    assert response.status_code == 201
    assert response.json()["photo_url"].startswith(f"https://storage.example/product-photos/{STORE_A.id}/")
    assert db.uploads[-1][0] == STORE_A.id  # always the store's own folder
    assert upload("p.gif", b"GIF89a", "image/gif").status_code == 415
    assert upload("big.jpg", b"x" * (5 * 1024 * 1024 + 1), "image/jpeg").status_code == 413
    assert upload("p.jpg", b"\xff\xd8", "image/jpeg", who=MEMBER).status_code == 201  # staff too
    assert upload("p.jpg", b"\xff\xd8", "image/jpeg", who=STRANGER).status_code == 403


# --- Settings (owners) -----------------------------------------------------------------------

def test_owner_settings_and_link_code(world):
    db, _, client, _, _ = world
    assert client.get(url("/settings"), headers=headers(ADMIN)).json()["payment_instructions"] == "Telebirr 0911"
    week = {day: {"open": True, "from": "08:30", "to": "19:00"} for day in ("mon", "tue", "wed", "thu", "fri", "sat")}
    week["sun"] = {"open": False}
    saved = client.put(url("/settings"), json={
        "payment_accounts": [{"name": "Telebirr", "number": "0911 000 000"}, {"name": "CBE", "number": "1000 1234"}],
        "delivery_areas": [{"area": "Bole", "fee": 150}, {"area": "Other areas", "fee": 250}],
        "opening_week": week, "return_policy": "  Exchange within 3 days ",
    }, headers=headers(ADMIN)).json()
    # The lists, and the texts the bot sends written from them.
    assert saved["payment_accounts"][1] == {"name": "CBE", "number": "1000 1234"}
    assert saved["payment_instructions"] == "Telebirr: 0911 000 000\nCBE: 1000 1234"
    assert saved["delivery_info"] == "Bole: 150 ETB\nOther areas: 250 ETB"
    assert saved["opening_hours"] == "Mon–Sat 08:30–19:00, Sun closed"
    assert saved["return_policy"] == "Exchange within 3 days" and saved["location"] is None  # not sent: unchanged
    bad_week = {**week, "mon": {"open": True, "from": "19:00", "to": "08:30"}}
    assert client.put(url("/settings"), json={"opening_week": bad_week}, headers=headers(ADMIN)).status_code == 422
    assert client.put(url("/settings"), json={"delivery_areas": [{"area": "X", "fee": -1}]},
                      headers=headers(ADMIN)).status_code == 422
    code = client.post(url("/link-code"), headers=headers(OWNER)).json()
    assert code["command"] == f"/link {code['code']}" and db.links[STORE_A.id] == code["code"]
    assert client.post(url("/link-code"), headers=headers(MEMBER)).status_code == 403


# --- The platform bot (D44–D45) ------------------------------------------------------------------

def platform(user_id):
    return {INIT_DATA_HEADER: init_data(user_id, PLATFORM_TOKEN)}


def test_sign_up_in_the_platform_bot(world):
    db, _, client, _, _ = world
    assert client.get("/api/v1/platform-app/me", headers=headers(MEMBER)).status_code == 401  # a store bot's login
    created = client.post("/api/v1/platform-app/stores", json={"name": "Nati Fashion", "bot_token": TOKEN_B[:10] + "Z" * 35},
                          headers=platform(MEMBER))
    assert created.status_code == 201 and created.json()["status"] == "pending"
    assert created.json()["dashboard_url"].startswith(f"{PUBLIC_URL}/app/?store=")
    me = client.get("/api/v1/platform-app/me", headers=platform(MEMBER)).json()
    assert [s["name"] for s in me["stores"]] == ["Nati Fashion"] and not me["is_platform_admin"]
    new_store = db.stores[UUID(created.json()["id"])]
    # The creator can open the new store's dashboard right away (no staff group yet).
    creator_login = {INIT_DATA_HEADER: init_data(MEMBER, new_store.telegram_bot_token.get_secret_value())}
    assert client.get(url("/me", new_store), headers=creator_login).json()["role"] == "owner"


def test_platform_admin_approves(world):
    db, _, client, _, _ = world
    pending = STORE_B.model_copy(update={"status": "pending"})
    db.stores[STORE_B.id] = pending
    assert client.get("/api/v1/platform-app/admin/stores", headers=platform(MEMBER)).status_code == 403
    assert len(client.get("/api/v1/platform-app/admin/stores", headers=platform(OWNER)).json()) == 2
    approved = client.post(f"/api/v1/platform-app/admin/stores/{STORE_B.id}/approve", headers=platform(OWNER))
    assert approved.json()["status"] == "active" and db.stores[STORE_B.id].status == "active"
    plan = client.put(f"/api/v1/platform-app/admin/stores/{STORE_B.id}/plan", json={"plan": "pro"},
                      headers=platform(OWNER))
    assert plan.json()["plan"] == "pro"


def test_platform_bot_webhook_answers_with_the_app_button(world):
    _, telegram, client, _, _ = world
    hook = lambda secret: client.post("/api/v1/platform-bot/webhook", json=_private(MEMBER, "/start"),
                                      headers={SECRET_HEADER: secret})
    assert hook("wrong").status_code == 401
    assert hook(platform_webhook_secret(PLATFORM_TOKEN)).status_code == 200
    [sent] = telegram.sent(MEMBER)
    assert sent["reply_markup"]["inline_keyboard"][0][0]["web_app"]["url"] == f"{PUBLIC_URL}/app/platform"


def test_mini_app_page_is_served(world):
    _, _, client, _, _ = world
    assert "telegram-web-app.js" in client.get(f"/app/?store={STORE_A.id}").text
    assert client.get("/app/platform").status_code == 200


# --- Orders (view only) and connections ------------------------------------------------------

def test_orders_list_for_staff(world):
    db, _, client, _, _ = world
    body = client.get(url("/orders"), headers=headers(MEMBER)).json()
    [order] = body["orders"]  # never store B's
    assert order["payment_status"] == "unpaid" and order["total"] == 7300 and order["fulfillment"] == "delivery"
    assert order["delivery_address"] is None  # "to be arranged" isn't an address
    assert order["items"] == [{"name": "Denim Jacket", "code": "P101", "color": "Blue", "size": "M",
                               "quantity": 2, "price": 3650, "list_price": None}]
    assert order["channel"] == "telegram" and order["sold_by"] is None
    assert order["number"] and order["customer"] == {"name": "Abebe", "phone": "0911223344"}
    client.get(url("/orders?status=paid&limit=5"), headers=headers(MEMBER))
    assert db.order_queries[-1][1:3] == ("paid", 5)
    assert client.get(url("/orders?channel=in_shop"), headers=headers(MEMBER)).json()["orders"] == []
    assert db.order_queries[-1][4] == "in_shop"
    assert client.get(url("/orders"), headers=headers(STRANGER)).status_code == 403


def test_connections_show_names(world):
    db, _, client, _, _ = world
    body = client.get(url("/connections"), headers=headers(OWNER)).json()
    assert body["staff_group"] == {"id": GROUP_A, "title": "Selam staff", "bot_can_see": True}
    assert body["channel"]["title"] == "Selam Shoes channel"
    db.stores[STORE_A.id] = STORE_A.model_copy(update={"channel_id": None})
    assert client.get(url("/connections"), headers=headers(OWNER)).json()["channel"] is None
    assert client.get(url("/connections"), headers=headers(MEMBER)).status_code == 403


def test_approval_tells_the_creator_in_the_platform_bot(world, monkeypatch):
    db, telegram, client, _, _ = world
    monkeypatch.setattr(get_settings(), "support_username", "@nati_support")
    created = client.post("/api/v1/platform-app/stores",
                          json={"name": "Nati Fashion", "bot_token": TOKEN_B[:10] + "Z" * 35},
                          headers=platform(MEMBER)).json()
    assert client.get("/api/v1/platform-app/me", headers=platform(MEMBER)).json()["support_url"] == \
        "https://t.me/nati_support"
    client.post(f"/api/v1/platform-app/admin/stores/{created['id']}/approve", headers=platform(OWNER))
    told = [b for t, m, b in telegram.calls if t == PLATFORM_TOKEN and m == "sendMessage" and b["chat_id"] == MEMBER]
    assert told and told[-1]["text"].startswith("✅ Nati Fashion is approved")


@pytest.fixture
def anyio_backend():
    return "asyncio"
