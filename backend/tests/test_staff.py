"""Phase 9: the staff group (buttons, staff replies, hand-back) and the
dashboard endpoints. No network: Telegram, the AI and the database are faked
(the same fakes as test_agent.py)."""
from datetime import timedelta
from itertools import count
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.agents.messages import t
from app.agents.staff import BOT_RESUMES_AFTER
from app.agents.tools import order_number
from app.api.v1.webhook import SECRET_HEADER, get_db, get_orchestrator
from app.main import app
from app.models.schemas import TelegramUpdate
from app.services.conversation_service import utc_now
from tests.test_agent import (
    AF1_WHITE_42,
    CUSTOMER,
    STAFF_CHAT,
    STORE,
    World,
    _up_to_summary,
    call,
    text,
)

pytestmark = pytest.mark.anyio
_update_ids = count(9000)
STAFF_MEMBER = 777


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _staff_update(**fields) -> TelegramUpdate:
    return TelegramUpdate.model_validate({"update_id": next(_update_ids), **fields})


def _press(data, message_id, chat=STAFF_CHAT, who="Sara"):
    return _staff_update(callback_query={
        "id": f"q{next(_update_ids)}", "from": {"id": STAFF_MEMBER, "first_name": who},
        "message": {"message_id": message_id, "date": 1790000000,
                    "chat": {"id": chat, "type": "supergroup"}},
        "data": data,
    })


def _reply(text_, reply_to, chat=STAFF_CHAT, who="Sara"):
    return _staff_update(message={
        "message_id": next(_update_ids), "date": 1790000000,
        "chat": {"id": chat, "type": "supergroup"},
        "from": {"id": STAFF_MEMBER, "is_bot": False, "first_name": who},
        "text": text_,
        "reply_to_message": {"message_id": reply_to, "date": 1790000000,
                             "chat": {"id": chat, "type": "supergroup"}},
    })


async def _staff(world: World, update: TelegramUpdate):
    await world.orchestrator.staff.handle(STORE, update)


def _button_answers(world: World):
    """(text, popup) of every answer to a button press."""
    return [(body["text"], body["show_alert"]) for method, body, _ in world.telegram.calls
            if method == "answerCallbackQuery"]


async def _placed_order(world: World):
    await _up_to_summary(world)
    world.script(call("confirm_order"), text("Thank you!"))
    await world.say("yes")
    [order] = world.db.orders.values()
    return order


# --- The payment screenshot ---------------------------------------------------

async def test_screenshot_reaches_staff_as_a_photo_with_buttons():
    world = World()
    order = await _placed_order(world)
    await world.send_photo(caption="paid")

    method, body, message_id = next(c for c in reversed(world.telegram.calls) if c[0] == "sendPhoto")
    assert body["chat_id"] == STAFF_CHAT
    assert body["photo"] == "screenshot"  # the customer's own photo, not just a text alert
    assert f"#{order_number(order.id)}" in body["caption"] and "paid" in body["caption"]
    assert world.telegram.buttons(message_id) == [
        (f"✅ Confirm payment #{order_number(order.id)}", f"pay:{order.id}"),
        ("▶️ Hand back to bot", f"resume:{CUSTOMER}"),
    ]
    assert world.conversation.bot_paused


