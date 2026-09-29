"""The tools the AI can ask us to run, and the code that runs them.

The AI only chooses a tool and gives simple arguments (what to search for,
the customer's phone, ...). It never gives the store, the customer, or a
price: those come from the webhook URL and the database. Arguments are
checked before anything runs; unknown tools or bad arguments are sent back
to the AI as an error.

The rules that protect the store are enforced here in code, not only in the
prompt:
- prices always come from the database
- an order is placed only after a real "yes" from the customer, sent after
  the order summary, with the draft unchanged since (checked by code)
- placing the same draft twice returns the same order (idempotency key)
- handing over to staff twice sends only one alert
- there is no tool for discounts or for confirming payments
"""
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.models.schemas import (
    Conversation,
    Customer,
    DraftItem,
    FulfillmentMethod,
    IncomingMessage,
    OrderDraft,
    OrderWithItems,
    Product,
    Store,
    VariantMatch,
)
from app.services.conversation_service import utc_now
from app.services.llm_service import ToolCall, ToolDefinition
from app.services.supabase_service import (
    OrderRejectedError,
    OutOfStockError,
    SupabaseService,
)
from app.services.telegram_service import TelegramService

logger = logging.getLogger(__name__)

# D19: a placed order holds its items for this long.
ORDER_HOLD_MINUTES = 5
# At or below this many available, customers are told "only a few left".
# Exact stock numbers are never shown.
LOW_STOCK_THRESHOLD = 3
MAX_QUANTITY_PER_ITEM = 10
MAX_SEARCH_RESULTS = 10

DEFAULT_PAYMENT_TEXT = "Our team will send you the payment details shortly."


# ---------------------------------------------------------------------------
# What a tool can see and change during one run
# ---------------------------------------------------------------------------

@dataclass
class ToolContext:
    store: Store
    customer: Customer
    conversation: Conversation  # the run's working copy; the orchestrator saves it
    new_messages: list[IncomingMessage]  # the customer messages this run answers
    chat_id: int
    db: SupabaseService
    telegram: TelegramService
    products: list[Product] = field(default_factory=list)  # the store's product names
    # Filled in by tools, used by the orchestrator:
    sent: list[str] = field(default_factory=list)  # already sent to the customer (the summary)
    after_reply: list[str] = field(default_factory=list)  # to send after the AI's reply
    staff_alerts: list[str] = field(default_factory=list)  # sent once the run is saved


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_price(price: Decimal | None) -> str:
    if price is None:
        return "price not set"
    if price == price.to_integral_value():
        return f"{price:,.0f} ETB"
    return f"{price:,.2f} ETB"


def availability(variant: VariantMatch) -> str:
    if variant.available <= 0:
        return "sold out"
    if variant.available <= LOW_STOCK_THRESHOLD:
        return "only a few left"
    return "in stock"


def describe(variant: VariantMatch) -> str:
    """E.g. "Air Force 1 (Nike), White, size 42"."""
    name = variant.product_name + (f" ({variant.brand})" if variant.brand else "")
    parts = [name]
    if variant.color:
        parts.append(variant.color)
    if variant.size:
        parts.append(f"size {variant.size}")
    return ", ".join(parts)


def order_number(order_id: UUID) -> str:
    """Short, readable order number: the first 8 characters of the id."""
    return str(order_id)[:8].upper()


def build_summary(draft: OrderDraft, variants: dict[UUID, VariantMatch]) -> str:
    lines = ["🧾 Order summary"]
    total = Decimal(0)
    for item in draft.items:
        price = variants[item.variant_id].price or Decimal(0)
        total += price * item.quantity
        lines.append(f"• {item.description} × {item.quantity} — {format_price(price * item.quantity)}")
    lines.append(f"Total: {format_price(total)}")
    lines.append(f"Name: {draft.contact_name}")
    lines.append(f"Phone: {draft.contact_phone}")
    if draft.fulfillment_method == "delivery":
        lines.append(f"Delivery to: {draft.delivery_address}")
    else:
        lines.append("Pickup at the store")
    lines.append("")
    lines.append('Reply "yes" (አዎ) to confirm, or tell me what to change.')
    return "\n".join(lines)


