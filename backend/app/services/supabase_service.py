"""All database reads and writes.

The only module that talks to Supabase. It uses the service_role key, which
bypasses Row Level Security, so EVERY function takes store_id and filters by
it. store_id must come from the webhook URL, never from the AI.

Multi-step writes (placing an order, changing stock, confirming a payment)
go through the Postgres functions from migration 002, so they are
all-or-nothing.
"""
import logging
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx
from postgrest.exceptions import APIError
from supabase import AsyncClient, acreate_client
from supabase.lib.client_options import AsyncClientOptions

from app.models.schemas import (
    ChatMessage,
    Conversation,
    Customer,
    InboxItem,
    OrderDraft,
    OrderItemDetail,
    OrderWithItems,
    Store,
    VariantMatch,
    normalize_phone,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class DatabaseError(Exception):
    """Base class. `code` is short and machine-readable, e.g. 'out_of_stock'."""

    def __init__(self, code: str, detail: str | None = None):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


class NotFoundError(DatabaseError):
    """The row doesn't exist, or belongs to another store."""


class OutOfStockError(DatabaseError):
    """Not enough stock. `detail` holds the variant id."""


class OrderRejectedError(DatabaseError):
    """A business rule refused the request (e.g. already_paid, address_required)."""


class DuplicateError(DatabaseError):
    """A unique rule was broken (e.g. the customer already exists)."""


class DatabaseUnavailableError(DatabaseError):
    """Supabase couldn't be reached."""


class VersionConflictError(DatabaseError):
    """The conversation was saved by someone else since we loaded it."""


# Error codes raised by the 002 functions.
_NOT_FOUND = {"store_not_found", "customer_not_found", "variant_not_found", "order_not_found"}
_OUT_OF_STOCK = {"out_of_stock", "insufficient_stock"}
_REJECTED = {
    "empty_order", "invalid_quantity", "price_missing", "invalid_fulfillment",
    "address_required", "order_cancelled", "already_paid", "incomplete_order",
}
_UNIQUE_VIOLATION = "23505"

# Give up on a database call after this long, so a slow database can't
# leave a customer waiting forever.
_DB_TIMEOUT_SECONDS = 10.0


def _translate(error: APIError) -> DatabaseError:
    code, detail = error.message or "", error.details
    if code in _NOT_FOUND:
        return NotFoundError(code, detail)
    if code in _OUT_OF_STOCK:
        return OutOfStockError(code, detail)
    if code in _REJECTED:
        return OrderRejectedError(code, detail)
    if error.code == _UNIQUE_VIOLATION:
        return DuplicateError("duplicate", error.message)
    return DatabaseError("database_error", f"{error.code}: {error.message}")


# ---------------------------------------------------------------------------
# Search helpers
# ---------------------------------------------------------------------------

# Keep letters (any language, so Amharic works), digits, and hyphens. Other
# characters have special meaning in Supabase's filter syntax.
_UNSAFE_SEARCH_CHARS = re.compile(r"[^\w\s-]", re.UNICODE)
_MAX_SEARCH_WORDS = 5


def _search_words(text: str | None) -> list[str]:
    cleaned = _UNSAFE_SEARCH_CHARS.sub(" ", text or "")
    return cleaned.split()[:_MAX_SEARCH_WORDS]


_VARIANT_COLUMNS = (
    "id, product_id, color, size, stock_quantity, price_override, "
    "products!inner(name, brand, category, base_price)"
)


def _to_variant_match(row: dict[str, Any]) -> VariantMatch:
    product = row["products"]
    price = row["price_override"] if row["price_override"] is not None else product["base_price"]
    return VariantMatch(
        variant_id=row["id"],
        product_id=row["product_id"],
        product_name=product["name"],
        brand=product["brand"],
        category=product["category"],
        color=row["color"],
        size=row["size"],
        stock_quantity=row["stock_quantity"],
        price=price,
    )


def _to_order_with_items(row: dict[str, Any]) -> OrderWithItems:
    items = []
    for item in row.pop("order_items", None) or []:
        variant = item.pop("product_variants", None) or {}
        product = variant.get("products") or {}
        items.append(OrderItemDetail(
            **item,
            product_name=product.get("name"),
            color=variant.get("color"),
            size=variant.get("size"),
        ))
    return OrderWithItems(**row, items=items)


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------

class SupabaseService:
    def __init__(self, db: AsyncClient, http_client: httpx.AsyncClient | None = None):
        self._db = db
        self._http = http_client

    @classmethod
    async def connect(cls, url: str, service_role_key: str) -> "SupabaseService":
        """Create the client once, at app startup."""
        # Our own HTTP client, so we control the timeout.
        http = httpx.AsyncClient(timeout=_DB_TIMEOUT_SECONDS)
        db = await acreate_client(url, service_role_key, AsyncClientOptions(httpx_client=http))
        return cls(db, http)

    async def close(self) -> None:
        """Close network connections, at app shutdown."""
        if self._http is not None:
            await self._http.aclose()

    async def _run(self, request) -> Any:
        """Execute a request and turn any failure into one of our errors."""
        try:
            response = await request.execute()
        except APIError as error:
            raise _translate(error) from error
        except httpx.HTTPError as error:
            raise DatabaseUnavailableError("database_unavailable", type(error).__name__) from error
        return response.data

    # --- Stores -------------------------------------------------------------

    async def get_store(self, store_id: UUID) -> Store | None:
        """The store, or None if it doesn't exist or is switched off."""
        rows = await self._run(
            self._db.table("stores")
            .select("*")
            .eq("id", str(store_id))
            .eq("is_active", True)
            .limit(1)
        )
        return Store.model_validate(rows[0]) if rows else None

    async def find_store_by_name(self, name: str) -> list[Store]:
        """Active stores with exactly this name (for setup scripts)."""
        rows = await self._run(
            self._db.table("stores").select("*").eq("name", name).eq("is_active", True)
        )
        return [Store.model_validate(row) for row in rows]

    async def set_webhook_secret(self, store_id: UUID, secret: str) -> None:
        """Save the secret Telegram must send with every webhook call."""
        rows = await self._run(
            self._db.table("stores").update({"webhook_secret": secret}).eq("id", str(store_id))
        )
        if not rows:
            raise NotFoundError("store_not_found", str(store_id))

    # --- Products -----------------------------------------------------------

    async def search_variants(
        self,
        store_id: UUID,
        query: str | None = None,
        color: str | None = None,
        size: str | None = None,
        limit: int = 20,
    ) -> list[VariantMatch]:
        """Variants of this store matching the search, most stock first.

        Every word of `query` must appear in the product's name, brand, or
        category (case-insensitive). `color` matches part of the color
        ("white" finds "White/Black"); `size` must match exactly.
        Sold-out variants are included, so the AI can say "sold out in 42,
        but we have 43".
        """
        request = (
            self._db.table("product_variants")
            .select(_VARIANT_COLUMNS)
            .eq("store_id", str(store_id))
        )
        for word in _search_words(query):
            request = request.or_(
                f'name.ilike."*{word}*",brand.ilike."*{word}*",category.ilike."*{word}*"',
                reference_table="products",
            )
        color_words = _search_words(color)
        if color_words:
            request = request.ilike("color", f"*{' '.join(color_words)}*")
        if size and size.strip():
            request = request.eq("size", size.strip())

        rows = await self._run(request.order("stock_quantity", desc=True).limit(limit))
        return [_to_variant_match(row) for row in rows]

    # --- Customers ----------------------------------------------------------

    async def get_or_create_customer(
        self, store_id: UUID, telegram_id: int, name: str | None = None
    ) -> Customer:
        existing = await self._find_customer(store_id, telegram_id)
        if existing:
            return existing
        try:
            rows = await self._run(
                self._db.table("customers").insert(
                    {"store_id": str(store_id), "telegram_id": telegram_id, "name": name}
                )
            )
            return Customer.model_validate(rows[0])
        except DuplicateError:
            # Two messages from a new customer arrived at the same moment and
            # the other one created the row first.
            customer = await self._find_customer(store_id, telegram_id)
            if customer is None:
                raise
            return customer

    async def _find_customer(self, store_id: UUID, telegram_id: int) -> Customer | None:
        rows = await self._run(
            self._db.table("customers")
            .select("*")
            .eq("store_id", str(store_id))
            .eq("telegram_id", telegram_id)
            .limit(1)
        )
        return Customer.model_validate(rows[0]) if rows else None

    async def update_customer(
        self,
        store_id: UUID,
        customer_id: UUID,
        *,
        name: str | None = None,
        phone: str | None = None,
        address: str | None = None,
    ) -> Customer:
        """Update the given fields; fields left as None are not changed."""
        changes: dict[str, str] = {}
        if name is not None:
            changes["name"] = name.strip()
        if phone is not None:
            changes["phone"] = normalize_phone(phone)
        if address is not None:
            changes["address"] = address.strip()

        table = self._db.table("customers")
        request = table.update(changes) if changes else table.select("*")
        rows = await self._run(
            request.eq("id", str(customer_id)).eq("store_id", str(store_id))
        )
        if not rows:
            raise NotFoundError("customer_not_found", str(customer_id))
        return Customer.model_validate(rows[0])

    # --- Orders -------------------------------------------------------------

    async def create_order(
        self,
        store_id: UUID,
        customer_id: UUID,
        draft: OrderDraft,
        idempotency_key: str,
    ) -> UUID:
        """Place the order through the place_order database function.

        Prices come from the database. Stock is checked but not reduced (D3).
        Calling again with the same idempotency_key returns the same order.
        Checking that the customer confirmed is the caller's job (the
        confirm_order tool); this only checks the draft is complete.
        """
        missing = draft.missing_fields()
        if missing:
            raise OrderRejectedError("incomplete_order", ", ".join(missing))

        order_id = await self._run(self._db.rpc("place_order", {
            "p_store_id": str(store_id),
            "p_customer_id": str(customer_id),
            "p_items": [
                {"variant_id": str(item.variant_id), "quantity": item.quantity}
                for item in draft.items
            ],
            "p_fulfillment_method": draft.fulfillment_method,
            "p_contact_name": draft.contact_name,
            "p_contact_phone": draft.contact_phone,
            "p_delivery_address": draft.delivery_address,
            "p_idempotency_key": idempotency_key,
        }))
        logger.info("order placed", extra={"store_id": str(store_id), "order_id": order_id})
        return UUID(order_id)

    async def get_customer_orders(
        self, store_id: UUID, customer_id: UUID, limit: int = 5
    ) -> list[OrderWithItems]:
        """This customer's most recent orders, newest first, with their items."""
        rows = await self._run(
            self._db.table("orders")
            .select(
                "*, order_items(id, order_id, variant_id, quantity, price, "
                "product_variants(color, size, products(name)))"
            )
            .eq("store_id", str(store_id))
            .eq("customer_id", str(customer_id))
            .order("created_at", desc=True)
            .limit(limit)
        )
        return [_to_order_with_items(row) for row in rows]

    # --- Stock and payments -------------------------------------------------

    async def update_stock(self, store_id: UUID, variant_id: UUID, delta: int) -> int:
        """Add (positive) or remove (negative) stock. Returns the new level.

        Raises OutOfStockError instead of going below zero.
        """
        return await self._run(self._db.rpc("adjust_stock", {
            "p_store_id": str(store_id),
            "p_variant_id": str(variant_id),
            "p_delta": delta,
        }))

    async def record_payment(
        self,
        store_id: UUID,
        order_id: UUID,
        amount: Decimal,
        method: str | None,
        staff_id: UUID | None,
    ) -> UUID:
        """Staff confirmed a payment: reduce stock, save payment, mark paid.

        All in one step (confirm_payment). If an item sold out in the
        meantime, nothing changes and OutOfStockError says which variant.
        """
        payment_id = await self._run(self._db.rpc("confirm_payment", {
            "p_store_id": str(store_id),
            "p_order_id": str(order_id),
            "p_amount": str(amount),  # as text so no precision is lost
            "p_method": method,
            "p_staff_id": str(staff_id) if staff_id else None,
        }))
        logger.info("payment recorded", extra={"store_id": str(store_id), "order_id": str(order_id)})
        return UUID(payment_id)

    # --- Inbox (migration 003) ----------------------------------------------

    async def save_to_inbox(
        self, store_id: UUID, update_id: int, telegram_id: int, payload: dict[str, Any]
    ) -> bool:
        """Save an incoming update. Returns False if it was already saved
        (Telegram resent it), True if it's new."""
        rows = await self._run(
            self._db.table("inbox").upsert(
                {
                    "store_id": str(store_id),
                    "update_id": update_id,
                    "telegram_id": telegram_id,
                    "payload": payload,
                },
                on_conflict="store_id,update_id",
                ignore_duplicates=True,  # an existing row is left alone and not returned
            )
        )
        return bool(rows)

    async def has_waiting_inbox(self, store_id: UUID, telegram_id: int) -> bool:
        """True if this customer has updates still waiting to be handled."""
        rows = await self._run(
            self._db.table("inbox")
            .select("id")
            .eq("store_id", str(store_id))
            .eq("telegram_id", telegram_id)
            .eq("status", "received")
            .limit(1)
        )
        return bool(rows)

    async def claim_inbox(self, store_id: UUID, telegram_id: int) -> list[InboxItem]:
        """Mark this customer's waiting updates as processing and return
        them, oldest first. Each claim counts as one attempt."""
        rows = await self._run(self._db.rpc("claim_inbox", {
            "p_store_id": str(store_id),
            "p_telegram_id": telegram_id,
        }))
        return sorted((InboxItem.model_validate(row) for row in rows or []),
                      key=lambda item: item.update_id)

    async def finish_inbox(self, store_id: UUID, ids: list[int]) -> None:
        """Handling succeeded: mark these updates done."""
        if not ids:
            return
        await self._run(
            self._db.table("inbox")
            .update({"status": "done", "finished_at": _now().isoformat(), "last_error": None})
            .eq("store_id", str(store_id))
            .in_("id", ids)
        )

    async def release_inbox(
        self, store_id: UUID, ids: list[int], error: str, max_attempts: int
    ) -> list[InboxItem]:
        """Handling failed. Updates tried max_attempts times become 'failed';
        the rest go back to 'received' for the recovery sweep to retry."""
        if not ids:
            return []
        rows = await self._run(self._db.rpc("release_inbox", {
            "p_store_id": str(store_id),
            "p_ids": ids,
            "p_error": error,
            "p_max_attempts": max_attempts,
        }))
        return [InboxItem.model_validate(row) for row in rows or []]

    # The two recovery functions below look across ALL stores on purpose:
    # they only find work, and the work itself is then done per store.

    async def reset_stuck_inbox(self, claimed_before: datetime) -> int:
        """Updates stuck in 'processing' since before `claimed_before` (the
        server crashed or restarted mid-run) go back to 'received'.
        Returns how many were reset."""
        rows = await self._run(
            self._db.table("inbox")
            .update({"status": "received"})
            .eq("status", "processing")
            .lt("claimed_at", claimed_before.isoformat())
        )
        return len(rows or [])

    async def find_waiting_inbox(
        self, received_before: datetime, limit: int = 200
    ) -> list[tuple[UUID, int]]:
        """(store_id, telegram_id) of customers with updates still waiting
        since before `received_before`, oldest first, without repeats."""
        rows = await self._run(
            self._db.table("inbox")
            .select("store_id, telegram_id")
            .eq("status", "received")
            .lt("received_at", received_before.isoformat())
            .order("received_at")
            .limit(limit)
        )
        customers = {(UUID(row["store_id"]), row["telegram_id"]): None for row in rows or []}
        return list(customers)

    # --- Conversations (migration 003) --------------------------------------

    async def get_or_create_conversation(self, store_id: UUID, telegram_id: int) -> Conversation:
        """This customer's conversation with the store, created if new.
        The customer must already exist in this store."""
        await self._run(
            self._db.table("conversations").upsert(
                {"store_id": str(store_id), "telegram_id": telegram_id},
                on_conflict="store_id,telegram_id",
                ignore_duplicates=True,
            )
        )
        conversation = await self.get_conversation(store_id, telegram_id)
        if conversation is None:  # deleted in the split second between the two calls
            raise NotFoundError("conversation_not_found", str(telegram_id))
        return conversation

    async def get_conversation(self, store_id: UUID, telegram_id: int) -> Conversation | None:
        rows = await self._run(
            self._db.table("conversations")
            .select("*")
            .eq("store_id", str(store_id))
            .eq("telegram_id", telegram_id)
            .limit(1)
        )
        return Conversation.model_validate(rows[0]) if rows else None

    async def save_conversation(self, conversation: Conversation) -> Conversation:
        """Save the order draft and last_message_at, only if nobody saved
        since this copy was loaded. Returns the saved copy (version + 1).

        Raises VersionConflictError otherwise: load it again and redo the work.
        Pausing the bot is not saved here; staff do that (Phase 9).
        """
        rows = await self._run(
            self._db.table("conversations")
            .update({
                "order_draft": conversation.order_draft.model_dump(mode="json"),
                "last_message_at": _iso(conversation.last_message_at),
                "version": conversation.version + 1,
                "updated_at": _now().isoformat(),
            })
            .eq("id", str(conversation.id))
            .eq("store_id", str(conversation.store_id))
            .eq("version", conversation.version)  # the version check
        )
        if not rows:
            raise VersionConflictError("version_conflict", str(conversation.id))
        return Conversation.model_validate(rows[0])

    # --- Messages (migration 003) -------------------------------------------

    async def add_messages(
        self, store_id: UUID, conversation_id: UUID, messages: list[ChatMessage]
    ) -> None:
        """Add messages to the history. A customer message whose update_id
        is already saved is skipped, so retrying never saves it twice."""
        if not messages:
            return
        rows = [
            {
                "store_id": str(store_id),
                "conversation_id": str(conversation_id),
                **message.model_dump(mode="json", exclude={"created_at"}),
            }
            for message in messages
        ]
        await self._run(
            self._db.table("messages").upsert(
                rows, on_conflict="store_id,update_id", ignore_duplicates=True
            )
        )

    async def get_recent_messages(
        self, store_id: UUID, conversation_id: UUID, limit: int, since: datetime | None = None
    ) -> list[ChatMessage]:
        """The last `limit` messages (sent after `since`), oldest first."""
        request = (
            self._db.table("messages")
            .select("role, kind, content, update_id, telegram_message_id, created_at")
            .eq("store_id", str(store_id))
            .eq("conversation_id", str(conversation_id))
        )
        if since is not None:
            request = request.gte("created_at", since.isoformat())
        rows = await self._run(request.order("id", desc=True).limit(limit))
        return [ChatMessage.model_validate(row) for row in reversed(rows or [])]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
