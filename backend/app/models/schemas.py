"""Pydantic models: the shape of every piece of data moving through the app.

Three groups:
1. Database rows — mirror the tables after migration 002. The rules match
   the database's own checks, so bad data is caught in Python first.
2. Telegram — only the parts of an incoming update we use.
3. Internal — IncomingMessage, OrderDraft, AgentDecision.

Money is `Decimal`, never `float`, so prices and totals are exact.
"""
import re
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

# Allowed values — must match the check constraints in 002 (decisions D4, D7, D11).
OrderStatus = Literal["pending", "confirmed", "out_for_delivery", "delivered", "cancelled"]
PaymentStatus = Literal["unpaid", "paid", "refunded"]
FulfillmentMethod = Literal["delivery", "pickup"]
StaffRole = Literal["owner", "staff"]


# ---------------------------------------------------------------------------
# 1. Database rows
# ---------------------------------------------------------------------------

class DbModel(BaseModel):
    # Ignore columns we don't model yet, so adding a column doesn't break us.
    model_config = ConfigDict(extra="ignore")


class Store(DbModel):
    id: UUID
    name: str
    plan: str | None = None
    staff_chat_id: int | None = None
    is_active: bool = True
    # Only the backend can read these (service_role). SecretStr hides them in logs.
    telegram_bot_token: SecretStr | None = None
    webhook_secret: SecretStr | None = None
    created_at: datetime | None = None


class StoreStaff(DbModel):
    id: UUID
    store_id: UUID
    user_id: UUID
    role: StaffRole = "staff"
    created_at: datetime | None = None


class Product(DbModel):
    id: UUID
    store_id: UUID
    name: str
    brand: str | None = None
    category: str | None = None
    base_price: Decimal | None = Field(default=None, ge=0)
    created_at: datetime | None = None


class ProductVariant(DbModel):
    id: UUID
    product_id: UUID
    store_id: UUID
    color: str | None = None
    size: str | None = None
    sku: str | None = None
    stock_quantity: int = Field(default=0, ge=0)
    # Internal only — never shown to customers or sent to the AI.
    cost_price: Decimal | None = Field(default=None, ge=0)
    price_override: Decimal | None = Field(default=None, ge=0)
    created_at: datetime | None = None


class Customer(DbModel):
    id: UUID
    store_id: UUID
    telegram_id: int | None = None
    name: str | None = None
    phone: str | None = None
    address: str | None = None
    created_at: datetime | None = None


class Order(DbModel):
    id: UUID
    store_id: UUID
    customer_id: UUID | None = None
    status: OrderStatus = "pending"
    payment_status: PaymentStatus = "unpaid"
    total_price: Decimal | None = Field(default=None, ge=0)
    currency: str = "ETB"
    # Empty only on orders created before migration 002.
    fulfillment_method: FulfillmentMethod | None = None
    contact_name: str | None = None
    contact_phone: str | None = None
    delivery_address: str | None = None
    idempotency_key: str | None = None
    created_at: datetime | None = None


class OrderItem(DbModel):
    id: UUID
    order_id: UUID
    variant_id: UUID | None = None
    quantity: int = Field(ge=1)
    price: Decimal = Field(ge=0)  # unit price at the time of the order


class Payment(DbModel):
    id: UUID
    order_id: UUID
    amount: Decimal = Field(gt=0)
    method: str | None = None  # free text, up to the store (D6)
    paid_at: datetime | None = None
    confirmed_by: UUID | None = None  # the staff member's user id


# ---------------------------------------------------------------------------
# 2. Telegram update (only the fields we use)
# https://core.telegram.org/bots/api#update
# ---------------------------------------------------------------------------

class TelegramModel(BaseModel):
    # Telegram sends many more fields; ignore them.
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class TelegramUser(TelegramModel):
    id: int
    is_bot: bool = False
    first_name: str
    last_name: str | None = None
    username: str | None = None
    language_code: str | None = None

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name or ''}".strip()


class TelegramChat(TelegramModel):
    id: int
    type: Literal["private", "group", "supergroup", "channel"]


class TelegramPhotoSize(TelegramModel):
    file_id: str
    file_unique_id: str
    width: int
    height: int


class TelegramFile(TelegramModel):
    """Stickers, voice notes, documents: we only need to know one arrived."""
    file_id: str
    file_unique_id: str


