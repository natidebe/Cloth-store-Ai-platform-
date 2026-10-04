"""Phase 15, quick wins (D70–D75): the owner's morning summary, the month's
export for the accountant, and order updates to customers from the staff
group. No network: Telegram and the database are faked."""
import io
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from openpyxl import load_workbook

from app.agents.daily_summary import ADDIS, DailySummaries, summary_text, yesterday
from app.agents.export import ExportError, build_export, month_bounds
from app.agents.messages import t
from app.agents.staff import next_steps
from app.agents.tools import order_number
from app.models.schemas import Store
from app.services.telegram_service import TELEGRAM_API, TelegramService
from tests import test_miniapp as mini
from tests.test_flow import CUSTOMER, STAFF_CHAT, STORE, World, placed_order
from tests.test_miniapp import MEMBER, OWNER, STORE_A, world  # noqa: F401  (fixture)
from tests.test_staff import STAFF_MEMBER, _button_answers, _press, _staff

TOKEN = "111111111:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
MORNING = datetime(2026, 10, 4, 9, 0, tzinfo=ADDIS)


@pytest.fixture
def anyio_backend():
    return "asyncio"


# --- 3. Order updates to customers (D74/D75) -----------------------------------------------

async def _paid(w: World, fulfillment="pickup"):
    """A placed order, paid with the staff group's button. Returns the order
    and the id of the staff note carrying its next steps."""
    order = await placed_order(w)
    w.db.orders[order.idempotency_key] = order.model_copy(update={"fulfillment_method": fulfillment})
    await w.send_photo()
    await _staff(w, _press(f"pay:{order.id}", w.telegram.last_message_id(STAFF_CHAT)))
    return order, w.telegram.last_message_id(STAFF_CHAT)


def _status(w, order):
    return next(o for o in w.db.orders.values() if o.id == order.id).status


def _buttons_now(w):
    """The buttons the last editMessageReplyMarkup left."""
    body = next(b for m, b, _ in reversed(w.telegram.calls) if m == "editMessageReplyMarkup")
    return [(b["text"], b["callback_data"]) for row in body["reply_markup"]["inline_keyboard"] for b in row]


@pytest.mark.anyio
async def test_after_payment_staff_get_the_next_steps():
    w = World()
    order, note = await _paid(w, "pickup")
    number = order_number(order.id)
    assert w.telegram.buttons(note) == [(f"📦 Ready for pickup #{number}", f"ready:{order.id}"),
                                            (f"✅ Picked up #{number}", f"done:{order.id}")]
    assert "Confirmed by Sara" in w.telegram.to(STAFF_CHAT)[-1]

    w = World()
    order, note = await _paid(w, "delivery")
    number = order_number(order.id)
    assert w.telegram.buttons(note) == [(f"🚚 On the way #{number}", f"ship:{order.id}"),
                                            (f"✅ Delivered #{number}", f"done:{order.id}")]


@pytest.mark.anyio
async def test_delivery_on_the_way_then_delivered():
    w = World()
    order, note = await _paid(w, "delivery")
    number = order_number(order.id)

    await _staff(w, _press(f"ship:{order.id}", note))
    assert _status(w, order) == "out_for_delivery"
    assert w.telegram.to(CUSTOMER)[-1] == t("order_on_the_way", "en", number=number)
    assert _buttons_now(w) == [(f"✅ Delivered #{number}", f"done:{order.id}")]  # only what's left
    assert "is on the way" in w.telegram.to(STAFF_CHAT)[-1] and "(Sara)" in w.telegram.to(STAFF_CHAT)[-1]
    assert w.db.status_changes[-1] == (order.id, "out_for_delivery", STAFF_MEMBER, "Sara")  # who (D74)

    told = len(w.telegram.to(CUSTOMER))
    await _staff(w, _press(f"ship:{order.id}", note))  # tapped twice
    assert _button_answers(w)[-1] == (f"Order #{number} is already on the way.", True)
    assert len(w.telegram.to(CUSTOMER)) == told

    await _staff(w, _press(f"done:{order.id}", note))
    assert _status(w, order) == "delivered"
    assert w.telegram.to(CUSTOMER)[-1] == t("order_delivered", "en", number=number, shop=STORE.name)
    assert _buttons_now(w) == []

    await _staff(w, _press(f"ship:{order.id}", note))  # never backwards
    assert _status(w, order) == "delivered"
    assert _button_answers(w)[-1] == (f"Order #{number} was already delivered.", True)