def payment_message(store: Store, order: OrderWithItems) -> str:
    return (
        f"✅ Order #{order_number(order.id)} is placed. Total: {format_price(order.total_price)}.\n"
        f"We're holding your items for {ORDER_HOLD_MINUTES} minutes.\n\n"
        f"How to pay:\n{store.payment_instructions or DEFAULT_PAYMENT_TEXT}\n\n"
        "After paying, please send a screenshot of the payment here."
    )


def new_order_alert(customer: Customer, order: OrderWithItems) -> str:
    items = "\n".join(
        f"• {i.product_name or 'item'}, {i.color or '-'}, size {i.size or '-'} × {i.quantity}"
        for i in order.items
    )
    where = (f"Delivery to: {order.delivery_address}" if order.fulfillment_method == "delivery"
             else "Pickup at the store")
    return (
        f"🛒 New order #{order_number(order.id)} — waiting for payment\n"
        f"{items}\nTotal: {format_price(order.total_price)}\n"
        f"Customer: {order.contact_name}, {order.contact_phone} (Telegram id {customer.telegram_id})\n"
        f"{where}"
    )


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
# Tool arguments (anything extra, like a price or store_id, is refused)
# ---------------------------------------------------------------------------

class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CheckStockArgs(_Args):
    query: str = Field(min_length=1, max_length=100)
    color: str | None = Field(default=None, max_length=50)
    size: str | None = Field(default=None, max_length=20)


class ItemArgs(_Args):
    variant_id: UUID
    quantity: int = Field(default=1, ge=1, le=MAX_QUANTITY_PER_ITEM)


class UpdateDraftArgs(_Args):
    items: list[ItemArgs] | None = Field(default=None, max_length=10)
    contact_name: str | None = Field(default=None, max_length=100)
    contact_phone: str | None = Field(default=None, max_length=30)
    fulfillment_method: FulfillmentMethod | None = None
    delivery_address: str | None = Field(default=None, max_length=300)


class NoArgs(_Args):
    pass


class EscalateArgs(_Args):
    reason: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=1000)


# ---------------------------------------------------------------------------
# The tools
# ---------------------------------------------------------------------------

def _variant_view(variant: VariantMatch) -> dict[str, Any]:
    return {
        "variant_id": str(variant.variant_id),
        "product": variant.product_name,
        "brand": variant.brand,
        "category": variant.category,
        "color": variant.color,
        "size": variant.size,
        "price": format_price(variant.price),
        "availability": availability(variant),
    }


async def check_stock(args: CheckStockArgs, ctx: ToolContext) -> dict[str, Any]:
    variants = await ctx.db.search_variants(
        ctx.store.id, args.query, color=args.color, size=args.size, limit=MAX_SEARCH_RESULTS
    )
    if variants:
        return {"results": [_variant_view(v) for v in variants]}

    # No exact match: other colors and sizes of the same product(s).
    if args.color or args.size:
        alternatives = await ctx.db.search_variants(ctx.store.id, args.query, limit=MAX_SEARCH_RESULTS)
        if alternatives:
            return {"results": [], "alternatives": [_variant_view(v) for v in alternatives],
                    "note": "No exact match for that color/size. These are the other colors and "
                            "sizes; offer the ones in stock."}

    # Nothing at all: maybe the product has another name in the catalog.
    return {"results": [], "product_names": [p.name for p in ctx.products],
            "note": "Nothing matches. If one of these product names is what the customer means, "
                    "search again with that name (in English). Otherwise tell the customer we "
                    "don't have it."}


def _draft_view(draft: OrderDraft) -> dict[str, Any]:
    return {
        "items": [{"variant_id": str(i.variant_id), "description": i.description,
                   "quantity": i.quantity} for i in draft.items],
        "contact_name": draft.contact_name,
        "contact_phone": draft.contact_phone,
        "fulfillment_method": draft.fulfillment_method,
        "delivery_address": draft.delivery_address,
        "missing": draft.missing_fields(),
    }


