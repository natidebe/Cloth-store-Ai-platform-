"""Shared order helpers used by the scripted order flow (flow.py) and staff.py.

Until Phase 8c this file held the AI's tools; now the chat is a scripted
flow (D28) and the AI calls no tools. What stays here are the parts that
protect the store, enforced in code:
- prices always come from the database
- an order is placed only after the customer confirmed the current summary
- placing the same draft twice returns the same order (idempotency key)
- handing over to staff twice sends only one alert
- there is no way to give discounts or confirm payments
"""
import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID

from app.agents.messages import Language, format_price, t
from app.models.schemas import (
    Conversation,
    Customer,
    IncomingMessage,
    OrderDraft,
    OrderWithItems,
    Store,
    VariantMatch,
)
from app.services.conversation_service import utc_now
from app.services.supabase_service import OrderRejectedError, OutOfStockError, SupabaseService
from app.services.telegram_service import TelegramService

logger = logging.getLogger(__name__)

# D19: a placed order holds its items for this long.
ORDER_HOLD_MINUTES = 5
# D29: delivery addresses are arranged by staff by phone. The database needs
# an address on delivery orders, so this stands in until staff update it.
ADDRESS_TO_ARRANGE = "To be arranged with the customer by phone"


@dataclass
class StaffAlert:
    """A message for the staff group about one customer (sent by staff.py)."""
    text: str
    telegram_id: int  # the customer it's about; staff can Reply to it
    order_id: UUID | None = None  # adds a "Confirm payment" button
    hand_back: bool = False  # adds a "Hand back to bot" button
    photo_file_id: str | None = None  # send this photo, with the text as its caption


@dataclass
class ToolContext:
    """What the order flow can see and change during one run."""
    store: Store
    customer: Customer
    conversation: Conversation  # the run's working copy; the orchestrator saves it
    new_messages: list[IncomingMessage]  # the customer messages this run answers
    chat_id: int
    db: SupabaseService
    telegram: TelegramService
    language: Language = "en"  # the customer's language, for messages our code writes
    staff_alerts: list[StaffAlert] = field(default_factory=list)  # sent once the run is saved


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def describe(variant: VariantMatch, language: Language = "en") -> str:
    """E.g. "Air Force 1 (Nike), White, size 42" / "…, White, ቁጥር 42".
    Product names and colors are shown as the store wrote them."""
    name = variant.product_name + (f" ({variant.brand})" if variant.brand else "")
    parts = [name]
    if variant.color:
        parts.append(variant.color)
    if variant.size:
        parts.append(t("size", language, size=variant.size))
    return ", ".join(parts)


def order_number(order_id: UUID) -> str:
    """Short, readable order number: the first 8 characters of the id."""
    return str(order_id)[:8].upper()


def build_summary(draft: OrderDraft, variants: dict[UUID, VariantMatch],
                  language: Language = "en", store: Store | None = None,
                  closing_key: str = "summary_confirm") -> str:
    """The order summary, with prices from the database (`variants`)."""
    lines = [t("summary_title", language, store)]
    total = Decimal(0)
    for item in draft.items:
        variant = variants[item.variant_id]
        price = variant.price or Decimal(0)
        total += price * item.quantity
        lines.append(f"• {describe(variant, language)} × {item.quantity} — "
                     f"{format_price(price * item.quantity, language)}")
    lines.append(t("summary_total", language, store, total=format_price(total, language)))
    lines.append(t("summary_name", language, store, name=draft.contact_name))
    lines.append(t("summary_phone", language, store, phone=draft.contact_phone))
    if draft.fulfillment_method == "delivery":
        if draft.delivery_address and draft.delivery_address != ADDRESS_TO_ARRANGE:
            lines.append(t("summary_delivery", language, store, address=draft.delivery_address))
        else:  # D29: staff call to arrange it
            lines.append(t("summary_delivery_arranged", language, store))
    else:
        lines.append(t("summary_pickup", language, store))
    lines.append("")
    lines.append(t(closing_key, language, store))
    return "\n".join(lines)