@pytest.mark.anyio
async def test_pickup_ready_then_picked_up():
    w = World()
    order, note = await _paid(w, "pickup")
    number = order_number(order.id)

    await _staff(w, _press(f"ready:{order.id}", note))
    assert _status(w, order) == "confirmed"  # D75: a message only
    ready = w.telegram.to(CUSTOMER)[-1]
    assert ready.startswith(t("order_ready", "en", number=number))
    assert t("pickup_hours", "en", hours=STORE.opening_hours) in ready  # when to come, from the profile
    assert _buttons_now(w) == [(f"✅ Picked up #{number}", f"done:{order.id}")]

    await _staff(w, _press(f"done:{order.id}", note))
    assert _status(w, order) == "delivered"
    assert w.telegram.to(CUSTOMER)[-1] == t("order_picked_up", "en", number=number, shop=STORE.name)


@pytest.mark.anyio
async def test_steps_are_refused_for_unpaid_orders_and_the_wrong_kind():
    w = World()
    order = await placed_order(w)  # not paid
    await w.send_photo()
    alert = w.telegram.last_message_id(STAFF_CHAT)
    told = len(w.telegram.to(CUSTOMER))
    await _staff(w, _press(f"done:{order.id}", alert))
    answer, popup = _button_answers(w)[-1]
    assert popup and "isn't paid yet" in answer
    assert _status(w, order) == "pending" and len(w.telegram.to(CUSTOMER)) == told

    w = World()
    order, note = await _paid(w, "pickup")
    await _staff(w, _press(f"ship:{order.id}", note))  # "on the way" for a pickup order
    assert _button_answers(w)[-1] == ("This button doesn't fit this order.", True)
    assert _status(w, order) == "confirmed"


@pytest.mark.anyio
async def test_amharic_customer_hears_it_in_amharic():
    w = World(language="am")  # the customer chose Amharic (D29)
    await w.say("ኤር ፎርስ")
    for label in ("White", "42", "1", t("btn_continue", "am"), t("btn_pickup", "am")):
        await w.tap(label)
    await w.say("አበበ")
    await w.say("0911223344")
    await w.tap(t("btn_confirm", "am"))
    [order] = w.db.orders.values()
    await w.send_photo()
    await _staff(w, _press(f"pay:{order.id}", w.telegram.last_message_id(STAFF_CHAT)))
    await _staff(w, _press(f"ready:{order.id}", w.telegram.last_message_id(STAFF_CHAT)))
    assert w.telegram.to(CUSTOMER)[-1].startswith(t("order_ready", "am", number=order_number(order.id)))
    await _staff(w, _press(f"done:{order.id}", w.telegram.last_message_id(STAFF_CHAT)))
    assert w.telegram.to(CUSTOMER)[-1] == t("order_picked_up", "am", number=order_number(order.id),
                                                shop=STORE.name)


@pytest.mark.anyio
async def test_another_stores_order_cant_be_moved():
    w = World()
    order, note = await _paid(w, "delivery")
    other = STORE.model_copy(update={"id": uuid4()})
    result = await w.orchestrator.staff.advance_order(other, order.id, "ship")
    assert not result.ok and result.message == "Order not found."
    assert _status(w, order) == "confirmed"


def test_next_step_buttons():
    order_id = uuid4()
    assert [d for _, d in next_steps(order_id, "delivery")] == [f"ship:{order_id}", f"done:{order_id}"]
    assert [d for _, d in next_steps(order_id, None)] == [f"ship:{order_id}", f"done:{order_id}"]  # older orders
    assert next_steps(order_id, "pickup", done="done") == []
    assert all(len(d) <= 64 for _, d in next_steps(order_id, "pickup"))  # Telegram's limit


