"""The owner's morning summary (Phase 15, D70/D71): how yesterday went.

Every morning (from 08:00 Addis time, with the Phase 14 daily check) the
owner of each active shop gets, in their private chat with the shop's bot:

    ☀️ Selam Shoes · yesterday, Oct 3
    🛒 12 orders: 9 on Telegram, 3 in the shop
    💰 38,500 ETB received (11 payments)
    ⏳ 2 orders not paid yet
    🏆 Best seller: Nike Air (5)
    ⚠️ Low on stock:
    • Nike Air · Black · 42: 1 left

The numbers come from the same database function as the Mini App's
analytics (store_analytics), so they always match it. "Received" is the
money paid yesterday (D71); unpaid orders are their own line. The owner
chooses Amharic, English, or off in Settings (stores.daily_summary). Each
day's summary is sent once (daily_summaries, migration 015).
"""
import logging
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from app.agents.inventory import LOW_STOCK_AT
from app.agents.messages import Language, format_price
from app.models.schemas import Store
from app.services.supabase_service import DatabaseError, SupabaseService
from app.services.telegram_service import TelegramError, TelegramService

logger = logging.getLogger(__name__)

ADDIS = timezone(timedelta(hours=3), "EAT")
SEND_FROM_HOUR = 8  # 08:00 Addis time
CHECK_EVERY_SECONDS = 15 * 60  # the sweep runs every minute; this at most this often
MAX_LOW_STOCK_LINES = 5

TEXTS: dict[str, dict[Language, str]] = {
    "title": {"en": "☀️ {shop} · yesterday, {day}", "am": "☀️ {shop} · ትናንት፣ {day}"},
    "no_orders": {"en": "🛒 No orders yesterday.", "am": "🛒 ትናንት ምንም ትዕዛዝ አልነበረም።"},
    "orders": {"en": "🛒 {count} orders: {telegram} on Telegram, {shop} in the shop",
               "am": "🛒 {count} ትዕዛዞች፦ {telegram} በቴሌግራም፣ {shop} በሱቅ"},
    "received": {"en": "💰 {amount} received ({count} payments)", "am": "💰 {amount} ተከፍሏል ({count} ክፍያዎች)"},
    "unpaid": {"en": "⏳ {count} orders not paid yet", "am": "⏳ {count} ትዕዛዞች ገና አልተከፈሉም"},
    "discounts": {"en": "🏷 Discounts in the shop: {amount}", "am": "🏷 በሱቅ የተሰጠ ቅናሽ፦ {amount}"},
    "best": {"en": "🏆 Best seller: {name} ({count})", "am": "🏆 በብዛት የተሸጠው፦ {name} ({count})"},
    "low_stock": {"en": "⚠️ Low on stock:", "am": "⚠️ ዕቃው ሊያልቅ ነው፦"},
    "left": {"en": "{count} left", "am": "{count} ቀርቷል"},
    "sold_out": {"en": "sold out", "am": "አልቋል"},
    "more": {"en": "…and {count} more (see the Mini App)", "am": "…እና ሌሎች {count} (ሚኒ አፑን ይመልከቱ)"},
    "all_stocked": {"en": "✅ Nothing is low on stock.", "am": "✅ ሊያልቅ የደረሰ ዕቃ የለም።"},
    "off": {"en": "(Turn this summary off or change its language in the Mini App: Settings.)",
            "am": "(ይህን ማጠቃለያ ለማጥፋት ወይም ቋንቋውን ለመቀየር፦ ሚኒ አፕ → Settings።)"},
}


def _t(key: str, language: Language, **values: object) -> str:
    return TEXTS[key][language].format(**values)


def yesterday(now: datetime | None = None) -> tuple[datetime, datetime]:
    """[start, end) of yesterday in Addis Ababa."""
    local = (now or datetime.now(timezone.utc)).astimezone(ADDIS)
    today = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return today - timedelta(days=1), today


def _day(value: datetime) -> str:
    return value.strftime("%b %d").replace(" 0", " ")  # "Oct 3"