def payment_message(store: Store, order: OrderWithItems, language: Language = "en") -> str:
    """After a PICKUP order: how to pay (the store's accounts), then where and
    when to pick it up (from the store's profile, if set)."""
    total = format_price(order.total_price, language)
    parts = [
        f"{t('order_placed', language, store, number=order_number(order.id), total=total)}\n"
        f"{t('order_holding', language, store, minutes=ORDER_HOLD_MINUTES)}",
        f"{t('how_to_pay', language, store)}\n"
        f"{store.payment_instructions or t('payment_default', language, store)}",
        t("after_paying", language, store),
    ]
    pickup = []
    if store.location:
        pickup.append(t("pickup_where", language, store, location=store.location))
    if store.opening_hours:
        pickup.append(t("pickup_hours", language, store, hours=store.opening_hours))
    if store.pickup_instructions:
        pickup.append(store.pickup_instructions)
    if pickup:
        parts.append("\n".join(pickup))
    return "\n\n".join(parts)


def delivery_message(store: Store, order: OrderWithItems, language: Language = "en") -> str:
    """After a DELIVERY order (D29): staff will call about the address; the
    customer pays when the items arrive, with the store's accounts; and the
    delivery areas and fees, if the store set them."""
    total = format_price(order.total_price, language)
    parts = [t("delivery_handoff", language, store, number=order_number(order.id), total=total,
               phone=order.contact_phone)]
    if store.delivery_info:
        parts.append(f"{t('delivery_fees', language, store)}\n{store.delivery_info}")
    if store.payment_instructions:
        parts.append(f"{t('pay_on_delivery_with', language, store)}\n{store.payment_instructions}")
    else:
        parts.append(t("pay_on_delivery", language, store))
    return "\n\n".join(parts)


def new_order_alert(customer: Customer, order: OrderWithItems) -> StaffAlert:
    items = "\n".join(
        f"• {i.product_name or 'item'}, {i.color or '-'}, size {i.size or '-'} × {i.quantity}"
        for i in order.items
    )
    where = (f"Delivery to: {order.delivery_address}" if order.fulfillment_method == "delivery"
             else "Pickup at the store")
    text = (
        f"🛒 New order #{order_number(order.id)} — waiting for payment\n"
        f"{items}\nTotal: {format_price(order.total_price)}\n"
        f"Customer: {order.contact_name}, {order.contact_phone} (Telegram id {customer.telegram_id})\n"
        f"{where}"
    )
    return StaffAlert(text=text, telegram_id=customer.telegram_id, order_id=order.id)


def delivery_order_alert(customer: Customer, order: OrderWithItems,
                         username: str | None) -> StaffAlert:
    """D29: a delivery order is handed to staff, who call the customer to
    arrange the address; the customer was told they pay on delivery."""
    items = "\n".join(
        f"• {i.product_name or 'item'}, {i.color or '-'}, size {i.size or '-'} × {i.quantity}"
        for i in order.items
    )
    contact = f"{order.contact_name}, {order.contact_phone}"
    if username:
        contact += f", @{username}"
    text = (
        f"🚚 New DELIVERY order #{order_number(order.id)} — please call the customer\n"
        f"{items}\nTotal: {format_price(order.total_price)} (delivery fee not included)\n"
        f"Customer: {contact} (Telegram id {customer.telegram_id})\n"
        "Arrange the delivery address. The customer was told they pay when they receive "
        "the items. The bot "
        "stays silent in this chat until you hand it back. Reply to this message to "
        "write to the customer."
    )
    return StaffAlert(text=text, telegram_id=customer.telegram_id, order_id=order.id, hand_back=True)


# ---------------------------------------------------------------------------
# Is this a real "yes"? (checked by code, not by the AI)
# ---------------------------------------------------------------------------