# --- 1. The morning summary (D70/D71) --------------------------------------------------------

RAW = {"revenue": 38500, "payments": 11, "orders_placed": 12, "telegram_orders": 9, "in_shop_sales": 3,
       "unpaid_orders": 2, "discount_total": 300,
       "top_products": [{"name": "Nike Air", "quantity": 5, "revenue": 25000}]}
LOW = [{"product_name": "Nike Air", "color": "Black", "size": "42", "stock": 1},
       {"product_name": "Leather bag", "color": "Brown", "size": None, "stock": 0}]


def summary_shop(**fields) -> Store:
    return Store(**{"id": uuid4(), "name": "Selam Shoes", "telegram_bot_token": TOKEN, "webhook_secret": "s",
                    "owner_telegram_id": 42, "staff_chat_id": -500, **fields})


def test_summary_says_how_yesterday_went():
    start, _ = yesterday(MORNING)
    text = summary_text(summary_shop(), RAW, LOW, start, "en")
    assert text.startswith("☀️ Selam Shoes · yesterday, Oct 3")
    for line in ("🛒 12 orders: 9 on Telegram, 3 in the shop", "💰 38,500 ETB received (11 payments)",
                 "⏳ 2 orders not paid yet", "🏷 Discounts in the shop: 300 ETB", "🏆 Best seller: Nike Air (5)",
                 "• Nike Air · Black · 42: 1 left", "• Leather bag · Brown: sold out"):
        assert line in text
    amharic = summary_text(summary_shop(), RAW, LOW, start, "am")
    assert "ትናንት" in amharic and "38,500 ብር" in amharic


def test_a_quiet_day_still_gets_a_summary():
    start, _ = yesterday(MORNING)
    text = summary_text(summary_shop(), {}, [], start, "en")
    assert "🛒 No orders yesterday." in text and "✅ Nothing is low on stock." in text
    assert "received" not in text and "not paid" not in text
    many = [{"product_name": f"P{i}", "stock": 1} for i in range(8)]
    assert "…and 3 more" in summary_text(summary_shop(), {}, many, start, "en")


def test_yesterday_is_an_addis_ababa_day():
    # 22:30 UTC on Oct 3 is already Oct 4 in Addis: yesterday is Oct 3.
    start, end = yesterday(datetime(2026, 10, 3, 22, 30, tzinfo=timezone.utc))
    assert (start.isoformat(), end - start) == ("2026-10-03T00:00:00+03:00", timedelta(days=1))


class SummaryDb:
    def __init__(self, stores):
        self.stores = stores
        self.sent_days = set()
        self.analytics = []

    async def stores_for_summary(self):
        return self.stores

    async def add_daily_summary(self, store_id, day):
        if (store_id, day) in self.sent_days:
            return False
        self.sent_days.add((store_id, day))
        return True

    async def store_analytics(self, store_id, start, end):
        self.analytics.append((store_id, start, end))
        return RAW

    async def low_stock(self, store_id, at_most=2, limit=50):
        return LOW


def summaries(db, refuse=()):
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["chat_id"] in refuse:
            return httpx.Response(403, json={"ok": False, "description": "Forbidden: bot was blocked by the user"})
        sent.append((body["chat_id"], body["text"]))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    telegram = TelegramService(httpx.AsyncClient(base_url=TELEGRAM_API, transport=httpx.MockTransport(handler)))
    return DailySummaries(db, telegram), sent


@pytest.mark.anyio
async def test_each_owner_gets_it_once_a_day_in_their_language():
    english, amharic = summary_shop(daily_summary="en"), summary_shop(owner_telegram_id=43)
    db = SummaryDb([english, amharic])
    daily, sent = summaries(db)
    assert await daily.send_all(MORNING) == 2
    assert {chat for chat, _ in sent} == {42, 43}  # the owners' private chats only (D70)
    assert "yesterday" in dict(sent)[42] and "ትናንት" in dict(sent)[43]
    start, end = yesterday(MORNING)
    assert db.analytics[0] == (english.id, start, end)
    sent.clear()
    assert await daily.send_all(MORNING + timedelta(hours=3)) == 0 and sent == []  # once a day
    assert await daily.send_all(MORNING + timedelta(days=1)) == 2  # the next day, again


