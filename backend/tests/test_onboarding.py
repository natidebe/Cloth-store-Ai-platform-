"""Phase 9b: store onboarding (D14–D17), with a fake database and Telegram.

The same rules as the real database (migration 008): one store per bot, new
stores are pending, link codes are used once and expire, owners can't be
removed. test_onboarding_db.py checks those rules in a real Supabase project.
"""
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.agents.messages import both
from app.agents.onboarding import Onboarding, link_command, new_link_code
from app.agents.orchestrator import Orchestrator
from app.api.v1.stores import get_onboarding
from app.api.v1.webhook import SECRET_HEADER, get_db, get_orchestrator
from app.main import app
from app.models.schemas import AuthUser, Customer, Store, StoreMembership, StoreSummary, TelegramUpdate
from app.services.conversation_service import InMemoryConversationStore
from app.services.supabase_service import DuplicateError
from app.services.telegram_service import TELEGRAM_API, TelegramService
from tests.test_conversation import EchoFlow

TOKEN_A = "111111111:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"  # @shop_a_bot
TOKEN_B = "222222222:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"  # @shop_b_bot
TOKEN_A2 = "111111111:CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC"  # @shop_a_bot, regenerated token
TOKEN_REVOKED = "333333333:DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD"
BOTS = {TOKEN_A: (111111111, "shop_a_bot"), TOKEN_B: (222222222, "shop_b_bot"),
        TOKEN_A2: (111111111, "shop_a_bot")}
PUBLIC_URL = "https://example.ngrok-free.dev"

OWNER, STAFF, ADMIN, STRANGER = (AuthUser(id=uuid4(), email=f"{n}@example.com", email_confirmed=True)
                                 for n in ("owner", "staff", "admin", "stranger"))
LOGINS = {"owner": OWNER, "staff": STAFF, "admin": ADMIN, "stranger": STRANGER}


def auth(who):
    return {"Authorization": f"Bearer {who}"}


class FakeDb:
    """The onboarding part of SupabaseService, with migration 008's rules."""

    def __init__(self):
        self.stores: dict[UUID, Store] = {}
        self.staff: dict[tuple[UUID, UUID], str] = {}
        self.admins = {ADMIN.id}
        self.links: dict[UUID, tuple[str, datetime]] = {}
        self.invites: dict[tuple[UUID, str], UUID] = {}
        self.logins = dict(LOGINS)
        self.inbox = []

    # stores
    async def get_store(self, store_id):
        store = self.stores.get(store_id)
        return store if store and store.status == "active" else None

    async def get_store_any_status(self, store_id):
        return self.stores.get(store_id)

    async def find_store_by_bot(self, bot_id):
        return next((s for s in self.stores.values() if s.telegram_bot_id == bot_id), None)

    async def create_store(self, name, bot_token, bot_id, bot_username, webhook_secret, owner):
        if await self.find_store_by_bot(bot_id):
            raise DuplicateError("duplicate")
        store = Store(id=uuid4(), name=name, telegram_bot_token=bot_token, telegram_bot_id=bot_id,
                      telegram_bot_username=bot_username, webhook_secret=webhook_secret,
                      status="pending", plan="free")
        self.stores[store.id] = store
        self.staff[(store.id, owner)] = "owner"
        return store.id

    async def set_bot(self, store_id, *, bot_id, bot_username, bot_token=None, webhook_secret=None):
        other = await self.find_store_by_bot(bot_id)
        if other and other.id != store_id:
            raise DuplicateError("duplicate")
        update = {"telegram_bot_id": bot_id, "telegram_bot_username": bot_username}
        if bot_token:
            update["telegram_bot_token"] = bot_token
        if webhook_secret:
            update["webhook_secret"] = webhook_secret
        self._update(store_id, **update)

    async def set_store_status(self, store_id, status):
        self._update(store_id, status=status, is_active=status == "active")

    async def set_store_plan(self, store_id, plan):
        self._update(store_id, plan=plan)

    def _update(self, store_id, **fields):
        secret = {"telegram_bot_token", "webhook_secret"}
        self.stores[store_id] = self.stores[store_id].model_copy(
            update={k: SecretStr(v) if k in secret else v for k, v in fields.items()})

    async def list_all_stores(self):
        return [StoreSummary(id=s.id, name=s.name, status=s.status, plan=s.plan or "free")
                for s in self.stores.values()]

    # an active store's customers (the flow itself is EchoFlow here)
    async def get_or_create_customer(self, store_id, telegram_id, name=None):
        return Customer(id=uuid4(), store_id=store_id, telegram_id=telegram_id, name=name)

    async def list_products(self, store_id, limit=100):
        return []

    # users and staff
    async def get_user(self, token):
        return self.logins.get(token)

    async def is_platform_admin(self, user_id):
        return user_id in self.admins

    async def staff_role(self, store_id, user_id):
        return self.staff.get((store_id, user_id))

    async def list_user_stores(self, user_id):
        return [StoreMembership(store_id=sid, name=self.stores[sid].name, role=role,
                                status=self.stores[sid].status, plan=self.stores[sid].plan or "free")
                for (sid, uid), role in self.staff.items() if uid == user_id]

    async def invite_staff(self, store_id, email, invited_by):
        self.invites[(store_id, email)] = invited_by

    async def send_invite_email(self, email):
        return email.startswith("new")

    async def accept_invites(self, user_id, email):
        joined = 0
        for store_id, invited in list(self.invites):
            if invited == email:
                del self.invites[(store_id, invited)]
                if (store_id, user_id) not in self.staff:
                    self.staff[(store_id, user_id)] = "staff"
                    joined += 1
        return joined

    async def remove_staff(self, store_id, user_id):
        if self.staff.get((store_id, user_id)) != "staff":
            return False
        del self.staff[(store_id, user_id)]
        return True

    # link codes
    async def set_link_code(self, store_id, code, expires_at):
        self.links[store_id] = (code, expires_at)

    async def use_link_code(self, store_id, code, field, chat_id):
        found = self.links.get(store_id)
        if not found or found[0] != code or found[1] <= datetime.now(timezone.utc):
            return False
        del self.links[store_id]
        self._update(store_id, **{field: chat_id})
        return True


