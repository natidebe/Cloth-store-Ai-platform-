"""Subscriptions (Phase 14, decisions D64–D69): plans, reminders, pausing.

    free (trial)  1 week from approval   100 AI replies a day
    basic         4,500 ETB / 3 months   100 AI replies a day
    pro           9,000 ETB / 3 months   300 AI replies a day

Every shop has an end date (stores.plan_ends_at). Once a day (in the
morning, Addis time) the minute sweep checks each shop and sends what's due,
each reminder once per end date (subscription_notices):

    7, 3, 1 days before (trial: 3, 1)   "ends in N days"
    the day it ends                     "3 days of grace, then the bot pauses"
    grace days 1, 2                     "the bot pauses in N days"
    grace day 3                         paused: status 'suspended', reason 'unpaid' (D67)

The shop hears it in its staff group and the owner's private chat (Amharic
and English); the platform admins get one list a day in the platform bot.
Recording a payment (the platform admin) adds 3 months from the current end
date and turns an unpaid pause back on (record_subscription_payment, 014).
"""
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal

from app.models.schemas import Store
from app.services.supabase_service import DatabaseError, SupabaseService
from app.services.telegram_service import TelegramError, TelegramService

logger = logging.getLogger(__name__)

ADDIS = timezone(timedelta(hours=3), "EAT")
TRIAL_DAYS = 7
GRACE_DAYS = 3
CHECK_FROM_HOUR = 8  # reminders go out from 08:00 Addis time
CHECK_EVERY_SECONDS = 15 * 60  # the sweep runs every minute; the check at most this often

PaidPlan = Literal["basic", "pro"]


@dataclass(frozen=True)
class Plan:
    name_en: str
    name_am: str
    price: Decimal  # ETB per period (0: the trial)
    months: int
    ai_per_day: int  # D68
    remind_days: tuple[int, ...]  # days before the end (D66)


PLANS: dict[str, Plan] = {
    "free": Plan("Free trial", "ነፃ ሙከራ", Decimal("0"), 0, 100, (3, 1)),
    "basic": Plan("Basic", "መሰረታዊ", Decimal("4500"), 3, 100, (7, 3, 1)),
    "pro": Plan("Pro", "ፕሮ", Decimal("9000"), 3, 300, (7, 3, 1)),
}


def plan_of(store: Store) -> Plan:
    return PLANS.get(store.plan or "free", PLANS["free"])


def ai_limit(store: Store, default: int | None) -> int | None:
    """AI replies a day for this shop's plan (D68). No plan known: `default`."""
    plan = PLANS.get(store.plan or "")
    return plan.ai_per_day if plan is not None else default


def trial_end(now: datetime | None = None) -> datetime:
    """D64/D69: the trial lasts a week from approval."""
    return (now or datetime.now(timezone.utc)) + timedelta(days=TRIAL_DAYS)


def days_left(ends_at: datetime, now: datetime | None = None) -> int:
    """Whole days from today to the end date, in Addis Ababa (0: ends today,
    negative: ended that many days ago)."""
    today = (now or datetime.now(timezone.utc)).astimezone(ADDIS).date()
    return (ends_at.astimezone(ADDIS).date() - today).days


def due_notice(store: Store, now: datetime | None = None) -> str | None:
    """What this shop should hear today, or None."""
    if store.plan_ends_at is None or store.status != "active":
        return None
    days = days_left(store.plan_ends_at, now)
    if days > 0:
        return f"before_{days}" if days in plan_of(store).remind_days else None
    if days == 0:
        return "ended"
    if days > -GRACE_DAYS:
        return f"grace_{-days}"
    return "paused"


# --- The messages --------------------------------------------------------------------------

def _day(value: date) -> str:
    return value.strftime("%b %d").replace(" 0", " ")  # "Oct 9"