@pytest.mark.anyio
async def test_not_before_eight_and_a_blocked_bot_is_not_counted():
    db = SummaryDb([summary_shop()])
    daily, sent = summaries(db, refuse={42})
    assert await daily.maybe_send(datetime(2026, 10, 4, 7, 30, tzinfo=ADDIS)) == 0 and db.analytics == []
    assert await daily.maybe_send(MORNING) == 0  # tried, but the owner never pressed Start
    assert sent == [] and len(db.sent_days) == 1  # not retried every 15 minutes


def test_the_owner_turns_it_off_or_changes_the_language(world):
    db, _, client, _, _ = world
    response = client.put(mini.url("/settings"), headers=mini.headers(OWNER), json={"daily_summary": "off"})
    assert response.status_code == 200 and response.json()["daily_summary"] == "off"
    assert db.stores[STORE_A.id].daily_summary == "off"
    assert client.put(mini.url("/settings"), headers=mini.headers(OWNER),
                      json={"daily_summary": "fr"}).status_code == 422
    assert client.put(mini.url("/settings"), headers=mini.headers(MEMBER),
                      json={"daily_summary": "en"}).status_code == 403  # owners only


# --- 2. The export for the accountant (D72/D73) -----------------------------------------------

def test_month_bounds_are_addis_months():
    start, end = month_bounds("2026-09", MORNING)
    assert (start.isoformat(), end.isoformat()) == ("2026-09-01T00:00:00+03:00", "2026-10-01T00:00:00+03:00")
    start, end = month_bounds("2025-12", MORNING)
    assert end.isoformat() == "2026-01-01T00:00:00+03:00"
    assert month_bounds("2026-10", MORNING)[0].day == 1  # this month so far
    for bad in ("2026-13", "2026-9", "", "1999-01", "2026-11"):  # the last: not started yet
        with pytest.raises(ExportError):
            month_bounds(bad, MORNING)


def _row(status="confirmed", paid=True, channel="telegram", items=(), payments=None, **fields):
    return {"id": str(uuid4()), "status": status, "payment_status": "paid" if paid else "unpaid",
            "total_price": sum(i["price"] * i["quantity"] for i in items), "currency": "ETB",
            "fulfillment_method": "pickup", "contact_name": "Abebe", "contact_phone": "0911",
            "created_at": "2026-09-14T07:30:00+00:00", "channel": channel, "payment_method": None,
            "sold_by_name": None, "payments": payments if payments is not None else [], **fields,
            "order_items": [dict(i) for i in items]}


def _item(name, price, quantity=1, list_price=None, color="Black", size="42"):
    return {"quantity": quantity, "price": price, "list_price": list_price,
            "product_variants": {"color": color, "size": size, "products": {"name": name, "code": "P101"}}}


EXPORT_ROWS = [
    _row(items=[_item("Nike Air", 5000, 2)],
         payments=[{"amount": 10000, "method": "Telebirr", "paid_at": "x", "confirmed_by_name": "Sara"}]),
    _row(channel="in_shop", items=[_item("Leather bag", 900, 1, list_price=1000, color="Brown", size=None)],
         payment_method="Cash", sold_by_name="Hana",
         payments=[{"amount": 900, "method": "Cash", "paid_at": "x", "confirmed_by_name": "Hana"}]),
    _row(status="pending", paid=False, items=[_item("Nike Air", 5000)]),
    _row(status="cancelled", paid=False, items=[_item("Nike Air", 5000)]),
]


def _book(export):
    return load_workbook(io.BytesIO(export.data))