async def test_confirm_payment_button():
    world = World()
    order = await _placed_order(world)
    await world.send_photo()
    alert_id = world.telegram.last_message_id(STAFF_CHAT)

    await _staff(world, _press(f"pay:{order.id}", alert_id))

    paid = await world.db.get_order(STORE.id, order.id)
    assert paid.payment_status == "paid"
    assert world.db.variants[AF1_WHITE_42].stock_quantity == 1  # stock goes down at payment (D3)
    assert world.db.payments[-1]["confirmed_by"] == (STAFF_MEMBER, "Sara")
    # The customer is told (now it's true: staff checked it)...
    assert world.telegram.to(CUSTOMER)[-1] == t("payment_confirmed", "en", number=order_number(order.id))
    # ...the bot answers them again, and staff see who confirmed it.
    assert not world.conversation.bot_paused
    assert "Confirmed by Sara" in world.telegram.to(STAFF_CHAT)[-1]
    assert ("editMessageReplyMarkup" in [c[0] for c in world.telegram.calls])  # buttons removed
    assert _button_answers(world)[-1][1] is False  # a short notice, not a popup

    # Pressing again (e.g. an old alert) changes nothing.
    await _staff(world, _press(f"pay:{order.id}", alert_id))
    assert _button_answers(world)[-1] == (f"Order #{order_number(order.id)} is already paid.", True)
    assert len(world.db.payments) == 1


async def test_sold_out_payment_is_refused_and_nothing_changes():
    world = World()
    order = await _placed_order(world)
    world.db.variants[AF1_WHITE_42] = world.db.variants[AF1_WHITE_42].model_copy(
        update={"stock_quantity": 0, "held": 0})  # sold in the shop meanwhile
    await world.send_photo()
    customer_messages = len(world.telegram.to(CUSTOMER))

    await _staff(world, _press(f"pay:{order.id}", world.telegram.last_message_id(STAFF_CHAT)))

    answer, popup = _button_answers(world)[-1]
    assert popup and "sold out" in answer and "Nothing was changed" in answer
    assert (await world.db.get_order(STORE.id, order.id)).payment_status == "unpaid"
    assert len(world.telegram.to(CUSTOMER)) == customer_messages  # customer not told anything
    assert world.conversation.bot_paused  # staff still have the chat


async def test_amharic_customer_gets_amharic_payment_confirmation():
    world = World()
    world.script(call("update_order_draft", items=[{"variant_id": str(AF1_WHITE_42), "quantity": 1}],
                      contact_name="Abebe", contact_phone="0911223344", fulfillment_method="pickup"),
                 call("confirm_order"), text("ያረጋግጡ።"))
    await world.say("ነጩን ቁጥር 42 እወስዳለሁ፣ አበበ 0911223344 ከሱቁ")
    world.script(call("confirm_order"), text("እናመሰግናለን!"))
    await world.say("yes")
    [order] = world.db.orders.values()
    await world.send_photo()
    await _staff(world, _press(f"pay:{order.id}", world.telegram.last_message_id(STAFF_CHAT)))
    assert world.telegram.to(CUSTOMER)[-1] == t("payment_confirmed", "am", number=order_number(order.id))


# --- Staff replying to a customer ---------------------------------------------

async def test_staff_reply_reaches_the_customer():
    world = World()
    world.script(call("escalate_to_staff", reason="asks for a discount", summary="Wants AF1 cheaper"),
                 text("A team member will reply soon."))
    await world.say("Can I get a discount?")
    alert_id = world.telegram.last_message_id(STAFF_CHAT)

    await _staff(world, _reply("Sorry, prices are fixed, but delivery is free this week!", alert_id))

    assert world.telegram.to(CUSTOMER)[-1] == "Sorry, prices are fixed, but delivery is free this week!"
    assert world.conversation.bot_paused and world.conversation.staff_active_at is not None
    history = world.store.messages[world.conversation.id]
    assert (history[-1][1].role, history[-1][1].content) == (
        "staff", "Sorry, prices are fixed, but delivery is free this week!")


async def test_customer_messages_during_handover_reach_staff_and_can_be_answered():
    world = World()
    world.script(call("escalate_to_staff", reason="complaint", summary="Wrong size delivered"),
                 text("A team member will reply soon."))
    await world.say("You sent the wrong size!")
    calls_before = len(world.llm.requests)

    await world.say("Hello?? Anyone there?")
    assert len(world.llm.requests) == calls_before  # the AI stays out of it
    forwarded_id = world.telegram.last_message_id(STAFF_CHAT)
    assert world.telegram.to(STAFF_CHAT)[-1] == "💬 Abebe (Telegram id 42):\nHello?? Anyone there?"

    await _staff(world, _reply("Yes! We'll exchange it tomorrow.", forwarded_id))
    assert world.telegram.to(CUSTOMER)[-1] == "Yes! We'll exchange it tomorrow."