def _how_to_pay(language: str, payment_info: str, support: str) -> str:
    if language == "am":
        prices = "ለመቀጠል፦ መሰረታዊ 4,500 ብር ወይም ፕሮ 9,000 ብር ለ3 ወር።"
        if payment_info:
            return f"{prices} በ{payment_info} ከፍለው ደረሰኙን ለ@{support} ይላኩ።" if support else \
                f"{prices} በ{payment_info} ይክፈሉ።"
        return f"{prices} ለመክፈል @{support}ን ያግኙ።" if support else prices
    prices = "To continue: Basic 4,500 ETB or Pro 9,000 ETB for 3 months."
    if payment_info:
        return f"{prices} Pay with {payment_info} and send the receipt to @{support}." if support else \
            f"{prices} Pay with {payment_info}."
    return f"{prices} Contact @{support} to pay." if support else prices


def shop_message(store: Store, kind: str, ends_at: datetime, payment_info: str, support: str) -> str:
    """The reminder for the shop (staff group and owner), Amharic then English."""
    plan = plan_of(store)
    end = ends_at.astimezone(ADDIS).date()
    pause = end + timedelta(days=GRACE_DAYS)
    name = store.name
    if kind.startswith("before_"):
        days = int(kind.removeprefix("before_"))
        am = f"⏰ {name}፦ የ{plan.name_am} እቅድዎ በ{days} ቀን ውስጥ ({_day(end)}) ያበቃል።"
        en = (f"⏰ {name}: your {plan.name_en} plan ends in {days} day{'s' if days != 1 else ''}, "
              f"on {_day(end)}.")
    elif kind == "ended":
        am = (f"⏰ {name}፦ የ{plan.name_am} እቅድዎ ዛሬ ያበቃል። ቦቱ ለ{GRACE_DAYS} ቀን መስራቱን ይቀጥላል፤ "
              f"ከዚያ ({_day(pause)}) እስኪከፍሉ ድረስ ይቆማል።")
        en = (f"⏰ {name}: your {plan.name_en} plan ends today. The bot keeps working for "
              f"{GRACE_DAYS} more days, then pauses on {_day(pause)} until you pay.")
    elif kind.startswith("grace_"):
        left = GRACE_DAYS - int(kind.removeprefix("grace_"))
        am = f"⚠️ {name}፦ እቅድዎ አብቅቷል። ቦቱ በ{left} ቀን ውስጥ ({_day(pause)}) ይቆማል።"
        en = (f"⚠️ {name}: your plan has ended. The bot pauses in {left} day{'s' if left != 1 else ''}, "
              f"on {_day(pause)}.")
    else:  # paused
        am = (f"⛔ {name} ቆሟል፦ እቅዱ አብቅቶ አልታደሰም። ደንበኞች ሱቁ ትዕዛዝ እንደማይቀበል ይነገራቸዋል፤ "
              "ምርቶችዎና ትዕዛዞችዎ ይቀመጣሉ። ሲከፍሉ ወዲያውኑ ይመለሳል።")
        en = (f"⛔ {name} is paused: the plan ended and wasn't renewed. Customers are told the shop "
              "isn't taking orders; your products and orders are kept. It's back as soon as you pay.")
    return (f"{am}\n{_how_to_pay('am', payment_info, support)}\n\n"
            f"{en}\n{_how_to_pay('en', payment_info, support)}")


def admin_line(store: Store, kind: str, ends_at: datetime) -> str:
    """One line of the platform admins' daily list (English)."""
    plan = plan_of(store).name_en
    end = ends_at.astimezone(ADDIS).date()
    if kind.startswith("before_"):
        days = int(kind.removeprefix("before_"))
        what = f"ends in {days} day{'s' if days != 1 else ''} ({_day(end)})"
    elif kind == "ended":
        what = f"ends today; grace until {_day(end + timedelta(days=GRACE_DAYS))}"
    elif kind.startswith("grace_"):
        what = f"ended {_day(end)}; pauses {_day(end + timedelta(days=GRACE_DAYS))}"
    else:
        what = "⛔ paused (didn't pay)"
    return f"• {store.name} ({plan}): {what}"


