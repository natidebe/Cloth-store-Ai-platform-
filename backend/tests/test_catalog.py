"""Phase 8d: the channel catalog (D30–D40). No network: Telegram and the
database are faked."""
import json
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

import app.api.v1.catalog as catalog_api
from app.agents.catalog import GONE, ORDER_LABEL, SOLD_OUT, Catalog, caption, order_link
from app.api.v1.catalog import get_catalog
from app.main import app
from app.models.schemas import Product, ProductPost, Store, VariantMatch
from app.services.telegram_service import TELEGRAM_API, TelegramService

pytestmark = pytest.mark.anyio

CHANNEL = -100777
STORE = Store(id=uuid4(), name="Selam Shoes", telegram_bot_token="123:TOKEN", webhook_secret="s",
              channel_id=CHANNEL)
NO_CHANNEL = Store(id=uuid4(), name="No channel", telegram_bot_token="456:TOKEN", webhook_secret="s")
AF1 = UUID("00000000-0000-0000-0000-00000000af01")


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _variant(color, size, stock, price="5000"):
    return VariantMatch(variant_id=uuid4(), product_id=AF1, product_name="Air Force 1", brand="Nike",
                        category="sneakers", color=color, size=size, stock_quantity=stock,
                        price=Decimal(price))


class FakeDb:
    def __init__(self):
        self.products = {AF1: Product(id=AF1, store_id=STORE.id, name="Air Force 1", brand="Nike",
                                      code="P101", description="Classic white sneakers.",
                                      photo_url="https://photos.example/af1.jpg")}
        self.variants = {AF1: [_variant("White", "42", 2), _variant("White", "43", 5),
                               _variant("Black", "42", 1)]}
        self.posts: list[ProductPost] = []

    async def get_store(self, store_id):
        return {STORE.id: STORE, NO_CHANNEL.id: NO_CHANNEL}.get(store_id)

    async def stores_with_channel(self):
        return [STORE]

    async def get_product(self, store_id, product_id):
        product = self.products.get(product_id)
        return product if product and product.store_id == store_id else None

    async def list_catalog(self, store_id):
        return [p for p in self.products.values() if p.store_id == store_id]

    async def get_product_variants(self, store_id, product_id):
        return list(self.variants.get(product_id, []))

    async def list_product_posts(self, store_id, product_id=None, code=None):
        return [p for p in self.posts if p.store_id == store_id
                and (product_id is None or p.product_id == product_id)
                and (code is None or p.product_code == code)]

    async def save_product_post(self, store_id, product, channel_id, message_id, has_photo, caption_hash):
        self.posts.append(ProductPost(id=len(self.posts) + 1, store_id=store_id, product_id=product.id,
                                      product_code=product.code, channel_id=channel_id,
                                      message_id=message_id, has_photo=has_photo,
                                      caption_hash=caption_hash))

    async def set_post_hash(self, store_id, post_id, caption_hash):
        post = next(p for p in self.posts if p.id == post_id and p.store_id == store_id)
        self.posts[self.posts.index(post)] = post.model_copy(update={"caption_hash": caption_hash})

    def delete_product(self, product_id):
        del self.products[product_id]
        self.posts = [p.model_copy(update={"product_id": None}) if p.product_id == product_id else p
                      for p in self.posts]  # on delete set null


class FakeTelegram:
    def __init__(self):
        self.calls = []  # (method, body)
        self.fail = None  # a method that fails

    def handler(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        body = json.loads(request.content or b"{}")
        self.calls.append((method, body))
        if method == self.fail:
            return httpx.Response(400, json={"ok": False, "error_code": 400,
                                             "description": "Bad Request: message to edit not found"})
        if method == "getMe":
            return httpx.Response(200, json={"ok": True, "result": {"id": 1, "username": "SelamShoesBot"}})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 900 + len(self.calls)}})

    def service(self):
        return TelegramService(httpx.AsyncClient(base_url=TELEGRAM_API,
                                                 transport=httpx.MockTransport(self.handler)))

    def edits(self):
        return [body for method, body in self.calls if method.startswith("editMessage")]


@pytest.fixture
def world():
    db, telegram = FakeDb(), FakeTelegram()
    return db, telegram, Catalog(db, telegram.service(), group_wait=0)


# --- The caption -----------------------------------------------------------------

def test_caption_shows_what_is_in_stock():
    db = FakeDb()
    db.variants[AF1][2] = _variant("Black", "42", 0)  # sold out: not shown
    text = caption(db.products[AF1], db.variants[AF1])
    assert "🆕 Air Force 1 (Nike)" in text and "5,000" in text
    assert "• White: 42, 43" in text and "Black" not in text
    assert "Classic white sneakers." in text and text.endswith("🔖 P101")
    assert SOLD_OUT not in text


def test_caption_of_a_sold_out_product():
    db = FakeDb()
    text = caption(db.products[AF1], [_variant("White", "42", 0)])
    assert text.startswith(SOLD_OUT) and "Colors" not in text


def test_several_prices_show_from():
    text = caption(FakeDb().products[AF1], [_variant("White", "42", 1, "5000"), _variant("Gold", "42", 1, "6500")])
    assert "from" in text and "5,000" in text and "6,500" not in text


def test_order_link():
    assert order_link("SelamShoesBot", "P101") == "https://t.me/SelamShoesBot?start=p_P101"


# --- Posting and editing ------------------------------------------------------------