def _variant(item: dict[str, Any]) -> str:
    return " · ".join(str(p) for p in (item.get("product_name"), item.get("color"), item.get("size")) if p)


def summary_text(store: Store, raw: dict[str, Any], low_stock: list[dict[str, Any]],
                 day: datetime, language: Language) -> str:
    """The message, from store_analytics' numbers for yesterday and today's low stock."""
    placed = int(raw.get("orders_placed") or 0)
    payments = int(raw.get("payments") or 0)
    unpaid = int(raw.get("unpaid_orders") or 0)
    discount = Decimal(str(raw.get("discount_total") or 0))
    lines = [_t("title", language, shop=store.name, day=_day(day))]
    if placed:
        lines.append(_t("orders", language, count=placed, telegram=int(raw.get("telegram_orders") or 0),
                        shop=int(raw.get("in_shop_sales") or 0)))
    else:
        lines.append(_t("no_orders", language))
    if payments:  # a payment for an order of an earlier day counts too
        lines.append(_t("received", language, amount=format_price(Decimal(str(raw.get("revenue") or 0)), language),
                        count=payments))
    if unpaid:
        lines.append(_t("unpaid", language, count=unpaid))
    if discount > 0:
        lines.append(_t("discounts", language, amount=format_price(discount, language)))
    top = raw.get("top_products") or []
    if top:
        lines.append(_t("best", language, name=top[0]["name"], count=int(top[0]["quantity"])))
    if low_stock:
        lines.append(_t("low_stock", language))
        for item in low_stock[:MAX_LOW_STOCK_LINES]:
            stock = int(item.get("stock") or 0)
            left = _t("left", language, count=stock) if stock else _t("sold_out", language)
            lines.append(f"• {_variant(item)}: {left}")
        if len(low_stock) > MAX_LOW_STOCK_LINES:
            lines.append(_t("more", language, count=len(low_stock) - MAX_LOW_STOCK_LINES))
    else:
        lines.append(_t("all_stocked", language))
    lines.append("")
    lines.append(_t("off", language))
    return "\n".join(lines)


class DailySummaries:
    def __init__(self, db: SupabaseService, telegram: TelegramService, clock=time.monotonic):
        self.db = db
        self.telegram = telegram
        self.clock = clock
        self._next_check = 0.0

    async def maybe_send(self, now: datetime | None = None) -> int:
        """From the minute sweep: from 08:00 Addis time, at most every 15
        minutes (each shop gets it once a day anyway)."""
        now = now or datetime.now(timezone.utc)
        if now.astimezone(ADDIS).hour < SEND_FROM_HOUR or self.clock() < self._next_check:
            return 0
        self._next_check = self.clock() + CHECK_EVERY_SECONDS
        return await self.send_all(now)

    async def send_all(self, now: datetime | None = None) -> int:
        """Yesterday's summary to every shop that hasn't had it. Returns how many were sent."""
        start, end = yesterday(now)
        sent = 0
        for store in await self.db.stores_for_summary():
            try:
                if not await self.db.add_daily_summary(store.id, start.date().isoformat()):
                    continue  # already sent today
                if await self.send(store, start, end):
                    sent += 1
            except DatabaseError:
                logger.exception("morning summary failed for a store", extra={"store_id": str(store.id)})
        if sent:
            logger.info("morning summaries sent", extra={"count": sent})
        return sent

    async def send(self, store: Store, start: datetime, end: datetime) -> bool:
        if store.telegram_bot_token is None or store.owner_telegram_id is None:
            return False
        language: Language = "en" if store.daily_summary == "en" else "am"
        raw = await self.db.store_analytics(store.id, start, end)
        low = await self.db.low_stock(store.id, LOW_STOCK_AT)
        try:
            await self.telegram.send_message(store.telegram_bot_token.get_secret_value(), store.owner_telegram_id,
                                             summary_text(store, raw, low, start, language))
        except TelegramError as error:
            # Usually: the owner never pressed Start in the shop's bot.
            logger.info("morning summary not delivered", extra={"error": error.description,
                                                                "store_id": str(store.id)})
            return False
        return True