_ORDER_FIELDS = {"items", "contact_name", "contact_phone", "fulfillment_method", "delivery_address"}


async def update_order_draft(args: UpdateDraftArgs, ctx: ToolContext) -> dict[str, Any]:
    old = ctx.conversation.order_draft
    changes: dict[str, Any] = args.model_dump(exclude_none=True, exclude={"items"})

    if args.items is not None:
        quantities: dict[UUID, int] = {}
        for item in args.items:  # the same variant twice: add up
            quantities[item.variant_id] = quantities.get(item.variant_id, 0) + item.quantity
        found = {v.variant_id: v for v in await ctx.db.get_variants(ctx.store.id, list(quantities))}
        problems = []
        for variant_id, quantity in quantities.items():
            variant = found.get(variant_id)
            if variant is None:
                problems.append(f"{variant_id}: unknown item (use a variant_id from check_stock)")
            elif variant.price is None:
                problems.append(f"{describe(variant)}: not for sale yet (no price)")
            elif variant.available < quantity:
                state = "sold out" if variant.available <= 0 else "not enough in stock for that quantity"
                problems.append(f"{describe(variant)}: {state}")
        if problems:
            return {"error": "Nothing was saved.", "problems": problems}
        changes["items"] = [
            DraftItem(variant_id=v, quantity=q, description=describe(found[v])).model_dump()
            for v, q in quantities.items()
        ]

    try:
        draft = OrderDraft.model_validate({**old.model_dump(), **changes})
    except ValidationError as error:
        return {"error": "Nothing was saved.", "problems": [e["msg"] for e in error.errors()]}

    if draft.model_dump(include=_ORDER_FIELDS) != old.model_dump(include=_ORDER_FIELDS):
        # Any change makes an earlier summary (and "yes") no longer valid.
        draft.revision = old.revision + 1
        draft.summary_revision = None
        draft.summary_message_id = None
        draft.customer_confirmed = False
    ctx.conversation.order_draft = draft
    return {"saved": True, "draft": _draft_view(draft)}


async def confirm_order(args: NoArgs, ctx: ToolContext) -> dict[str, Any]:
    draft = ctx.conversation.order_draft
    if not draft.items:
        if draft.last_order_id:
            return {"status": "already_placed", "order_number": order_number(draft.last_order_id),
                    "instruction": "This order is already placed. Do not place it again."}
        return {"error": "The order has no items yet."}
    missing = draft.missing_fields()
    if missing:
        return {"error": "Some details are missing.", "missing": missing}

    summary_is_current = (draft.summary_revision == draft.revision
                          and draft.summary_message_id is not None)
    if summary_is_current:
        confirmed = any(
            m.message_id > draft.summary_message_id and looks_like_yes(m.text)
            for m in ctx.new_messages
        )
        if confirmed:
            return await _place_order(ctx)
        return {"status": "waiting_for_yes",
                "instruction": "The customer has not clearly confirmed the summary. Answer their "
                               "message, and ask them to reply 'yes' if they want to place the order. "
                               "The order is NOT placed."}

    # First time for this version of the draft: re-check it and send the
    # summary ourselves, so the prices in it come from the database.
    variants = {v.variant_id: v for v in await ctx.db.get_variants(
        ctx.store.id, [i.variant_id for i in draft.items])}
    problems = []
    for item in draft.items:
        variant = variants.get(item.variant_id)
        if variant is None or variant.price is None:
            problems.append(f"{item.description}: no longer available")
        elif variant.available < item.quantity:
            problems.append(f"{item.description}: sold out" if variant.available <= 0
                            else f"{item.description}: not enough in stock")
    if problems:
        return {"error": "The order can't be placed as it is.", "problems": problems,
                "instruction": "Tell the customer and offer alternatives (use check_stock)."}

    summary = build_summary(draft, variants)
    message_id = await ctx.telegram.send_message(
        ctx.store.telegram_bot_token.get_secret_value(), ctx.chat_id, summary
    )
    draft.summary_revision = draft.revision
    draft.summary_message_id = message_id
    ctx.sent.append(summary)
    return {"status": "summary_sent",
            "instruction": "The order summary was just sent to the customer. Ask them to reply "
                           "'yes' to confirm. Do not repeat the summary. The order is NOT placed yet."}