async def test_publish_posts_photo_with_order_button(world):
    db, telegram, catalog = world
    result = await catalog.publish(STORE, AF1)
    assert result.ok
    method, body = telegram.calls[-1]
    assert method == "sendPhoto" and body["chat_id"] == CHANNEL
    assert body["photo"] == "https://photos.example/af1.jpg"
    [[button]] = body["reply_markup"]["inline_keyboard"]
    assert button == {"text": ORDER_LABEL, "url": "https://t.me/SelamShoesBot?start=p_P101"}
    [post] = db.posts
    assert post.product_code == "P101" and post.has_photo and post.message_id == 900 + len(telegram.calls)


async def test_publish_without_photo_sends_text(world):
    db, telegram, catalog = world
    db.products[AF1] = db.products[AF1].model_copy(update={"photo_url": None})
    await catalog.publish(STORE, AF1)
    assert telegram.calls[-1][0] == "sendMessage" and not db.posts[0].has_photo


async def test_publish_twice_keeps_one_post(world):
    db, telegram, catalog = world
    await catalog.publish(STORE, AF1)
    result = await catalog.publish(STORE, AF1)
    assert result.ok and len(db.posts) == 1
    assert [m for m, _ in telegram.calls].count("sendPhoto") == 1


async def test_publish_needs_a_channel_and_the_stores_own_product(world):
    db, telegram, catalog = world
    assert not (await catalog.publish(NO_CHANNEL, AF1)).ok
    other = STORE.model_copy(update={"id": uuid4()})
    assert not (await catalog.publish(other, AF1)).ok  # AF1 belongs to STORE
    assert telegram.calls == []


async def test_stock_change_edits_the_post_once(world):
    db, telegram, catalog = world
    await catalog.publish(STORE, AF1)
    assert await catalog.sync(STORE, AF1) == 0  # nothing changed: no edit
    db.variants[AF1] = [_variant("White", "43", 5)]
    assert await catalog.sync(STORE, AF1) == 1
    [edit] = telegram.edits()
    assert "caption" in edit and "• White: 43" in edit["caption"] and "Black" not in edit["caption"]
    assert await catalog.sync(STORE, AF1) == 0  # remembered


async def test_deleted_product_post_says_no_longer_available(world):
    db, telegram, catalog = world
    await catalog.publish(STORE, AF1)
    db.delete_product(AF1)
    assert await catalog.sync_deleted(STORE, "P101") == 1
    [edit] = telegram.edits()
    assert edit["caption"].startswith(GONE) and edit["reply_markup"] == {"inline_keyboard": []}


async def test_failed_edit_is_tried_again_later(world):
    db, telegram, catalog = world
    await catalog.publish(STORE, AF1)
    db.variants[AF1] = [_variant("White", "43", 5)]
    telegram.fail = "editMessageCaption"
    assert await catalog.sync(STORE, AF1) == 0
    telegram.fail = None
    assert await catalog.reconcile() == 1  # the sweep's safety net


async def test_reconcile_does_not_post_old_products(world):
    db, telegram, catalog = world
    assert await catalog.reconcile() == 0 and telegram.calls == []


# --- Database webhooks -------------------------------------------------------------

def _change(table, kind, **record):
    return {"type": kind, "table": table, "schema": "public", "record": record, "old_record": None}


async def test_new_product_is_posted_automatically(world):
    db, telegram, catalog = world
    await catalog.on_change(_change("products", "INSERT", id=str(AF1), store_id=str(STORE.id)))
    await catalog.settle()
    assert len(db.posts) == 1


async def test_quick_changes_are_grouped_into_one_edit(world):
    db, telegram, catalog = world
    await catalog.publish(STORE, AF1)
    catalog.group_wait = 0.05
    db.variants[AF1] = [_variant("White", "43", 5)]
    for _ in range(3):
        await catalog.on_change(_change("product_variants", "UPDATE", id=str(uuid4()),
                                        product_id=str(AF1), store_id=str(STORE.id)))
    await catalog.settle()
    assert len(telegram.edits()) == 1


async def test_variant_change_does_not_post_an_unposted_product(world):
    db, telegram, catalog = world
    await catalog.on_change(_change("product_variants", "UPDATE", id=str(uuid4()),
                                    product_id=str(AF1), store_id=str(STORE.id)))
    await catalog.settle()
    assert db.posts == [] and telegram.calls == []


async def test_deleted_product_webhook(world):
    db, telegram, catalog = world
    await catalog.publish(STORE, AF1)
    db.delete_product(AF1)
    await catalog.on_change({"type": "DELETE", "table": "products", "record": None,
                             "old_record": {"id": str(AF1), "store_id": str(STORE.id), "code": "P101"}})
    assert telegram.edits()[0]["caption"].startswith(GONE)


async def test_unreadable_webhook_is_ignored(world):
    db, telegram, catalog = world
    await catalog.on_change({"type": "UPDATE", "table": "products", "record": {"id": "x"}})
    await catalog.settle()
    assert telegram.calls == []


class _Settings:
    catalog_webhook_secret = SecretStr("hook-secret")


def test_webhook_endpoint_checks_the_secret(monkeypatch):
    received = []

    class RecordingCatalog:
        async def on_change(self, change):
            received.append(change)

    monkeypatch.setattr(catalog_api, "get_settings", lambda: _Settings())
    app.dependency_overrides[get_catalog] = lambda: RecordingCatalog()
    try:
        client = TestClient(app)
        change = _change("products", "UPDATE", id=str(AF1), store_id=str(STORE.id))
        assert client.post("/api/v1/catalog/webhook", json=change).status_code == 401
        assert client.post("/api/v1/catalog/webhook", json=change,
                           headers={"X-Webhook-Secret": "wrong"}).status_code == 401
        assert received == []
        response = client.post("/api/v1/catalog/webhook", json=change,
                               headers={"X-Webhook-Secret": "hook-secret"})
        assert response.status_code == 200 and received == [change]
    finally:
        app.dependency_overrides.clear()