def test_the_workbook_has_summary_sales_and_orders():
    start, _ = month_bounds("2026-09", MORNING)
    export = build_export(STORE_A, EXPORT_ROWS, start)
    assert export.filename == "Selam-Shoes-2026-09.xlsx" and export.revenue == Decimal("10900")
    book = _book(export)
    assert book.sheetnames == ["Summary", "Sales", "Orders"]

    summary = {row[0]: row[1:] for row in book["Summary"].iter_rows(values_only=True) if row[0]}
    assert summary["Selam Shoes"][0] == "September 2026"
    assert summary["Money received (paid orders and sales)"][0] == 10900
    assert summary["Paid orders and sales"][0] == 2
    assert summary["Discounts given in the shop"][0] == 100
    assert summary["Orders not paid"][0] == 1 and summary["Cancelled orders"][0] == 1
    assert summary["Telebirr"][:2] == (1, 10000) and summary["Cash"][:2] == (1, 900)

    sales = list(book["Sales"].iter_rows(values_only=True))
    assert sales[0][:8] == ("Date", "Order", "Where", "Product", "Code", "Color", "Size", "Quantity")
    assert len(sales) == 3  # only paid, not cancelled
    nike, bag = sales[1], sales[2]
    assert nike[0] == datetime(2026, 9, 14, 10, 30)  # Addis time
    assert nike[3:] == ("Nike Air", "P101", "Black", "42", 2, 5000, 5000, 0, 10000, "Telebirr", "Sara")
    assert bag[2] == "In the shop" and bag[8:] == (1000, 900, 100, 900, "Cash", "Hana")

    orders = list(book["Orders"].iter_rows(values_only=True))
    assert len(orders) == 5  # every order, with its stage
    assert [o[6] for o in orders[1:]] == ["Paid", "Paid", "Waiting for payment", "Cancelled"]


def test_the_columns_use_the_shops_own_words():
    phones = STORE_A.model_copy(update={"shop_type": "electronics"})
    start, _ = month_bounds("2026-09", MORNING)
    sales = next(_book(build_export(phones, [], start))["Sales"].iter_rows(values_only=True))
    assert sales[5:7] == ("Color", "Storage")


def test_the_owner_gets_the_file_from_the_bot(world):
    db, telegram, client, _, _ = world
    asked = []

    async def orders_for_export(store_id, start, end):
        asked.append((store_id, start, end))
        return EXPORT_ROWS

    db.orders_for_export = orders_for_export
    response = client.post(mini.url("/orders/export"), headers=mini.headers(OWNER), json={"month": "2026-09"})
    assert response.status_code == 200, response.text
    assert response.json()["file"] == "Selam-Shoes-2026-09.xlsx" and response.json()["orders"] == 4
    assert [store_id for store_id, _, _ in asked] == [STORE_A.id]  # only this shop's orders
    token, form = telegram.documents[-1]
    assert token == mini.TOKEN_A  # the shop's own bot...
    assert b'name="chat_id"\r\n\r\n1\r\n' in form  # ...to the owner who asked, in private
    assert b"Selam-Shoes-2026-09.xlsx" in form


def test_export_is_for_owners_and_needs_the_bot_started(world):
    db, telegram, client, _, _ = world

    async def orders_for_export(store_id, start, end):
        return []

    db.orders_for_export = orders_for_export
    path = mini.url("/orders/export")
    assert client.post(path, headers=mini.headers(MEMBER), json={"month": "2026-09"}).status_code == 403
    assert client.post(path, headers=mini.headers(OWNER), json={"month": "2026-9"}).status_code == 422
    assert client.post(path, headers=mini.headers(OWNER), json={"month": "2099-01"}).status_code == 422
    telegram.refuse_documents = True
    response = client.post(path, headers=mini.headers(OWNER), json={"month": "2026-09"})
    assert response.status_code == 409 and "@selam_bot" in response.json()["detail"]
    # Another store's owner can't export this store.
    assert client.post(path, headers=mini.headers(mini.STRANGER, mini.TOKEN_B),
                       json={"month": "2026-09"}).status_code in (401, 403)