class FakeTelegram:
    """Answers getMe for the known tokens; records every call."""

    def __init__(self):
        self.calls = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        _, bot, method = request.url.path.split("/")
        token = bot.removeprefix("bot")
        body = json.loads(request.content or b"{}")
        self.calls.append((token, method, body))
        if method == "getMe":
            if token not in BOTS:
                return httpx.Response(401, json={"ok": False, "error_code": 401, "description": "Unauthorized"})
            bot_id, username = BOTS[token]
            return httpx.Response(200, json={"ok": True, "result": {
                "id": bot_id, "is_bot": True, "first_name": "Shop", "username": username}})
        if method == "sendMessage":
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 900}})
        return httpx.Response(200, json={"ok": True, "result": True})

    def methods(self, token=None):
        return [m for t, m, _ in self.calls if token is None or t == token]

    def sent(self, chat_id):
        return [b["text"] for _, m, b in self.calls if m == "sendMessage" and b["chat_id"] == chat_id]


@pytest.fixture
def world():
    db, telegram = FakeDb(), FakeTelegram()
    service = TelegramService(httpx.AsyncClient(base_url=TELEGRAM_API,
                                                transport=httpx.MockTransport(telegram.handler)))
    orchestrator = Orchestrator(db, InMemoryConversationStore(), service, None,
                                burst_wait=0, flow=EchoFlow())
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_orchestrator] = lambda: orchestrator
    app.dependency_overrides[get_onboarding] = lambda: Onboarding(db, service, PUBLIC_URL)
    yield db, telegram, TestClient(app)
    app.dependency_overrides.clear()


def create(client, token=TOKEN_A, who="owner", name="Selam Shoes"):
    return client.post("/api/v1/stores", json={"name": name, "bot_token": token}, headers=auth(who))


def webhook(client, store, update):
    return client.post(f"/api/v1/webhook/{store.id}", json=update,
                       headers={SECRET_HEADER: store.webhook_secret.get_secret_value()})


def group_message(text, chat_id=-500, chat_type="group"):
    message = {"message_id": 7, "date": 1790000000, "chat": {"id": chat_id, "type": chat_type},
               "text": text}
    if chat_type == "channel":
        return {"update_id": 1, "channel_post": message}
    return {"update_id": 1, "message": message | {"from": {"id": 5, "first_name": "Owner"}}}


def private_message(text):
    return {"update_id": 2, "message": {"message_id": 1, "date": 1790000000,
                                        "chat": {"id": 42, "type": "private"},
                                        "from": {"id": 42, "first_name": "Abebe"}, "text": text}}


# --- Creating a store ----------------------------------------------------------------