class TelegramMessage(TelegramModel):
    message_id: int
    date: datetime  # Telegram sends a Unix timestamp; Pydantic converts it
    chat: TelegramChat
    # "from" is a Python keyword, so the field is called from_user.
    from_user: TelegramUser | None = Field(default=None, alias="from")
    text: str | None = None
    caption: str | None = None  # text sent together with a photo
    photo: list[TelegramPhotoSize] | None = None  # same photo in several sizes
    sticker: TelegramFile | None = None
    voice: TelegramFile | None = None
    document: TelegramFile | None = None


class TelegramUpdate(TelegramModel):
    update_id: int
    message: TelegramMessage | None = None
    edited_message: TelegramMessage | None = None


# ---------------------------------------------------------------------------
# 3. Internal models
# ---------------------------------------------------------------------------

MessageKind = Literal["text", "photo", "sticker", "voice", "document", "other"]


class IncomingMessage(BaseModel):
    """One customer message, cleaned up from a TelegramUpdate.

    store_id always comes from the webhook URL, never from the message.
    """
    store_id: UUID
    update_id: int
    chat_id: int
    message_id: int
    telegram_id: int  # the customer's Telegram user id
    customer_name: str | None = None
    language_code: str | None = None
    kind: MessageKind
    text: str | None = None  # the text, or a photo's caption
    photo_file_id: str | None = None  # largest size, when kind == "photo"
    sent_at: datetime

    @model_validator(mode="after")
    def _kind_matches_content(self) -> "IncomingMessage":
        if self.kind == "text" and not self.text:
            raise ValueError("a text message needs text")
        if self.kind == "photo" and not self.photo_file_id:
            raise ValueError("a photo message needs photo_file_id")
        return self


# Any phone number: optional +, then 7-15 digits. Spaces, dashes, dots and
# brackets are removed first, so "+251 91-123 4567" is accepted.
_PHONE_SEPARATORS = re.compile(r"[\s\-().]")
_PHONE_PATTERN = re.compile(r"^\+?\d{7,15}$")


def normalize_phone(value: str) -> str:
    cleaned = _PHONE_SEPARATORS.sub("", value)
    if not _PHONE_PATTERN.fullmatch(cleaned):
        raise ValueError("phone must be 7 to 15 digits, optionally starting with +")
    return cleaned


class DraftItem(BaseModel):
    """One line of an order being collected."""
    variant_id: UUID  # a specific variant found by check_stock
    quantity: int = Field(default=1, ge=1)
    # Human-readable, for the order summary, e.g. "Air Force 1, white, 42".
    # Prices are NOT stored here: they're always read from the database.
    description: str = Field(min_length=1)


class OrderDraft(BaseModel):
    """An order still being filled in during the conversation.

    Every field starts empty. The AI fills them in as the customer answers,
    and the order can be placed only when missing_fields() is empty and the
    customer has confirmed.
    """
    items: list[DraftItem] = Field(default_factory=list)
    contact_name: str | None = None
    contact_phone: str | None = None
    fulfillment_method: FulfillmentMethod | None = None
    delivery_address: str | None = None
    customer_confirmed: bool = False

    @field_validator("contact_name", "delivery_address")
    @classmethod
    def _strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("contact_phone")
    @classmethod
    def _valid_phone(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return normalize_phone(value)

    def missing_fields(self) -> list[str]:
        """Names of the required fields that are still empty."""
        missing = []
        if not self.items:
            missing.append("items")
        if not self.contact_name:
            missing.append("contact_name")
        if not self.contact_phone:
            missing.append("contact_phone")
        if not self.fulfillment_method:
            missing.append("fulfillment_method")
        if self.fulfillment_method == "delivery" and not self.delivery_address:
            missing.append("delivery_address")
        return missing

    @property
    def is_ready(self) -> bool:
        """All details present AND the customer said yes to the summary."""
        return not self.missing_fields() and self.customer_confirmed


class AgentDecision(BaseModel):
    """What the agent decided to do with one customer message."""
    action: Literal["reply", "escalate", "stay_silent"]
    reply_text: str | None = None  # sent to the customer
    order_id: UUID | None = None  # set when an order was placed during this run
    escalation_reason: str | None = None

    @model_validator(mode="after")
    def _required_fields(self) -> "AgentDecision":
        if self.action in ("reply", "escalate") and not self.reply_text:
            raise ValueError(f"action '{self.action}' needs reply_text")
        if self.action == "escalate" and not self.escalation_reason:
            raise ValueError("action 'escalate' needs escalation_reason")
        return self
