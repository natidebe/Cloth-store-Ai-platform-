"""The dashboard's numbers (Phase 10b): how the store is doing.

Periods are Addis Ababa days (UTC+3, no daylight saving): "today" starts at
midnight in Addis, "7d" and "30d" include today. The database computes the
order and payment numbers in one call (store_analytics, migration 010);
here we add what can be derived, the current low stock, and today's AI use.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from app.agents.inventory import LOW_STOCK_AT
from app.services.supabase_service import SupabaseService

ADDIS = timezone(timedelta(hours=3), "EAT")
Period = Literal["today", "7d", "30d"]
PERIOD_DAYS = {"today": 1, "7d": 7, "30d": 30}


def period_bounds(period: Period, now: datetime | None = None) -> tuple[datetime, datetime]:
    """[start, end) of the period: from Addis midnight N-1 days ago until the
    end of today (Addis)."""
    local = (now or datetime.now(timezone.utc)).astimezone(ADDIS)
    today = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return today - timedelta(days=PERIOD_DAYS[period] - 1), today + timedelta(days=1)


def _number(value: Any) -> Decimal:
    return Decimal(str(value or 0))


async def store_analytics(db: SupabaseService, store_id: UUID, period: Period,
                          ai_daily_limit: int | None, now: datetime | None = None) -> dict[str, Any]:
    start, end = period_bounds(period, now)
    raw = await db.store_analytics(store_id, start, end)
    revenue, payments = _number(raw.get("revenue")), int(raw.get("payments") or 0)
    placed, paid = int(raw.get("orders_placed") or 0), int(raw.get("orders_paid") or 0)
    today = (now or datetime.now(timezone.utc)).astimezone(ADDIS).date().isoformat()
    return {
        "period": period,
        "from": start.isoformat(), "to": end.isoformat(),
        "revenue": revenue,
        "payments": payments,
        "average_order": (revenue / payments).quantize(Decimal("0.01")) if payments else Decimal("0"),
        "orders_placed": placed,
        "orders_paid": paid,
        "paid_rate": round(paid / placed, 3) if placed else 0.0,  # 0.75 = 75% of orders were paid
        "unpaid_orders": int(raw.get("unpaid_orders") or 0),
        "delivery_orders": int(raw.get("delivery_orders") or 0),
        "pickup_orders": int(raw.get("pickup_orders") or 0),
        "new_customers": int(raw.get("new_customers") or 0),
        # Phase 12 (D57): sales in the shop next to Telegram, discounts given, per seller.
        "telegram_orders": int(raw.get("telegram_orders") or 0),
        "in_shop_sales": int(raw.get("in_shop_sales") or 0),
        "in_shop_revenue": _number(raw.get("in_shop_revenue")),
        "discount_total": _number(raw.get("discount_total")),
        "discounted_items": int(raw.get("discounted_items") or 0),
        "sellers": [{"telegram_id": s.get("telegram_id"), "name": s.get("name"), "sales": int(s["sales"]),
                     "revenue": _number(s["revenue"])} for s in raw.get("sellers") or []],
        "per_day": [{"day": d["day"], "placed": int(d["placed"]), "paid": int(d["paid"]),
                     "revenue": _number(d["revenue"])} for d in raw.get("per_day") or []],
        "top_products": [{"product_id": t["product_id"], "name": t["name"], "code": t.get("code"),
                          "quantity": int(t["quantity"]), "revenue": _number(t["revenue"])}
                         for t in raw.get("top_products") or []],
        "low_stock": await db.low_stock(store_id, LOW_STOCK_AT),
        "ai_calls_today": await db.ai_calls_today(store_id, today),
        "ai_daily_limit": ai_daily_limit,
    }
