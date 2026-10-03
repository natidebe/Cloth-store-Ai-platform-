"""The dashboard in one tap: a "📊 Dashboard" menu button next to the message
box for the store's team (not customers), taken away from someone who left,
and a pinned Dashboard button in the staff group."""
from uuid import UUID

from tests import test_miniapp as mini
from tests.test_miniapp import (  # noqa: F401  (world is a pytest fixture)
    MEMBER,
    PUBLIC_URL,
    STORE_A,
    STRANGER,
    _private,
    _webhook,
    world,
)
from tests.test_onboarding import auth, create, group_message, webhook
from tests.test_onboarding import world as onboarding_world  # noqa: F401


def menus(telegram, chat_id):
    return [b["menu_button"] for _, m, b in telegram.calls
            if m == "setChatMenuButton" and b.get("chat_id") == chat_id]


def test_the_team_gets_the_dashboard_button_and_customers_dont(world):
    _, telegram, client, _, _ = world
    _webhook(client, _private(MEMBER, "/start"))
    [button] = menus(telegram, MEMBER)
    assert button == {"type": "web_app", "text": "📊 Dashboard",
                      "web_app": {"url": f"{PUBLIC_URL}/app/?store={STORE_A.id}"}}
    _webhook(client, _private(MEMBER, "/start"))  # already has it: not set again
    assert len(menus(telegram, MEMBER)) == 1

    _webhook(client, _private(STRANGER, "/start"))  # a customer
    assert menus(telegram, STRANGER) == []


def test_someone_who_left_the_staff_group_loses_the_button(world, monkeypatch):
    _, telegram, client, _, orchestrator = world
    _webhook(client, _private(MEMBER, "/start"))
    assert menus(telegram, MEMBER)[-1]["type"] == "web_app"
    monkeypatch.setitem(mini.STATUS_IN_A, MEMBER, "left")
    orchestrator.app_access._known.clear()  # the remembered answer has expired
    _webhook(client, _private(MEMBER, "/start"))
    assert menus(telegram, MEMBER)[-1] == {"type": "default"}


def test_dashboard_command_also_sets_the_button(world):
    _, telegram, client, _, _ = world
    _webhook(client, _private(MEMBER, "/dashboard"))
    assert menus(telegram, MEMBER)[-1]["type"] == "web_app"
    assert "next to the message box" in telegram.sent(MEMBER)[0]["text"]


def test_the_staff_group_welcome_has_the_button_and_is_pinned(onboarding_world):
    db, telegram, client = onboarding_world
    store_id = UUID(create(client).json()["store_id"])
    code = client.post(f"/api/v1/stores/{store_id}/link-code", headers=auth("owner")).json()["code"]
    webhook(client, db.stores[store_id], group_message(f"/link {code}"))
    welcome = [b for _, m, b in telegram.calls if m == "sendMessage" and b["chat_id"] == -500][-1]
    assert welcome["reply_markup"]["inline_keyboard"][0][0]["url"] == "https://t.me/shop_a_bot?start=dashboard"
    assert any(m == "pinChatMessage" and b == {"chat_id": -500, "message_id": 900, "disable_notification": True}
               for _, m, b in telegram.calls)


def test_the_owner_gets_the_button_when_the_shop_is_created(world):
    _, telegram, client, _, _ = world
    created = client.post("/api/v1/platform-app/stores", headers=mini.platform(MEMBER),
                          json={"name": "Phone World", "bot_token": mini.TOKEN_B[:10] + "Z" * 35})
    assert created.status_code == 201
    [button] = menus(telegram, MEMBER)
    assert button["type"] == "web_app" and created.json()["id"] in button["web_app"]["url"]