_YES_WORDS = {
    "yes", "yeah", "yep", "yup", "ok", "okay", "sure", "confirm", "confirmed",
    "correct", "agree", "proceed", "awo", "awon", "ishi", "eshi",
    "አዎ", "አዎን", "እሺ", "ትክክል", "ይሁን",
}
_YES_PHRASES = ("go ahead", "sounds good", "place the order", "place it", "👍", "✅")
_NO_WORDS = {
    "no", "not", "dont", "don't", "cancel", "wait", "change", "stop",
    # "yes, but ..." / "ok, instead ...": the customer wants something different
    "but", "instead", "however", "except", "rather", "different", "another",
    "edit", "modify", "replace", "switch",
    "አይ", "አይደለም", "አልፈልግም", "ይቅር",
}
# Amharic adds endings to words (ቀይሩ, ቀይረው, ቀይሩልኝ...), so these are
# matched anywhere in the message, not as whole words.
_NO_STEMS = (
    "ግን",      # but
    "ቀይር", "ቀይሩ", "ቀይረ", "ቀይሪ",  # change (not ቀይ = red)
    "ለውጥ", "ለውጡ", "ለውጠ",  # change
    "ሌላ",      # other / another
    "በምትኩ",    # instead
    "ሳይሆን",    # rather than
)
_WORD = re.compile(r"[\w']+", re.UNICODE)


def looks_like_yes(text: str | None) -> bool:
    """A short, clear confirmation like "yes", "ok go ahead", "አዎ".
    Anything with a "no", "wait", "change", or "but" in it doesn't count:
    "yes, but size 43" means the customer wants a change, not this order."""
    if not text:
        return False
    lowered = text.lower()
    words = set(_WORD.findall(lowered))
    if words & _NO_WORDS or any(stem in lowered for stem in _NO_STEMS):
        return False
    return bool(words & _YES_WORDS) or any(p in lowered for p in _YES_PHRASES)


# ---------------------------------------------------------------------------
# Handing over to staff, and placing the order
# ---------------------------------------------------------------------------

@dataclass
class EscalateArgs:
    reason: str
    summary: str


async def escalate_to_staff(args: EscalateArgs, ctx: ToolContext) -> bool:
    """Pause the bot for this customer and alert the staff group once.
    False if staff already have this chat (no second alert)."""
    conversation = ctx.conversation
    if conversation.bot_paused:
        return False
    conversation.bot_paused = True
    conversation.paused_at = utc_now()
    conversation.staff_active_at = None
    photos = [m.photo_file_id for m in ctx.new_messages if m.photo_file_id]
    ctx.staff_alerts.append(StaffAlert(
        text=(
            f"🙋 A customer needs a person\n"
            f"Customer: {ctx.customer.name or 'unknown'} (Telegram id {ctx.customer.telegram_id})\n"
            f"Reason: {args.reason[:200]}\n"
            f"Summary: {args.summary[:1000]}\n"
            "The bot stays silent in this chat until you hand it back. "
            "To answer the customer, Reply to this message."
        ),
        telegram_id=ctx.customer.telegram_id,
        order_id=conversation.order_draft.last_order_id,  # adds "Confirm payment"
        hand_back=True,
        photo_file_id=photos[-1] if photos else None,  # e.g. the payment screenshot
    ))
    return True


@dataclass
class PlacedOrder:
    order: OrderWithItems | None = None
    sold_out_variant: UUID | None = None  # set when an item sold out just now
    refused: str | None = None  # another refusal code


async def place_order(ctx: ToolContext) -> PlacedOrder:
    """Create the order from the draft (place_order in the database: prices
    from the database, stock checked, items held for ORDER_HOLD_MINUTES).
    The caller has already checked the customer confirmed the summary."""
    draft = ctx.conversation.order_draft
    # Fixed per draft version: a repeated confirm, a retry, or a resent update
    # all give the same key, so the database returns the same order.
    key = f"conv-{ctx.conversation.id}-rev-{draft.revision}"
    try:
        order_id = await ctx.db.create_order(
            ctx.store.id, ctx.customer.id, draft, key, hold_minutes=ORDER_HOLD_MINUTES)
    except OutOfStockError as error:
        return PlacedOrder(sold_out_variant=UUID(error.detail) if error.detail else None)
    except OrderRejectedError as error:
        return PlacedOrder(refused=error.code)

    order = await ctx.db.get_order(ctx.store.id, order_id)
    logger.info("order placed", extra={"order_id": str(order_id)})
    ctx.staff_alerts.append(new_order_alert(ctx.customer, order))
    try:  # remember the contact details for next time
        await ctx.db.update_customer(ctx.store.id, ctx.customer.id, name=draft.contact_name,
                                     phone=draft.contact_phone, address=draft.delivery_address)
    except Exception:
        logger.warning("could not update customer details", exc_info=True)
    return PlacedOrder(order=order)