async def test_replying_to_a_new_order_alert_takes_over_the_chat():
    world = World()
    await _placed_order(world)
    new_order_alert = next(mid for method, body, mid in world.telegram.calls
                           if body.get("chat_id") == STAFF_CHAT and body.get("text", "").startswith("🛒"))
    assert not world.conversation.bot_paused

    await _staff(world, _reply("Hi Abebe, can you come at 5pm?", new_order_alert))

    assert world.telegram.to(CUSTOMER)[-1] == "Hi Abebe, can you come at 5pm?"
    assert world.conversation.bot_paused  # the bot and staff don't both answer
    note_id = world.telegram.last_message_id(STAFF_CHAT)
    assert "took over this chat" in world.telegram.to(STAFF_CHAT)[-1]
    assert world.telegram.buttons(note_id) == [("▶️ Hand back to bot", f"resume:{CUSTOMER}")]


async def test_ordinary_staff_chat_is_ignored():
    world = World()
    await _staff(world, _reply("lunch?", reply_to=123456))  # not a reply to the bot about a customer
    await _staff(world, _staff_update(message={
        "message_id": 5, "date": 1790000000, "chat": {"id": STAFF_CHAT, "type": "supergroup"},
        "from": {"id": STAFF_MEMBER, "first_name": "Sara"}, "text": "good morning"}))
    assert world.telegram.calls == []


# --- Handing back to the bot --------------------------------------------------

async def test_hand_back_button_resumes_the_bot():
    world = World()
    world.script(call("escalate_to_staff", reason="asks for a person", summary="-"),
                 text("A team member will reply soon."))
    await world.say("I want to talk to a person")
    alert_id = world.telegram.last_message_id(STAFF_CHAT)

    await _staff(world, _press(f"resume:{CUSTOMER}", alert_id))
    assert not world.conversation.bot_paused
    assert _button_answers(world)[-1] == ("▶️ The bot is answering this customer again.", False)
    assert "Handed back to the bot by Sara" in world.telegram.to(STAFF_CHAT)[-1]

    world.script(text("How can I help?"))
    await world.say("hi again")
    assert world.telegram.to(CUSTOMER)[-1] == "How can I help?"

    await _staff(world, _press(f"resume:{CUSTOMER}", alert_id))  # pressed twice
    assert _button_answers(world)[-1] == ("The bot was already answering this customer.", False)


async def test_buttons_only_work_in_the_staff_group():
    world = World()
    order = await _placed_order(world)
    await _staff(world, _press(f"pay:{order.id}", 1, chat=-100999))  # another group
    assert _button_answers(world)[-1] == ("This button can't be used here.", False)
    assert (await world.db.get_order(STORE.id, order.id)).payment_status == "unpaid"


async def test_bot_takes_idle_chats_back_after_two_hours():
    world = World()
    world.script(call("escalate_to_staff", reason="complaint", summary="-"), text("Someone will reply."))
    await world.say("This is broken")
    staff = world.orchestrator.staff

    assert await staff.resume_idle() == 0  # just handed over
    world.conversation.paused_at = utc_now() - BOT_RESUMES_AFTER - timedelta(minutes=1)
    world.conversation.staff_active_at = utc_now() - timedelta(minutes=30)  # staff replied recently
    assert await staff.resume_idle() == 0
    assert world.conversation.bot_paused

    world.conversation.staff_active_at = utc_now() - BOT_RESUMES_AFTER - timedelta(minutes=1)
    assert await staff.resume_idle() == 1
    assert not world.conversation.bot_paused
    assert "No staff reply for 2 hours" in world.telegram.to(STAFF_CHAT)[-1]