async def _place_order(ctx: ToolContext) -> dict[str, Any]:
    draft = ctx.conversation.order_draft
    draft.customer_confirmed = True
    # Fixed per draft version: a repeated "yes", a retry, or a resent update
    # all give the same key, so the database returns the same order.
    key = f"conv-{ctx.conversation.id}-rev-{draft.revision}"
    try:
        order_id = await ctx.db.create_order(
            ctx.store.id, ctx.customer.id, draft, key, hold_minutes=ORDER_HOLD_MINUTES
        )
    except OutOfStockError as error:
        item = next((i.description for i in draft.items if str(i.variant_id) == error.detail), "an item")
        draft.summary_revision = draft.summary_message_id = None  # needs a new summary
        return {"error": f"Sorry: {item} just sold out.", "instruction":
                "Tell the customer and offer alternatives (use check_stock). The order is NOT placed."}
    except OrderRejectedError as error:
        return {"error": f"The order was refused ({error.code}).", "instruction":
                "Tell the customer something went wrong and hand over to staff if needed."}

    orders = await ctx.db.get_customer_orders(ctx.store.id, ctx.customer.id, limit=5)
    order = next(o for o in orders if o.id == order_id)
    logger.info("order placed by agent", extra={"order_id": str(order_id)})

    # Start a fresh draft. The revision keeps counting up so the next
    # order's idempotency key is different.
    ctx.conversation.order_draft = OrderDraft(revision=draft.revision + 1, last_order_id=order_id)
    ctx.after_reply.append(payment_message(ctx.store, order))
    ctx.staff_alerts.append(new_order_alert(ctx.customer, order))
    try:  # remember the contact details for next time
        await ctx.db.update_customer(ctx.store.id, ctx.customer.id, name=draft.contact_name,
                                     phone=draft.contact_phone, address=draft.delivery_address)
    except Exception:
        logger.warning("could not update customer details", exc_info=True)

    return {"status": "order_placed", "order_number": order_number(order_id),
            "total": format_price(order.total_price),
            "instruction": "Thank the customer in one short sentence and say the order is placed "
                           "(not 'confirmed': it's confirmed once staff check the payment). The "
                           "payment instructions are sent automatically right after your reply; "
                           "don't repeat them."}


async def check_order_status(args: NoArgs, ctx: ToolContext) -> dict[str, Any]:
    orders = await ctx.db.get_customer_orders(ctx.store.id, ctx.customer.id, limit=5)
    if not orders:
        return {"orders": [], "note": "This customer has no orders."}
    return {"orders": [{
        "order_number": order_number(o.id),
        "placed_on": o.created_at.date().isoformat() if o.created_at else None,
        "status": o.status,
        "payment_status": o.payment_status,
        "total": format_price(o.total_price),
        "fulfillment": o.fulfillment_method,
        "items": [f"{i.product_name or 'item'}, {i.color or '-'}, size {i.size or '-'} × {i.quantity}"
                  for i in o.items],
    } for o in orders]}


async def escalate_to_staff(args: EscalateArgs, ctx: ToolContext) -> dict[str, Any]:
    conversation = ctx.conversation
    if conversation.bot_paused:
        return {"status": "already_with_staff",
                "instruction": "Staff already have this chat. Don't escalate again."}
    conversation.bot_paused = True
    conversation.paused_at = utc_now()
    ctx.staff_alerts.append(
        f"🙋 A customer needs a person\n"
        f"Customer: {ctx.customer.name or 'unknown'} (Telegram id {ctx.customer.telegram_id})\n"
        f"Reason: {args.reason}\n"
        f"Summary: {args.summary}\n"
        "The bot stays silent in this chat until staff hand it back."
    )
    return {"status": "handed_over",
            "instruction": "Tell the customer in one short sentence that a team member will reply soon."}


