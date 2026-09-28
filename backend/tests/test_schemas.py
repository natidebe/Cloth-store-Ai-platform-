from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.schemas import (
    AgentDecision,
    DraftItem,
    IncomingMessage,
    Order,
    OrderDraft,
    OrderItem,
    Payment,
    ProductVariant,
    Store,
    TelegramUpdate,
)

STORE_ID = uuid4()


# --- Database rows ----------------------------------------------------------

def test_order_row_from_supabase():
    # Supabase returns numeric columns as JSON numbers and uuids as strings.
    order = Order.model_validate({
        "id": str(uuid4()), "store_id": str(STORE_ID), "customer_id": None,
        "status": "pending", "payment_status": "unpaid", "total_price": 5000.50,
        "currency": "ETB", "fulfillment_method": "delivery",
        "created_at": "2026-09-26T10:00:00+00:00", "some_future_column": "ignored",
    })
    assert order.total_price == Decimal("5000.5")
    assert order.fulfillment_method == "delivery"


@pytest.mark.parametrize("status", ["shipped", "completed", "PENDING", ""])
def test_order_rejects_unknown_status(status):
    with pytest.raises(ValidationError):
        Order(id=uuid4(), store_id=STORE_ID, status=status)


def test_order_rejects_unknown_payment_status():
    with pytest.raises(ValidationError):
        Order(id=uuid4(), store_id=STORE_ID, payment_status="pending_verification")


def test_variant_stock_cannot_be_negative():
    with pytest.raises(ValidationError):
        ProductVariant(id=uuid4(), product_id=uuid4(), store_id=STORE_ID, stock_quantity=-1)


def test_order_item_and_payment_limits():
    with pytest.raises(ValidationError):
        OrderItem(id=uuid4(), order_id=uuid4(), quantity=0, price=100)
    with pytest.raises(ValidationError):
        Payment(id=uuid4(), order_id=uuid4(), amount=0)
    # Payment method is free text (D6).
    assert Payment(id=uuid4(), order_id=uuid4(), amount=100, method="Telebirr").method == "Telebirr"


def test_store_secrets_are_hidden_when_printed():
    store = Store(id=STORE_ID, name="Selam Shoes", telegram_bot_token="123:ABC", webhook_secret="s3cret")
    assert "123:ABC" not in repr(store)
    assert "s3cret" not in str(store.model_dump())
    assert store.telegram_bot_token.get_secret_value() == "123:ABC"


# --- Telegram ---------------------------------------------------------------

TEXT_UPDATE = {
    "update_id": 1001,
    "message": {
        "message_id": 55,
        "date": 1790000000,
        "chat": {"id": 42, "type": "private", "first_name": "Abebe"},
        "from": {"id": 42, "is_bot": False, "first_name": "Abebe", "last_name": "Kebede",
                 "language_code": "am"},
        "text": "Do you have white AF1 in 42?",
    },
}

PHOTO_UPDATE = {
    "update_id": 1002,
    "message": {
        "message_id": 56,
        "date": 1790000060,
        "chat": {"id": 42, "type": "private"},
        "from": {"id": 42, "is_bot": False, "first_name": "Abebe"},
        "caption": "payment",
        "photo": [
            {"file_id": "small", "file_unique_id": "u1", "width": 90, "height": 90},
            {"file_id": "large", "file_unique_id": "u2", "width": 1280, "height": 1280},
        ],
    },
}


def test_parse_text_update():
    update = TelegramUpdate.model_validate(TEXT_UPDATE)
    msg = update.message
    assert msg.text == "Do you have white AF1 in 42?"
    assert msg.from_user.id == 42  # "from" in JSON becomes from_user
    assert msg.from_user.full_name == "Abebe Kebede"
    assert msg.date.year == 2026


def test_parse_photo_update():
    msg = TelegramUpdate.model_validate(PHOTO_UPDATE).message
    assert [p.file_id for p in msg.photo] == ["small", "large"]
    assert msg.caption == "payment"


def test_update_without_update_id_is_rejected():
    with pytest.raises(ValidationError):
        TelegramUpdate.model_validate({"message": TEXT_UPDATE["message"]})


# --- IncomingMessage --------------------------------------------------------

def _incoming(**overrides):
    data = dict(store_id=STORE_ID, update_id=1, chat_id=42, message_id=5, telegram_id=42,
                kind="text", text="hi", sent_at="2026-09-26T10:00:00Z")
    data.update(overrides)
    return IncomingMessage(**data)