def test_create_store_is_pending_and_the_bot_is_connected(world):
    db, telegram, client = world
    response = create(client)
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pending" and body["bot_username"] == "shop_a_bot" and body["bot_connected"]
    store = db.stores[UUID(body["store_id"])]
    assert db.staff[(store.id, OWNER.id)] == "owner"
    assert store.webhook_secret is not None
    assert telegram.methods(TOKEN_A) == ["getMe", "setWebhook", "setMyDescription",
                                         "setMyShortDescription", "setMyCommands"]
    webhook_call = next(b for _, m, b in telegram.calls if m == "setWebhook")
    assert webhook_call["url"] == f"{PUBLIC_URL}/api/v1/webhook/{store.id}"
    assert webhook_call["secret_token"] == store.webhook_secret.get_secret_value()


def test_create_store_needs_a_login(world):
    _, telegram, client = world
    assert client.post("/api/v1/stores", json={"name": "X shop", "bot_token": TOKEN_A}).status_code == 401
    assert create(client, who="expired").status_code == 401
    assert telegram.calls == []


def test_a_bad_or_revoked_token_is_refused(world):
    db, _, client = world
    bad = create(client, token="not-a-token-at-all-really")
    assert bad.status_code == 400 and "bot token" in bad.json()["detail"]
    revoked = create(client, token=TOKEN_REVOKED)
    assert revoked.status_code == 400 and "doesn't accept" in revoked.json()["detail"]
    assert db.stores == {}


def test_one_store_per_bot_even_with_a_regenerated_token(world):
    db, _, client = world
    assert create(client).status_code == 201
    again = create(client, token=TOKEN_A2, who="stranger", name="Copy shop")
    assert again.status_code == 409 and "@shop_a_bot is already used" in again.json()["detail"]
    assert len(db.stores) == 1


def test_my_stores_lists_role_and_status(world):
    _, _, client = world
    create(client)
    [mine] = client.get("/api/v1/me/stores", headers=auth("owner")).json()
    assert mine["role"] == "owner" and mine["status"] == "pending"
    assert client.get("/api/v1/me/stores", headers=auth("stranger")).json() == []


# --- A pending store (D14) ------------------------------------------------------------

def test_pending_store_tells_customers_it_is_not_open(world):
    db, telegram, client = world
    store = db.stores[UUID(create(client).json()["store_id"])]
    telegram.calls.clear()
    assert webhook(client, store, private_message("hi")).status_code == 200
    assert telegram.sent(42) == [both("store_not_open")]
    assert db.inbox == []  # nothing is processed


def test_chatid_still_works_while_pending(world):
    db, telegram, client = world
    store = db.stores[UUID(create(client).json()["store_id"])]
    webhook(client, store, group_message("/chatid"))
    assert "This chat's id is -500" in telegram.sent(-500)[0]


# --- /link <code> ------------------------------------------------------------------------

def test_link_code_connects_the_staff_group_once(world):
    db, telegram, client = world
    store_id = UUID(create(client).json()["store_id"])
    response = client.post(f"/api/v1/stores/{store_id}/link-code", headers=auth("owner"))
    assert response.status_code == 200
    code = response.json()["code"]
    assert f"/link {code}" in response.json()["instructions"] and "@shop_a_bot" in response.json()["instructions"]
    assert "GROUP" in response.json()["instructions"] and "CHANNEL" in response.json()["instructions"]

    webhook(client, db.stores[store_id], group_message(f"/link {code.lower()}"))  # typed in lowercase
    assert db.stores[store_id].staff_chat_id == -500
    assert telegram.sent(-500)[-1].startswith("✅ This group is now the staff group of Selam Shoes")

    webhook(client, db.stores[store_id], group_message(f"/link {code}", chat_id=-600))  # used up
    assert db.stores[store_id].staff_chat_id == -500
    assert telegram.sent(-600)[-1].startswith("❌ That code is wrong, already used, or expired")


def test_link_code_in_a_channel_saves_the_channel_quietly(world):
    db, telegram, client = world
    store_id = UUID(create(client).json()["store_id"])
    code = client.post(f"/api/v1/stores/{store_id}/link-code", headers=auth("owner")).json()["code"]
    webhook(client, db.stores[store_id], group_message(f"/link@shop_a_bot {code}", chat_id=-100777,
                                                       chat_type="channel"))
    assert db.stores[store_id].channel_id == -100777
    assert "deleteMessage" in telegram.methods() and telegram.sent(-100777) == []


