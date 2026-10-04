"""Phase 14, subscriptions (D64–D69): what's due when, the daily check
(reminders to the shop and the platform admins, each once, pausing after
the grace), AI replies by plan, recording a payment, approval starts the trial."""
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest

from app.agents.subscriptions import (
    ADDIS,
    Subscriptions,
    ai_limit,
    days_left,
    due_notice,
    shop_message,
)
from app.models.schemas import Store
from app.services.telegram_service import TELEGRAM_API, TelegramService
from tests import test_miniapp as mini
from tests.test_miniapp import OWNER, STORE_A, world  # noqa: F401  (world is a pytest fixture)

TOKEN = "111111111:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
PLATFORM = "999999999:PPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPP"
GROUP, OWNER_CHAT, ADMIN_CHAT = -500, 42, 7
# 09:00 in Addis Ababa on Oct 4, 2026.
MORNING = datetime(2026, 10, 4, 9, 0, tzinfo=ADDIS)


def at(days: int) -> datetime:
    """An end date `days` from MORNING's day (late afternoon, Addis)."""
    return datetime(2026, 10, 4, 17, 0, tzinfo=ADDIS) + timedelta(days=days)


def shop(plan="basic", ends_in=3, status="active", name="Selam Shoes") -> Store:
    return Store(id=uuid4(), name=name, plan=plan, status=status, plan_ends_at=at(ends_in),
                 telegram_bot_token=TOKEN, webhook_secret="s", staff_chat_id=GROUP,
                 owner_telegram_id=OWNER_CHAT)


# --- What's due -----------------------------------------------------------------------------

@pytest.mark.parametrize("plan, days, kind", [
    ("basic", 7, "before_7"), ("basic", 5, None), ("basic", 3, "before_3"), ("pro", 1, "before_1"),
    ("free", 7, None), ("free", 3, "before_3"),  # the 1-week trial: 3 and 1 day before
    ("basic", 0, "ended"), ("basic", -1, "grace_1"), ("basic", -2, "grace_2"),
    ("basic", -3, "paused"), ("basic", -9, "paused"),
])
def test_what_is_due_each_day(plan, days, kind):
    assert due_notice(shop(plan, days), MORNING) == kind


def test_nothing_is_due_for_a_paused_pending_or_unstarted_shop():
    assert due_notice(shop(ends_in=1, status="suspended"), MORNING) is None
    unstarted = shop().model_copy(update={"plan_ends_at": None})
    assert due_notice(unstarted, MORNING) is None


def test_days_are_counted_in_addis_ababa():
    # 23:30 UTC on Oct 3 is already Oct 4 in Addis: an end on Oct 4 is "today".
    late = datetime(2026, 10, 3, 23, 30, tzinfo=timezone.utc)
    assert days_left(at(0), late) == 0


def test_ai_replies_a_day_by_plan():
    assert [ai_limit(shop(p), 999) for p in ("free", "basic", "pro")] == [100, 100, 300]
    # No plan or an unknown one (never in the database: plan is required there): the default.
    assert ai_limit(Store(id=uuid4(), name="x"), 250) == 250
    assert ai_limit(Store(id=uuid4(), name="x", plan="gold"), 250) == 250


def test_the_shop_is_told_how_to_pay():
    text = shop_message(shop(), "before_3", at(3), "Telebirr 0960570692", "kiyay30")
    assert "ends in 3 days, on Oct 7" in text and "Basic 4,500 ETB or Pro 9,000 ETB" in text
    assert "Pay with Telebirr 0960570692 and send the receipt to @kiyay30" in text
    assert "በ3 ቀን ውስጥ" in text  # Amharic first


# --- The daily check ------------------------------------------------------------------------

class FakeDb:
    def __init__(self, stores):
        self.stores = {s.id: s for s in stores}
        self.notices = set()
        self.statuses = []

    async def stores_with_plan_end(self):
        return list(self.stores.values())

    async def add_subscription_notice(self, store_id, ends_at, kind):
        key = (store_id, ends_at, kind)
        if key in self.notices:
            return False
        self.notices.add(key)
        return True

    async def set_store_status(self, store_id, status, reason=None):
        self.statuses.append((store_id, status, reason))
        self.stores[store_id] = self.stores[store_id].model_copy(update={"status": status,
                                                                          "suspended_reason": reason})

    async def platform_admin_ids(self):
        return [ADMIN_CHAT]