# --- The webhook sends staff-group updates to the staff desk -------------------

def test_webhook_routes_staff_group_updates():
    world = World()
    app.dependency_overrides[get_db] = lambda: world.db
    app.dependency_overrides[get_orchestrator] = lambda: world.orchestrator
    try:
        client = TestClient(app)
        press = _press(f"resume:{CUSTOMER}", 1).model_dump(mode="json", by_alias=True, exclude_none=True)
        response = client.post(f"/api/v1/webhook/{STORE.id}", json=press, headers={SECRET_HEADER: "s"})
        assert response.status_code == 200
        assert world.store.inbox == {}  # not treated as a customer message
        assert [c[0] for c in world.telegram.calls] == ["answerCallbackQuery"]
    finally:
        app.dependency_overrides.clear()


def test_chatid_command_tells_a_group_its_id():
    world = World()
    app.dependency_overrides[get_db] = lambda: world.db
    app.dependency_overrides[get_orchestrator] = lambda: world.orchestrator
    try:
        client = TestClient(app)
        update = {"update_id": 1, "message": {
            "message_id": 3, "date": 1790000000, "chat": {"id": -100777, "type": "supergroup"},
            "from": {"id": 5, "first_name": "Owner"}, "text": "/chatid@demostore01bot"}}
        assert client.post(f"/api/v1/webhook/{STORE.id}", json=update,
                           headers={SECRET_HEADER: "s"}).status_code == 200
        assert "chat id is -100777" in world.telegram.to(-100777)[0]
        assert world.store.inbox == {}
    finally:
        app.dependency_overrides.clear()


# --- Dashboard endpoints ------------------------------------------------------

@pytest.fixture
def dashboard():
    world = World()
    world.db.staff_logins[(STORE.id, "good-token")] = uuid4()
    app.dependency_overrides[get_db] = lambda: world.db
    app.dependency_overrides[get_orchestrator] = lambda: world.orchestrator
    yield world, TestClient(app)
    app.dependency_overrides.clear()


def _confirm_url(order_id, store_id=STORE.id):
    return f"/api/v1/admin/stores/{store_id}/orders/{order_id}/confirm-payment"


async def test_dashboard_needs_a_staff_login_for_this_store(dashboard):
    world, client = dashboard
    order = await _placed_order(world)
    assert client.post(_confirm_url(order.id)).status_code == 401
    assert client.post(_confirm_url(order.id), headers={"Authorization": "Bearer wrong"}).status_code == 403
    # Staff of another store (valid login, but not for this store):
    other_store = uuid4()
    world.db.staff_logins[(other_store, "other-token")] = uuid4()
    assert client.post(_confirm_url(order.id),
                       headers={"Authorization": "Bearer other-token"}).status_code == 403
    assert (await world.db.get_order(STORE.id, order.id)).payment_status == "unpaid"


async def test_dashboard_confirm_payment_and_hand_back(dashboard):
    world, client = dashboard
    order = await _placed_order(world)
    auth = {"Authorization": "Bearer good-token"}

    response = client.post(_confirm_url(order.id), json={"method": "Telebirr"}, headers=auth)
    assert response.status_code == 200 and response.json()["ok"]
    assert world.db.payments[-1]["method"] == "Telebirr"
    assert world.db.payments[-1]["staff"] == world.db.staff_logins[(STORE.id, "good-token")]

    again = client.post(_confirm_url(order.id), headers=auth)
    assert again.status_code == 409 and "already paid" in again.json()["detail"]

    world.store.pause_bot(STORE.id, CUSTOMER)  # staff had taken over
    response = client.post(f"/api/v1/admin/stores/{STORE.id}/conversations/{CUSTOMER}/hand-back",
                           headers=auth)
    assert response.json()["message"] == "The bot is answering this customer again."
    assert not world.conversation.bot_paused