def test_expired_or_missing_link_code_is_refused(world):
    db, telegram, client = world
    store_id = UUID(create(client).json()["store_id"])
    db.links[store_id] = ("ABCDEFGH", datetime.now(timezone.utc) - timedelta(minutes=1))
    webhook(client, db.stores[store_id], group_message("/link ABCDEFGH"))
    webhook(client, db.stores[store_id], group_message("/link"))
    assert db.stores[store_id].staff_chat_id is None
    assert all(t.startswith("❌") for t in telegram.sent(-500))


def test_only_the_owner_gets_link_codes_and_manages_staff(world):
    db, _, client = world
    store_id = UUID(create(client).json()["store_id"])
    db.staff[(store_id, STAFF.id)] = "staff"
    for who, status in (("staff", 403), ("stranger", 403)):
        assert client.post(f"/api/v1/stores/{store_id}/link-code", headers=auth(who)).status_code == status
        assert client.post(f"/api/v1/stores/{store_id}/invites", json={"email": "x@example.com"},
                           headers=auth(who)).status_code == status
        assert client.put(f"/api/v1/stores/{store_id}/bot-token", json={"bot_token": TOKEN_B},
                          headers=auth(who)).status_code == status
    assert db.stores[store_id].telegram_bot_id == 111111111


@pytest.mark.parametrize("text, code", [
    ("/link ABCD2345", "ABCD2345"), ("/linkabcd2345", "ABCD2345"), ("/LINK  abcd2345 ", "ABCD2345"),
    ("/link@shop_a_bot ABCD2345", "ABCD2345"), ("/link", ""),
    ("/link <ABCD2345>", "ABCD2345"), ("/link<ABCD2345>", "ABCD2345"),
])
def test_link_command_forms(text, code):
    assert link_command(TelegramUpdate.model_validate(group_message(text))) == (-500, "group", code)


@pytest.mark.parametrize("text", ["link ABCD2345", "/linker is here", "hello /link ABCD2345", "/chatid"])
def test_not_a_link_command(text):
    assert link_command(TelegramUpdate.model_validate(group_message(text))) is None


def test_the_owner_of_another_store_cant_touch_this_one(world):
    db, _, client = world
    store_a = UUID(create(client).json()["store_id"])
    create(client, token=TOKEN_B, who="stranger", name="Other shop")  # the stranger owns store B
    assert client.post(f"/api/v1/stores/{store_a}/link-code", headers=auth("stranger")).status_code == 403
    assert client.post(f"/api/v1/stores/{store_a}/invites", json={"email": "x@example.com"},
                       headers=auth("stranger")).status_code == 403
    assert client.delete(f"/api/v1/stores/{store_a}/staff/{OWNER.id}", headers=auth("stranger")).status_code == 403
    assert client.put(f"/api/v1/stores/{store_a}/bot-token", json={"bot_token": TOKEN_A2},
                      headers=auth("stranger")).status_code == 403
    assert [s["name"] for s in client.get("/api/v1/me/stores", headers=auth("stranger")).json()] == ["Other shop"]
    assert db.links == {} and db.invites == {}


def test_link_codes_are_easy_to_type():
    code = new_link_code()
    assert len(code) == 8 and not set(code) & set("0O1IL")


# --- Staff invitations ----------------------------------------------------------------------

def test_invite_accept_and_remove_staff(world):
    db, _, client = world
    store_id = UUID(create(client).json()["store_id"])
    response = client.post(f"/api/v1/stores/{store_id}/invites", json={"email": "Staff@Example.com"},
                           headers=auth("owner"))
    assert response.status_code == 200 and not response.json()["email_sent"]  # existing account
    assert (store_id, "staff@example.com") in db.invites

    assert client.post("/api/v1/me/accept-invites", headers=auth("staff")).json() == {"joined": 1}
    assert db.staff[(store_id, STAFF.id)] == "staff"
    assert client.post("/api/v1/me/accept-invites", headers=auth("staff")).json() == {"joined": 0}

    assert client.delete(f"/api/v1/stores/{store_id}/staff/{STAFF.id}", headers=auth("owner")).status_code == 200
    assert (store_id, STAFF.id) not in db.staff
    # The owner can't be removed.
    assert client.delete(f"/api/v1/stores/{store_id}/staff/{OWNER.id}", headers=auth("owner")).status_code == 404