def checker(db):
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        sent.append((request.url.path.split("/")[1].removeprefix("bot"), body["chat_id"], body["text"]))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    telegram = TelegramService(httpx.AsyncClient(base_url=TELEGRAM_API, transport=httpx.MockTransport(handler)))
    return Subscriptions(db, telegram, platform_token=PLATFORM, payment_info="Telebirr 0960570692",
                         support="kiyay30"), sent


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_reminders_go_to_the_shop_and_the_admins_once():
    ending, later = shop(ends_in=3), shop(ends_in=20, name="Far away")
    db = FakeDb([ending, later])
    subs, sent = checker(db)
    assert await subs.check(MORNING) == 1
    to = {(token, chat) for token, chat, _ in sent}
    assert to == {(TOKEN, GROUP), (TOKEN, OWNER_CHAT), (PLATFORM, ADMIN_CHAT)}
    admin_text = next(text for token, _, text in sent if token == PLATFORM)
    assert "Selam Shoes (Basic): ends in 3 days (Oct 7)" in admin_text and "Far away" not in admin_text
    sent.clear()
    assert await subs.check(MORNING + timedelta(hours=2)) == 0 and sent == []  # once


@pytest.mark.anyio
async def test_after_the_grace_the_shop_is_paused():
    late = shop(ends_in=-3)
    db = FakeDb([late])
    subs, sent = checker(db)
    await subs.check(MORNING)
    assert db.statuses == [(late.id, "suspended", "unpaid")]
    assert any("is paused" in text for _, chat, text in sent if chat == GROUP)
    assert any("⛔ paused (didn't pay)" in text for _, chat, text in sent if chat == ADMIN_CHAT)


@pytest.mark.anyio
async def test_nothing_before_eight_in_the_morning():
    db = FakeDb([shop(ends_in=3)])
    subs, sent = checker(db)
    assert await subs.maybe_check(datetime(2026, 10, 4, 6, 30, tzinfo=ADDIS)) == 0 and sent == []
    assert await subs.maybe_check(MORNING) == 1
    assert await subs.maybe_check(MORNING + timedelta(minutes=1)) == 0  # not again within 15 minutes


# --- Payments and approval (the platform admin's Mini App) --------------------------------------

def _payments(db):
    recorded = []

    async def record_subscription_payment(store_id, plan, months, amount, method, reference, by):
        recorded.append((store_id, plan, months, amount, method, reference, by))
        return {"payment_id": str(uuid4()), "plan": plan, "period_start": "2026-10-11T09:00:00+00:00",
                "period_end": "2027-01-11T09:00:00+00:00", "resumed": True}

    async def platform_admin_ids():
        return [OWNER]

    db.record_subscription_payment = record_subscription_payment
    db.platform_admin_ids = platform_admin_ids
    return recorded


def test_the_admin_records_a_payment_and_the_shop_is_thanked(world):
    db, telegram, client, _, _ = world
    recorded = _payments(db)
    response = client.post(f"/api/v1/platform-app/admin/stores/{STORE_A.id}/payments", headers=mini.platform(OWNER),
                           json={"plan": "basic", "amount": 4500, "method": "Telebirr", "reference": "TX123"})
    assert response.status_code == 201 and response.json()["period_end"].startswith("2027-01-11")
    assert recorded == [(STORE_A.id, "basic", 3, Decimal("4500"), "Telebirr", "TX123", OWNER)]
    thanks = telegram.sent(mini.GROUP_A)[-1]["text"]
    assert "thank you! Basic until Jan 11. The bot takes orders again." in thanks


def test_only_platform_admins_record_payments_and_not_for_pending_shops(world):
    db, _, client, _, _ = world
    _payments(db)
    path = f"/api/v1/platform-app/admin/stores/{STORE_A.id}/payments"
    assert client.post(path, headers=mini.platform(mini.MEMBER), json={"plan": "pro", "amount": 9000}).status_code == 403
    assert client.post(path, headers=mini.platform(OWNER), json={"plan": "gold", "amount": 1}).status_code == 422
    db.stores[STORE_A.id] = db.stores[STORE_A.id].model_copy(update={"status": "pending"})
    assert client.post(path, headers=mini.platform(OWNER), json={"plan": "pro", "amount": 9000}).status_code == 409


def test_approval_starts_the_one_week_trial(world):
    db, _, client, _, _ = world
    pending = mini.STORE_B.model_copy(update={"status": "pending", "plan_ends_at": None})
    db.stores[pending.id] = pending
    client.post(f"/api/v1/platform-app/admin/stores/{pending.id}/approve", headers=mini.platform(OWNER))
    ends = db.stores[pending.id].plan_ends_at
    assert ends is not None and timedelta(days=6, hours=23) < ends - datetime.now(timezone.utc) <= timedelta(days=7)
    # Suspended by the admin: marked so a payment never undoes it.
    client.post(f"/api/v1/platform-app/admin/stores/{pending.id}/suspend", headers=mini.platform(OWNER))
    assert db.stores[pending.id].suspended_reason == "admin"


def test_me_tells_the_shop_its_plan_end(world):
    db, _, client, _, _ = world
    db.stores[STORE_A.id] = db.stores[STORE_A.id].model_copy(update={"plan": "pro", "plan_ends_at": at(23)})
    shop_info = client.get(mini.url("/me"), headers=mini.headers(OWNER)).json()["store"]
    assert shop_info["plan"] == "pro" and shop_info["plan_ends_at"].startswith("2026-10-27")