def test_incoming_message_valid():
    assert _incoming().kind == "text"
    assert _incoming(kind="photo", text=None, photo_file_id="large").photo_file_id == "large"
    assert _incoming(kind="sticker", text=None).kind == "sticker"


def test_incoming_message_kind_must_match_content():
    with pytest.raises(ValidationError):
        _incoming(kind="text", text=None)
    with pytest.raises(ValidationError):
        _incoming(kind="photo", photo_file_id=None)
    with pytest.raises(ValidationError):
        _incoming(kind="video")


# --- OrderDraft -------------------------------------------------------------

def _item(**overrides):
    data = dict(variant_id=uuid4(), quantity=1, description="Air Force 1, white, 42")
    data.update(overrides)
    return DraftItem(**data)


def test_empty_draft_reports_all_required_fields():
    assert OrderDraft().missing_fields() == [
        "items", "contact_name", "contact_phone", "fulfillment_method",
    ]


def test_delivery_needs_address_pickup_does_not():
    draft = OrderDraft(items=[_item()], contact_name="Abebe", contact_phone="0911223344",
                       fulfillment_method="delivery")
    assert draft.missing_fields() == ["delivery_address"]
    draft.fulfillment_method = "pickup"
    assert OrderDraft.model_validate(draft.model_dump()).missing_fields() == []


def test_draft_is_ready_only_after_confirmation():
    draft = OrderDraft(items=[_item(), _item(quantity=2)], contact_name="Abebe",
                       contact_phone="0911223344", fulfillment_method="delivery",
                       delivery_address="Bole, Addis Ababa")
    assert draft.missing_fields() == []
    assert not draft.is_ready
    draft.customer_confirmed = True
    assert draft.is_ready


def test_blank_text_counts_as_missing():
    draft = OrderDraft(contact_name="   ", delivery_address="", contact_phone=" ")
    assert draft.contact_name is None
    assert draft.delivery_address is None
    assert "contact_name" in draft.missing_fields()


@pytest.mark.parametrize("raw, saved", [
    ("0911223344", "0911223344"),
    ("+251 91-122 3344", "+251911223344"),
    ("(011) 555.1234", "0115551234"),
    ("+14155550123", "+14155550123"),  # foreign numbers are fine
])
def test_phone_accepted_and_cleaned(raw, saved):
    assert OrderDraft(contact_phone=raw).contact_phone == saved


@pytest.mark.parametrize("raw", ["12345", "call me", "0911-22-33-44-55-66-77", "++251911223344"])
def test_bad_phone_rejected(raw):
    with pytest.raises(ValidationError):
        OrderDraft(contact_phone=raw)


def test_draft_item_rules():
    with pytest.raises(ValidationError):
        _item(quantity=0)
    with pytest.raises(ValidationError):
        _item(description="")
    with pytest.raises(ValidationError):
        _item(variant_id="not-a-uuid")


# --- AgentDecision ----------------------------------------------------------

def test_agent_decision_rules():
    assert AgentDecision(action="reply", reply_text="Yes, we have it!").action == "reply"
    assert AgentDecision(action="stay_silent").reply_text is None
    AgentDecision(action="escalate", reply_text="Connecting you with our team.",
                  escalation_reason="discount request")
    with pytest.raises(ValidationError):
        AgentDecision(action="reply")
    with pytest.raises(ValidationError):
        AgentDecision(action="escalate", reply_text="Connecting you.")


# --- Store profile (migration 004) ------------------------------------------

def test_store_profile_filled_and_missing():
    store = Store(id=STORE_ID, name="Selam Shoes", opening_hours="Mon–Sat 8:30–19:00",
                  payment_instructions="Telebirr 0911 000 000", location="   ")
    profile = store.profile
    assert profile.filled() == {"opening_hours": "Mon–Sat 8:30–19:00",
                                "payment_instructions": "Telebirr 0911 000 000"}
    # Blank text counts as not set, so the AI knows not to guess it.
    assert profile.missing() == ["location", "delivery_info", "pickup_instructions", "return_policy"]


def test_store_profile_has_no_secrets():
    store = Store(id=STORE_ID, name="Selam Shoes", telegram_bot_token="123:ABC",
                  webhook_secret="s3cret", opening_hours="9–5")
    dumped = str(store.profile.model_dump())
    assert "123:ABC" not in dumped and "s3cret" not in dumped