def test_an_unconfirmed_email_cant_claim_an_invite(world):
    db, _, client = world
    store_id = UUID(create(client).json()["store_id"])
    client.post(f"/api/v1/stores/{store_id}/invites", json={"email": "staff@example.com"}, headers=auth("owner"))
    db.logins["unconfirmed"] = AuthUser(id=uuid4(), email="staff@example.com", email_confirmed=False)
    assert client.post("/api/v1/me/accept-invites", headers=auth("unconfirmed")).json() == {"joined": 0}
    assert (store_id, "staff@example.com") in db.invites


# --- Changing the bot token (D17) ------------------------------------------------------------

def test_same_bot_with_a_regenerated_token(world):
    db, telegram, client = world
    store_id = UUID(create(client).json()["store_id"])
    old_secret = db.stores[store_id].webhook_secret.get_secret_value()
    response = client.put(f"/api/v1/stores/{store_id}/bot-token", json={"bot_token": TOKEN_A2},
                          headers=auth("owner"))
    assert response.status_code == 200 and response.json()["note"] == ""
    store = db.stores[store_id]
    assert store.telegram_bot_token.get_secret_value() == TOKEN_A2
    assert store.webhook_secret.get_secret_value() != old_secret
    assert "deleteWebhook" not in telegram.methods()
    assert "setWebhook" in telegram.methods(TOKEN_A2)


def test_a_new_bot_disconnects_the_old_one(world):
    db, telegram, client = world
    store_id = UUID(create(client).json()["store_id"])
    response = client.put(f"/api/v1/stores/{store_id}/bot-token", json={"bot_token": TOKEN_B},
                          headers=auth("owner"))
    assert response.status_code == 200 and "@shop_b_bot" in response.json()["note"]
    assert db.stores[store_id].telegram_bot_id == 222222222
    assert "deleteWebhook" in telegram.methods(TOKEN_A) and "setWebhook" in telegram.methods(TOKEN_B)


def test_a_bot_used_by_another_store_is_refused(world):
    db, _, client = world
    first = UUID(create(client).json()["store_id"])
    create(client, token=TOKEN_B, who="stranger", name="Other shop")
    response = client.put(f"/api/v1/stores/{first}/bot-token", json={"bot_token": TOKEN_B}, headers=auth("owner"))
    assert response.status_code == 409
    assert db.stores[first].telegram_bot_id == 111111111


# --- Platform admin (D14–D16) -------------------------------------------------------------------

def test_only_platform_admins_manage_stores(world):
    db, _, client = world
    store_id = UUID(create(client).json()["store_id"])
    for who in ("owner", "stranger"):
        assert client.get("/api/v1/platform/stores", headers=auth(who)).status_code == 403
        assert client.post(f"/api/v1/platform/stores/{store_id}/approve", headers=auth(who)).status_code == 403
    assert client.get("/api/v1/platform/stores").status_code == 401
    assert db.stores[store_id].status == "pending"


def test_approve_suspend_and_plan(world):
    db, telegram, client = world
    store_id = UUID(create(client).json()["store_id"])
    db._update(store_id, staff_chat_id=-500)

    [listed] = client.get("/api/v1/platform/stores", headers=auth("admin")).json()
    assert listed["status"] == "pending"

    assert client.post(f"/api/v1/platform/stores/{store_id}/approve", headers=auth("admin")).json()["status"] == "active"
    assert db.stores[store_id].status == "active"
    assert telegram.sent(-500)[-1].startswith("✅ Selam Shoes is approved")
    telegram.calls.clear()
    webhook(client, db.stores[store_id], private_message("hi"))  # now served by the flow
    assert telegram.sent(42) == ["You said: hi"]

    client.post(f"/api/v1/platform/stores/{store_id}/suspend", headers=auth("admin"))
    assert db.stores[store_id].status == "suspended"
    assert telegram.sent(-500)[-1].startswith("⛔ Selam Shoes is suspended")

    assert client.put(f"/api/v1/platform/stores/{store_id}/plan", json={"plan": "pro"},
                      headers=auth("admin")).json()["plan"] == "pro"
    assert db.stores[store_id].plan == "pro"
    assert client.put(f"/api/v1/platform/stores/{store_id}/plan", json={"plan": "gold"},
                      headers=auth("admin")).status_code == 422
    assert client.post(f"/api/v1/platform/stores/{uuid4()}/approve", headers=auth("admin")).status_code == 404
