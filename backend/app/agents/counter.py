"""Sales in the shop (Phase 12, decisions D53–D57).

A walk-in customer buys at the counter; staff record it in the Mini App:
- The listed price never changes (D54). Each line keeps the listed price
  and the price agreed at the counter; the difference is the discount.
- The owner may agree any price (up to the listed one); staff down to the
  store's limit, staff_discount_percent (D53). The database checks it in
  the same step that saves the sale, so it can't be bypassed.
- Stock goes down, the sale is saved as paid and handed over, all or
  nothing (record_counter_sale, migration 012). The channel post updates by
  itself (the stock change reaches the catalog webhook, Phase 8d).
- The last piece held by an online order (D55): the Mini App warns first
  (availability); if staff sell it anyway, the staff group is told to call
  that online customer.
- Every counter sale is announced in the staff group (the owner sees shop
  sales too). Staff-facing texts are in English, like the other alerts.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.agents.messages import format_price
from app.agents.miniapp import AppAccess
from app.agents.tools import describe, order_number
from app.models.schemas import Store, VariantMatch
from app.services.supabase_service import (
    DatabaseError,
    NotFoundError,
    OrderRejectedError,
    OutOfStockError,
    SupabaseService,
)
from app.services.telegram_service import TelegramError, TelegramService

logger = logging.getLogger(__name__)


class CounterSaleError(Exception):
    """A sale was refused. `message` is safe to show in the Mini App; `code`
    tells the app what happened (e.g. "held_by_online_order": ask, then retry)."""

    def __init__(self, message: str, status_code: int, code: str):
        super().__init__(message)
        self.message, self.status_code, self.code = message, status_code, code


@dataclass
class SaleLine:
    variant_id: UUID
    quantity: int
    price: Decimal  # agreed at the counter


@dataclass
class SaleRequest:
    lines: list[SaleLine]
    payment_method: str
    request_id: str  # from the app: the same sale sent twice is saved once
    payment_note: str | None = None
    customer_name: str | None = None
    customer_phone: str | None = None
    note: str | None = None
    allow_held: bool = False


@dataclass
class SaleResult:
    order_id: UUID
    number: str
    total: Decimal
    list_total: Decimal
    already_saved: bool
    held_orders: list[str] = field(default_factory=list)  # order numbers to call

    @property
    def discount(self) -> Decimal:
        return max(self.list_total - self.total, Decimal(0))


def _percent_off(listed: Decimal, paid: Decimal) -> str:
    if listed <= 0 or paid >= listed:
        return ""
    return f", −{((listed - paid) / listed * 100).quantize(Decimal('1'))}%"


class CounterSales:
    def __init__(self, db: SupabaseService, telegram: TelegramService):
        self.db, self.telegram = db, telegram

    async def availability(self, store: Store, variant_id: UUID) -> dict[str, Any]:
        """Stock, what online orders are holding, and for how long (D55)."""
        [variant] = await self.db.get_variants(store.id, [variant_id]) or [None]
        if variant is None:
            raise CounterSaleError("This item doesn't exist (any more).", 404, "variant_not_found")
        now = datetime.now(timezone.utc)
        holds = []
        for hold in await self.db.holds_on_variant(store.id, variant_id):
            until = datetime.fromisoformat(hold["reserved_until"])
            holds.append({"order_number": order_number(UUID(hold["order_id"])),
                          "quantity": hold["quantity"],
                          "minutes_left": max(0, round((until - now).total_seconds() / 60))})
        return {"variant_id": variant_id, "stock": variant.stock_quantity, "held": variant.held,
                "available": max(0, variant.stock_quantity - variant.held),
                "listed_price": variant.price, "holds": holds}

    async def sell(self, access: AppAccess, sale: SaleRequest) -> SaleResult:
        store = access.store
        if store.status != "active":
            raise CounterSaleError("The store isn't approved yet.", 409, "store_not_active")
        variants = {v.variant_id: v for v in await self.db.get_variants(store.id, [l.variant_id for l in sale.lines])}
        limit = None if access.is_owner else store.staff_discount_percent
        try:
            raw = await self.db.record_counter_sale(
                store.id,
                [{"variant_id": l.variant_id, "quantity": l.quantity, "price": l.price} for l in sale.lines],
                payment_method=sale.payment_method, payment_note=sale.payment_note,
                sold_by_telegram_id=access.user.id, sold_by_name=access.user.full_name,
                contact_name=sale.customer_name, contact_phone=sale.customer_phone, note=sale.note,
                max_discount_percent=limit, allow_held=sale.allow_held,
                idempotency_key=f"counter:{sale.request_id}",
            )
        except DatabaseError as error:
            raise self._refusal(error, store, variants) from None

        result = SaleResult(
            order_id=UUID(raw["order_id"]), number=order_number(UUID(raw["order_id"])),
            total=Decimal(str(raw.get("total") or 0)),
            list_total=Decimal(str(raw.get("list_total") or raw.get("total") or 0)),
            already_saved=bool(raw.get("already_saved")),
            held_orders=[order_number(UUID(o)) for o in raw.get("held_orders") or []],
        )
        if not result.already_saved:
            logger.info("counter sale", extra={"store_id": str(store.id), "items": len(sale.lines),
                                               "discount": str(result.discount)})
            await self._tell_staff(access, sale, result, variants, [UUID(o) for o in raw.get("held_orders") or []])
        return result

    def _refusal(self, error: DatabaseError, store: Store, variants: dict[UUID, VariantMatch]) -> CounterSaleError:
        code = error.code
        item = variants.get(UUID(error.detail)) if error.detail and len(error.detail) == 36 else None
        label = describe(item, store=store) if item else "This item"
        if isinstance(error, OutOfStockError):
            return CounterSaleError(f"{label}: not enough in stock.", 409, "insufficient_stock")
        if isinstance(error, NotFoundError):
            if code == "store_not_found":
                return CounterSaleError("The store isn't active.", 409, "store_not_active")
            return CounterSaleError("An item doesn't exist (any more).", 404, "variant_not_found")
        if isinstance(error, OrderRejectedError):
            messages = {
                "discount_too_large": (403, f"Staff can give at most {store.staff_discount_percent.normalize():f}% "
                                            "off the listed price. Ask the owner for a lower price."),
                "price_above_list": (422, "The price can't be above the listed price."),
                "held_by_online_order": (409, f"{label} is held by an online order. Sell it anyway?"),
                "price_missing": (409, "An item has no price yet: the owner sets it first."),
                "duplicate_item": (422, "The same item is in the sale twice: change its quantity instead."),
                "empty_order": (422, "Add at least one item."),
                "invalid_quantity": (422, "Check the quantities."),
                "invalid_price": (422, "Check the prices."),
            }
            status, message = messages.get(code, (409, "The sale was refused."))
            return CounterSaleError(message, status, code)
        logger.error("counter sale failed", extra={"error": str(error)})
        return CounterSaleError("The sale couldn't be saved. Try again.", 503, "database_error")

    async def _tell_staff(self, access: AppAccess, sale: SaleRequest, result: SaleResult,
                          variants: dict[UUID, VariantMatch], held_orders: list[UUID]) -> None:
        """The sale (and any online order that now can't be filled) in the staff group."""
        store = access.store
        if store.staff_chat_id is None:
            return
        lines = []
        for line in sale.lines:
            variant = variants.get(line.variant_id)
            listed = variant.price if variant and variant.price is not None else line.price
            text = f"• {describe(variant, store=store) if variant else 'item'} × {line.quantity}: {format_price(line.price * line.quantity)}"
            if line.price < listed:
                text += f" (listed {format_price(listed * line.quantity)}{_percent_off(listed, line.price)})"
            lines.append(text)
        method = sale.payment_method + (f" ({sale.payment_note})" if sale.payment_note else "")
        note = (f"🏪 {access.user.full_name} sold in the shop (#{result.number}):\n" + "\n".join(lines)
                + f"\nTotal: {format_price(result.total)}, {method}")
        if result.discount > 0:
            note += f"\nDiscount: {format_price(result.discount)}"
        if sale.customer_name or sale.customer_phone:
            note += "\nCustomer: " + ", ".join(x for x in (sale.customer_name, sale.customer_phone) if x)
        if sale.note:
            note += f"\nNote: {sale.note}"
        await self._note(store, note)
        for order_id in held_orders:
            order = await self.db.get_order(store.id, order_id)
            who = ", ".join(x for x in (order.contact_name, order.contact_phone) if x) if order else ""
            await self._note(store, f"📞 Online order #{order_number(order_id)} can't be filled: an item it was "
                                    f"holding was just sold in the shop. Please call the customer"
                                    + (f" ({who})." if who else "."))

    async def _note(self, store: Store, text: str) -> None:
        try:
            await self.telegram.notify_staff(store, text)
        except TelegramError as error:
            logger.warning("counter sale note not sent", extra={"error": error.description})