# ---------------------------------------------------------------------------
# Definitions sent to the AI, and running a call
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _Tool:
    definition: ToolDefinition
    args: type[_Args]
    run: Callable[[Any, ToolContext], Awaitable[dict[str, Any]]]


_NO_PARAMETERS = {"type": "object", "properties": {}, "additionalProperties": False}

_TOOLS: dict[str, _Tool] = {t.definition.name: t for t in [
    _Tool(ToolDefinition(
        name="check_stock",
        description=(
            "Search this store's catalog for products, with colors, sizes, price, and "
            "availability. Always use it before talking about stock or prices. Search in "
            "English catalog words (translate Amharic, e.g. ጫማ -> shoes)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Product name, brand, or category, in English"},
                "color": {"type": "string", "description": "Optional, e.g. white"},
                "size": {"type": "string", "description": "Optional, exact size, e.g. 42 or M"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    ), CheckStockArgs, check_stock),
    _Tool(ToolDefinition(
        name="update_order_draft",
        description=(
            "Save order details as the customer gives them. Only include fields the customer "
            "just gave or changed. 'items' is the COMPLETE list of items they want (it "
            "replaces the previous list); use variant_id values from check_stock."
        ),
        parameters={
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "variant_id": {"type": "string"},
                            "quantity": {"type": "integer", "minimum": 1, "maximum": MAX_QUANTITY_PER_ITEM},
                        },
                        "required": ["variant_id", "quantity"],
                        "additionalProperties": False,
                    },
                },
                "contact_name": {"type": "string"},
                "contact_phone": {"type": "string"},
                "fulfillment_method": {"type": "string", "enum": ["delivery", "pickup"]},
                "delivery_address": {"type": "string"},
            },
            "additionalProperties": False,
        },
    ), UpdateDraftArgs, update_order_draft),
    _Tool(ToolDefinition(
        name="confirm_order",
        description=(
            "Call when every detail is collected. The first call sends the customer an order "
            "summary to confirm. Call it again after the customer replies 'yes' to place "
            "the order."
        ),
        parameters=_NO_PARAMETERS,
    ), NoArgs, confirm_order),
    _Tool(ToolDefinition(
        name="check_order_status",
        description="This customer's recent orders with their status (for 'where is my order?').",
        parameters=_NO_PARAMETERS,
    ), NoArgs, check_order_status),
    _Tool(ToolDefinition(
        name="escalate_to_staff",
        description=(
            "Hand the chat to a human: discounts or bargaining, complaints, payment questions "
            "or 'I paid', anything you're unsure about, or when the customer asks for a person. "
            "After this the bot stays silent in this chat."
        ),
        parameters={
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "Short reason, e.g. 'asks for a discount'"},
                "summary": {"type": "string", "description": "What the customer wants, for staff"},
            },
            "required": ["reason", "summary"],
            "additionalProperties": False,
        },
    ), EscalateArgs, escalate_to_staff),
]}

TOOL_DEFINITIONS: list[ToolDefinition] = [t.definition for t in _TOOLS.values()]


async def run_tool(call: ToolCall, ctx: ToolContext) -> str:
    """Run one tool call and return the result for the AI, as JSON text.
    Bad calls get an error result; nothing is run."""
    tool = _TOOLS.get(call.name)
    if tool is None:
        result: dict[str, Any] = {"error": f"Unknown tool '{call.name}'."}
    elif call.arguments_error:
        result = {"error": f"The arguments were not valid JSON: {call.arguments_error}"}
    else:
        try:
            args = tool.args.model_validate(call.arguments)
        except ValidationError as error:
            result = {"error": "Invalid arguments; nothing was done.", "problems": [
                f"{'.'.join(str(p) for p in e['loc']) or 'arguments'}: {e['msg']}" for e in error.errors()
            ]}
        else:
            result = await tool.run(args, ctx)
    logger.info("tool call", extra={"tool": call.name, "ok": "error" not in result})
    return json.dumps(result, ensure_ascii=False, default=str)