def thanks_message(store: Store, plan: str, ends_at: datetime, resumed: bool) -> str:
    """After a payment, for the shop."""
    info = PLANS[plan]
    end = _day(ends_at.astimezone(ADDIS).date())
    back_am = " ቦቱ እንደገና ትዕዛዝ ይቀበላል።" if resumed else ""
    back_en = " The bot takes orders again." if resumed else ""
    return (f"✅ {store.name}፦ እናመሰግናለን! {info.name_am} እስከ {end}።{back_am}\n"
            f"✅ {store.name}: thank you! {info.name_en} until {end}.{back_en}")


# --- The daily check and payments ------------------------------------------------------------

class Subscriptions:
    def __init__(self, db: SupabaseService, telegram: TelegramService, *, platform_token: str,
                 payment_info: str = "", support: str = "", clock=time.monotonic):
        self.db = db
        self.telegram = telegram
        self.platform_token = platform_token
        self.payment_info = payment_info.strip()
        self.support = support.strip().lstrip("@")
        self.clock = clock
        self._next_check = 0.0

    async def maybe_check(self, now: datetime | None = None) -> int:
        """From the minute sweep: the daily check, in the morning, at most every
        15 minutes (each reminder is sent once anyway)."""
        now = now or datetime.now(timezone.utc)
        if now.astimezone(ADDIS).hour < CHECK_FROM_HOUR or self.clock() < self._next_check:
            return 0
        self._next_check = self.clock() + CHECK_EVERY_SECONDS
        return await self.check(now)

    async def check(self, now: datetime | None = None) -> int:
        """Send what's due today, pause shops past their grace. Returns how many
        reminders went out."""
        now = now or datetime.now(timezone.utc)
        lines: list[str] = []
        for store in await self.db.stores_with_plan_end():
            kind = due_notice(store, now)
            if kind is None:
                continue
            try:
                if not await self.db.add_subscription_notice(store.id, store.plan_ends_at, kind):
                    continue  # already sent for this end date
                if kind == "paused":
                    await self.db.set_store_status(store.id, "suspended", reason="unpaid")
                    logger.info("store paused: not paid", extra={"store_id": str(store.id)})
                await self.tell_shop(store, shop_message(store, kind, store.plan_ends_at,
                                                         self.payment_info, self.support))
                lines.append(admin_line(store, kind, store.plan_ends_at))
            except DatabaseError:
                logger.exception("subscription check failed for a store", extra={"store_id": str(store.id)})
        if lines:
            await self.tell_admins("📋 Subscriptions today\n" + "\n".join(lines))
        return len(lines)

    async def record_payment(self, store: Store, plan: PaidPlan, amount: Decimal, method: str | None,
                             reference: str | None, recorded_by: int | None, months: int = 3) -> dict:
        """D65: extend the period by `months` from its current end (or today),
        turn an unpaid pause back on, and thank the shop."""
        result = await self.db.record_subscription_payment(store.id, plan, months, amount, method,
                                                           reference, recorded_by)
        ends_at = datetime.fromisoformat(str(result["period_end"]))
        await self.tell_shop(store, thanks_message(store, plan, ends_at, bool(result.get("resumed"))))
        return result

    async def tell_shop(self, store: Store, text: str) -> None:
        """The staff group and the owner's private chat; one failing doesn't stop the other."""
        if store.telegram_bot_token is None:
            return
        token = store.telegram_bot_token.get_secret_value()
        for chat_id in (store.staff_chat_id, store.owner_telegram_id):
            if chat_id is None:
                continue
            try:
                await self.telegram.send_message(token, chat_id, text)
            except TelegramError as error:
                logger.info("subscription message not delivered", extra={"error": error.description})

    async def tell_admins(self, text: str) -> None:
        if not self.platform_token:
            return
        for telegram_id in await self.db.platform_admin_ids():
            try:
                await self.telegram.send_message(self.platform_token, telegram_id, text)
            except TelegramError as error:
                logger.info("admin not told", extra={"error": error.description})
