"""Phase 8c: the scripted order flow (D28). No network: Telegram, the AI and
the database are faked. Replaces the old agent-loop tests (test_agent.py)."""
import json
from decimal import Decimal
from itertools import count
from uuid import UUID, uuid4

import httpx
import pytest

from app.agents.messages import t
from app.agents.orchestrator import Orchestrator
from app.agents.tools import order_number
from app.models.schemas import Customer, OrderItemDetail, OrderWithItems, Product, Store, VariantMatch
from app.services.conversation_service import InMemoryConversationStore
from app.services.llm_service import FakeProvider, LLMResponse, ToolCall
from app.services.supabase_service import NotFoundError, OrderRejectedError, OutOfStockError
from app.services.telegram_service import TELEGRAM_API, TelegramService

pytestmark = pytest.mark.anyio

BOT_TOKEN = "123456:TEST-TOKEN"
STAFF_CHAT = -100555
CUSTOMER = 42
PAYMENT_TEXT = "Telebirr: 0911 000 000 (Selam Shoes)"
STORE = Store(id=uuid4(), name="Selam Shoes", telegram_bot_token=BOT_TOKEN, webhook_secret="s",
              staff_chat_id=STAFF_CHAT, payment_instructions=PAYMENT_TEXT,
              opening_hours="Mon-Sat 8:30-19:00, Sun closed")
OTHER_STORE_ID = uuid4()

AF1 = UUID("00000000-0000-0000-0000-00000000af01")
SAMBA = UUID("00000000-0000-0000-0000-00000000ba01")
SHIRT = UUID("00000000-0000-0000-0000-000000005101")
OTHER_SHOE = UUID("00000000-0000-0000-0000-000000000999")  # another store's product
AF1_WHITE_42 = UUID("00000000-0000-0000-0000-000000000042")
AF1_WHITE_43 = UUID("00000000-0000-0000-0000-000000000043")
AF1_BLACK_42 = UUID("00000000-0000-0000-0000-000000000142")
SAMBA_WHITE_40 = UUID("00000000-0000-0000-0000-000000000040")
SHIRT_BLACK_M = UUID("00000000-0000-0000-0000-00000000000d")
OTHER_VARIANT = UUID("00000000-0000-0000-0000-000000000998")

_update_ids = count(1)


@pytest.fixture
def anyio_backend():
    return "asyncio"


# --- Fakes ----------------------------------------------------------------------

def _v(variant_id, product_id, name, brand, category, color, size, stock, price, keywords=""):
    return VariantMatch(variant_id=variant_id, product_id=product_id, product_name=name, brand=brand,
                        category=category, color=color, size=size, stock_quantity=stock,
                        price=Decimal(price)), keywords


class FakeDb:
    """Just enough of SupabaseService for the flow, with the database's rules."""

    def __init__(self):
        rows = [
            _v(AF1_WHITE_42, AF1, "Air Force 1", "Nike", "sneakers", "White", "42", 2, "5000", "AF1, ኤር ፎርስ"),
            _v(AF1_WHITE_43, AF1, "Air Force 1", "Nike", "sneakers", "White", "43", 10, "5000", "AF1, ኤር ፎርስ"),
            _v(AF1_BLACK_42, AF1, "Air Force 1", "Nike", "sneakers", "Black", "42", 5, "5000", "AF1, ኤር ፎርስ"),
            _v(SAMBA_WHITE_40, SAMBA, "Samba", "Adidas", "sneakers", "White", "40", 3, "5200", "ሳምባ"),
            _v(SHIRT_BLACK_M, SHIRT, "Basic T-Shirt", None, "clothing", "Black", "M", 4, "800", "tshirt"),
        ]
        self.variants = {v.variant_id: v for v, _ in rows}
        self.keywords = {v.product_id: k for v, k in rows}
        self.store_of = {AF1: STORE.id, SAMBA: STORE.id, SHIRT: STORE.id, OTHER_SHOE: OTHER_STORE_ID}
        other, _ = _v(OTHER_VARIANT, OTHER_SHOE, "Other store shoe", None, "sneakers", "White", "42", 9, "100")
        self.variants[OTHER_VARIANT] = other
        self.orders: dict[str, OrderWithItems] = {}
        self.customer = Customer(id=uuid4(), store_id=STORE.id, telegram_id=CUSTOMER, name="Abebe")
        self.customer_updates = []
        self.payments = []
        self.staff_logins = {}

    def _mine(self, store_id):
        return [v for v in self.variants.values() if self.store_of[v.product_id] == store_id]

    async def get_store(self, store_id):
        return STORE if store_id == STORE.id else None

    async def get_or_create_customer(self, store_id, telegram_id, name=None):
        assert store_id == STORE.id
        return self.customer

    async def update_customer(self, store_id, customer_id, **fields):
        self.customer_updates.append(fields)
        self.customer = self.customer.model_copy(update={k: v for k, v in fields.items() if v})

    async def list_products(self, store_id, limit=100):
        seen = {}
        for v in self._mine(store_id):
            seen.setdefault(v.product_id, Product(id=v.product_id, store_id=store_id, name=v.product_name,
                                                  brand=v.brand, category=v.category,
                                                  search_keywords=self.keywords.get(v.product_id)))
        return list(seen.values())

    async def list_categories(self, store_id):
        return sorted({v.category for v in self._mine(store_id) if v.stock_quantity > 0})

    async def list_products_in_stock(self, store_id, category=None, limit=20):
        in_stock = {v.product_id for v in self._mine(store_id) if v.stock_quantity > 0}
        return sorted([p for p in await self.list_products(store_id)
                       if p.id in in_stock and (category is None or p.category == category)],
                      key=lambda p: p.name)

    async def get_product_variants(self, store_id, product_id):
        return [v for v in self._mine(store_id) if v.product_id == product_id]

    async def search_variants(self, store_id, query=None, color=None, size=None, limit=20):
        words = (query or "").lower().split()
        found = []
        for v in self._mine(store_id):
            haystack = " ".join(filter(None, [v.product_name, v.brand, v.category,
                                              self.keywords.get(v.product_id)])).lower()
            if all(w in haystack for w in words):
                found.append(v)
        return found[:limit]

    async def create_order(self, store_id, customer_id, draft, idempotency_key, hold_minutes=5):
        assert (store_id, customer_id) == (STORE.id, self.customer.id)
        if idempotency_key in self.orders:
            return self.orders[idempotency_key].id
        items = []
        for item in draft.items:
            variant = self.variants[item.variant_id]
            if variant.available < item.quantity:
                raise OutOfStockError("out_of_stock", str(item.variant_id))
            items.append(OrderItemDetail(id=uuid4(), order_id=uuid4(), variant_id=item.variant_id,
                                         quantity=item.quantity, price=variant.price,
                                         product_name=variant.product_name, color=variant.color,
                                         size=variant.size))
        for item in draft.items:  # the order holds its items (D19)
            variant = self.variants[item.variant_id]
            self.variants[item.variant_id] = variant.model_copy(update={"held": variant.held + item.quantity})
        order = OrderWithItems(
            id=uuid4(), store_id=store_id, customer_id=customer_id, items=items,
            total_price=sum(i.price * i.quantity for i in items),
            fulfillment_method=draft.fulfillment_method, contact_name=draft.contact_name,
            contact_phone=draft.contact_phone, delivery_address=draft.delivery_address,
            idempotency_key=idempotency_key,
        )
        self.orders[idempotency_key] = order
        return order.id

    async def get_order(self, store_id, order_id):
        return next((o for o in self.orders.values() if o.id == order_id and o.store_id == store_id), None)

    async def get_customer_orders(self, store_id, customer_id, limit=5):
        return [o for o in self.orders.values()
                if o.store_id == store_id and o.customer_id == customer_id][:limit]

    async def get_customer(self, store_id, customer_id):
        return self.customer if (store_id, customer_id) == (STORE.id, self.customer.id) else None

    async def record_payment(self, store_id, order_id, amount, method, staff_id):
        """Like confirm_payment in the database: all or nothing (D3)."""
        order = await self.get_order(store_id, order_id)
        if order is None:
            raise NotFoundError("order_not_found")
        if order.payment_status == "paid":
            raise OrderRejectedError("already_paid")
        for item in order.items:
            variant = self.variants[item.variant_id]
            own_hold = item.quantity if variant.held >= item.quantity else 0
            if variant.stock_quantity - (variant.held - own_hold) < item.quantity:
                raise OutOfStockError("out_of_stock", str(item.variant_id))
        for item in order.items:
            variant = self.variants[item.variant_id]
            self.variants[item.variant_id] = variant.model_copy(update={
                "stock_quantity": variant.stock_quantity - item.quantity,
                "held": max(variant.held - item.quantity, 0)})
        self.orders[order.idempotency_key] = order.model_copy(
            update={"payment_status": "paid", "status": "confirmed"})
        self.payments.append({"order_id": order_id, "amount": amount, "method": method, "staff": staff_id})
        return uuid4()

    async def note_payment_confirmer(self, store_id, payment_id, telegram_id, name):
        self.payments[-1]["confirmed_by"] = (telegram_id, name)

    async def verify_staff(self, store_id, token):
        return self.staff_logins.get((store_id, token))

    def set_stock(self, variant_id, stock):
        self.variants[variant_id] = self.variants[variant_id].model_copy(update={"stock_quantity": stock})


class FakeTelegram:
    def __init__(self):
        self.sent = []  # (chat_id, text)
        self.calls = []  # (method, body, message_id)
        self._next_id = 1000

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        method = request.url.path.rsplit("/", 1)[-1]
        self._next_id += 1
        self.calls.append((method, body, self._next_id))
        if method in ("sendMessage", "sendPhoto"):
            self.sent.append((body.get("chat_id"), body.get("text") or body.get("caption")))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": self._next_id}})

    def service(self):
        return TelegramService(httpx.AsyncClient(base_url=TELEGRAM_API,
                                                 transport=httpx.MockTransport(self.handler)))

    def to(self, chat_id):
        return [text for chat, text in self.sent if chat == chat_id]

    def last_message_id(self, chat_id):
        return next(mid for method, body, mid in reversed(self.calls)
                    if method in ("sendMessage", "sendPhoto") and body.get("chat_id") == chat_id)

    def buttons(self, message_id):
        body = next(body for _, body, mid in self.calls if mid == message_id)
        rows = body.get("reply_markup", {}).get("inline_keyboard", [])
        return [(b["text"], b["callback_data"]) for row in rows for b in row]


def interp(**fields) -> LLMResponse:
    """The AI's answer: one call to the interpret tool."""
    return LLMResponse(model="fake", tool_calls=[ToolCall(id="i1", name="interpret", arguments=fields)])


class World:
    def __init__(self, language="en"):
        """language: chosen automatically before the first message (None: not chosen)."""
        self.db = FakeDb()
        self.telegram = FakeTelegram()
        self.store = InMemoryConversationStore()
        self.llm = FakeProvider()
        self.orchestrator = Orchestrator(self.db, self.store, self.telegram.service(), self.llm,
                                         burst_wait=0)
        self._pending_language = language

    def script(self, *responses: LLMResponse):
        self.llm._responses.extend(responses)

    async def _deliver(self, update):
        if self._pending_language:
            language, self._pending_language = self._pending_language, None
            await self.tap_data(f"f:lang:{language}")
        await self.store.save_to_inbox(STORE.id, update["update_id"], CUSTOMER, update)
        await self.orchestrator.process_customer(STORE, CUSTOMER)

    def _message(self, **fields):
        return {"message_id": 5000 + next(_update_ids), "date": 1790000000,
                "chat": {"id": CUSTOMER, "type": "private"},
                "from": {"id": CUSTOMER, "is_bot": False, "first_name": "Abebe", "username": "abebe_k"},
                **fields}

    async def say(self, text):
        await self._deliver({"update_id": next(_update_ids), "message": self._message(text=text)})

    async def send_photo(self, caption=None):
        fields = {"photo": [{"file_id": "screenshot", "file_unique_id": "s1", "width": 800, "height": 1600}]}
        if caption:
            fields["caption"] = caption
        await self._deliver({"update_id": next(_update_ids), "message": self._message(**fields)})

    async def tap_data(self, data):
        """The customer taps a button with this data (even an old one)."""
        update = {"update_id": next(_update_ids), "callback_query": {
            "id": f"q{next(_update_ids)}", "from": {"id": CUSTOMER, "first_name": "Abebe", "username": "abebe_k"},
            "message": {"message_id": 1, "date": 1790000000, "chat": {"id": CUSTOMER, "type": "private"}},
            "data": data}}
        if self._pending_language:
            await self._deliver(update)
            return
        await self.store.save_to_inbox(STORE.id, update["update_id"], CUSTOMER, update)
        await self.orchestrator.process_customer(STORE, CUSTOMER)

    async def tap(self, label):
        """The customer taps the button with this label on the bot's last message."""
        await self.tap_data(dict(self.last_buttons())[label])

    def last_text(self):
        return self.telegram.to(CUSTOMER)[-1]

    def last_buttons(self):
        return self.telegram.buttons(self.telegram.last_message_id(CUSTOMER))

    def labels(self):
        return [label for label, _ in self.last_buttons()]

    @property
    def conversation(self):
        return self.store.conversations[(STORE.id, CUSTOMER)]

    @property
    def draft(self):
        return self.conversation.order_draft


START_OVER = t("btn_start_over", "en")
LANGUAGE = t("btn_change_language", "en")


async def up_to_confirm(world: World, color="White", size="42", fulfillment="btn_pickup"):
    """Buttons all the way to the order summary (new customer)."""
    await world.say("/start")
    await world.tap("Sneakers")
    await world.tap("Air Force 1 (Nike)")
    await world.tap(color)
    if size in world.labels():
        await world.tap(size)
    await world.tap("1")
    await world.tap(t(fulfillment, "en"))
    await world.say("Abebe Kebede")
    await world.say("0911 22 33 44")


async def placed_order(world: World):
    await up_to_confirm(world)
    await world.tap(t("btn_confirm", "en"))
    [order] = world.db.orders.values()
    return order


# --- Step 0: the language (D29) ----------------------------------------------------

async def test_first_contact_asks_for_the_language():
    world = World(language=None)
    await world.say("Do you have Air Force 1?")
    assert world.last_text() == t("choose_language", "en")
    assert world.labels() == ["አማርኛ", "English"]
    assert world.llm.requests == [] and world.draft.product_id is None  # nothing else yet
    await world.tap("English")
    assert world.last_text() == t("ask_product", "en")


async def test_chosen_language_is_used_for_everything():
    world = World(language="am")
    await world.say("/start")
    assert world.last_text() == t("ask_product", "am")
    assert world.labels()[-1] == t("btn_start_over", "am")
    await world.say("Air Force 1")  # typing English doesn't switch the chosen language
    assert world.last_text() == t("ask_color", "am", product="Air Force 1", price="5,000 ብር")


async def test_language_is_remembered_and_can_be_changed():
    world = World(language="am")
    await world.say("/start")
    await world.tap(t("btn_change_language", "am"))
    assert world.last_text() == t("choose_language", "en")
    await world.tap("English")
    assert world.last_text() == t("ask_product", "en")
    await world.tap(START_OVER)
    assert world.draft.language == "en"  # a new order keeps the language


async def test_the_ai_is_told_the_chosen_language():
    world = World(language="am")
    world.script(interp(intent="side_question", reply="ሰላም!"))
    await world.say("hi")
    system_prompt = world.llm.requests[0]["system_prompt"]
    assert "THE CUSTOMER'S LANGUAGE: Amharic" in system_prompt


# --- Step 1: ASK_PRODUCT ---------------------------------------------------------

async def test_start_shows_categories_with_stock_language_and_start_over():
    world = World()
    await world.say("/start")
    assert world.last_text() == t("ask_product", "en")
    assert world.labels() == ["Clothing", "Sneakers", LANGUAGE, START_OVER]
    assert world.draft.step == "ask_product"
    assert world.llm.requests == []  # no AI needed


async def test_category_with_several_products_shows_product_buttons():
    world = World()
    await world.say("/start")
    await world.tap("Sneakers")
    assert world.last_text() == t("ask_product_pick", "en")
    assert world.labels() == ["Air Force 1 (Nike)", "Samba (Adidas)", START_OVER]
    assert "Other store shoe" not in str(world.labels())  # never another store's products


async def test_category_with_one_product_goes_straight_on():
    world = World()
    await world.say("/start")
    await world.tap("Clothing")  # only the T-shirt: one color, one size
    assert world.draft.product_name == "Basic T-Shirt"
    assert world.draft.step == "ask_quantity"  # Black and M picked automatically


async def test_typed_product_name_and_nickname():
    world = World()
    await world.say("Air Force 1")
    assert world.draft.product_name == "Air Force 1" and world.draft.step == "ask_color"
    world2 = World()
    await world2.say("ሳምባ")  # the Amharic nickname
    assert world2.draft.product_name == "Samba"
    assert world.llm.requests == [] and world2.llm.requests == []


async def test_greeting_is_not_mistaken_for_a_product():
    world = World()
    world.script(interp(intent="side_question", reply="Hello! Welcome to Selam Shoes."))
    await world.say("hi")  # "hi" is inside "T-Shirt", but it's a greeting
    assert world.draft.product_id is None
    assert world.last_text().startswith("Hello! Welcome to Selam Shoes.")
    assert t("ask_product", "en") in world.last_text()


async def test_photo_while_choosing_a_product():
    world = World()
    await world.send_photo()
    assert world.last_text().startswith(t("photo_not_product", "en"))
    assert not world.conversation.bot_paused


# --- Step 2: ASK_COLOR (before the size, D29) --------------------------------------

async def test_colors_in_stock_with_database_price():
    world = World()
    await world.say("Air Force 1")
    assert world.last_text() == t("ask_color", "en", product="Air Force 1", price="5,000 ETB")
    assert world.labels() == ["White", "Black", START_OVER]


async def test_sold_out_color_is_not_offered():
    world = World()
    world.db.set_stock(AF1_BLACK_42, 0)
    await world.say("Air Force 1")
    # Only White is left: nothing to ask, straight to White's sizes.
    assert world.draft.color == "White" and world.draft.step == "ask_size"
    assert world.labels() == ["42", "43", START_OVER]


async def test_typed_color_in_amharic():
    world = World()
    await world.say("Air Force 1")
    await world.say("ጥቁር")  # black: only size 42 in black, so the size is picked too
    assert world.draft.variant_id == AF1_BLACK_42 and world.draft.step == "ask_quantity"
    assert world.llm.requests == []


# --- Step 3: ASK_SIZE (only sizes of the chosen color) ------------------------------

async def test_only_sizes_in_stock_for_the_chosen_color():
    world = World()
    await world.say("Air Force 1")
    await world.tap("White")
    assert world.last_text() == t("ask_size", "en")
    assert world.labels() == ["42", "43", START_OVER]


async def test_held_stock_is_not_offered():
    world = World()
    world.db.variants[AF1_WHITE_43] = world.db.variants[AF1_WHITE_43].model_copy(update={"held": 10})
    await world.say("Air Force 1")
    await world.tap("White")
    assert world.draft.size == "42"  # 43 is all held by other orders (D19)


async def test_typed_size_and_unavailable_size():
    world = World()
    await world.say("Air Force 1")
    await world.tap("White")
    await world.say("50")
    assert world.last_text().startswith(t("size_unavailable", "en", size="50"))
    assert world.draft.step == "ask_size"
    await world.say("ቁጥር 43")
    assert world.draft.variant_id == AF1_WHITE_43 and world.draft.step == "ask_quantity"
    assert world.llm.requests == []


# --- Step 4: ASK_QUANTITY -------------------------------------------------------

async def test_quantity_buttons_never_offer_more_than_available():
    world = World()
    await world.say("Air Force 1")
    await world.tap("White")
    await world.tap("42")  # 2 in stock
    assert world.last_text() == t("ask_quantity", "en")
    assert world.labels() == ["1", "2", START_OVER]
    await world.say("5")
    assert world.last_text().startswith(t("too_many", "en"))
    await world.say("ሁለት")  # two
    assert world.draft.quantity == 2 and world.draft.step == "ask_delivery"


# --- Step 5: ASK_DELIVERY and ASK_CONTACT ----------------------------------------------

async def test_delivery_no_longer_asks_for_an_address():
    world = World()
    await world.say("Air Force 1")
    await world.tap("White")
    await world.tap("43")
    await world.tap("1")
    assert world.labels() == [t("btn_delivery", "en"), t("btn_pickup", "en"), START_OVER]
    await world.say("delivery please")
    assert world.draft.fulfillment_method == "delivery"
    assert world.draft.step == "ask_name"  # staff arrange the address by phone (D29)


async def test_new_customer_gives_name_and_phone():
    world = World()
    await world.say("Air Force 1")
    await world.tap("White")
    await world.tap("43")
    await world.tap("1")
    await world.tap(t("btn_pickup", "en"))
    assert world.last_text() == t("ask_name", "en")
    assert world.labels() == [t("btn_use", "en", value="Abebe"), START_OVER]  # the Telegram name
    await world.tap(t("btn_use", "en", value="Abebe"))
    assert world.last_text() == t("ask_phone", "en")
    await world.say("12")
    assert world.last_text().startswith(t("invalid_phone", "en"))
    await world.say("+251 91 122 3344")
    assert world.draft.contact_phone == "+251911223344" and world.draft.step == "confirm"


async def test_known_customer_skips_contact():
    world = World()
    world.db.customer = world.db.customer.model_copy(update={"phone": "0911223344"})
    await world.say("Air Force 1")
    await world.tap("White")
    await world.tap("43")
    await world.tap("1")
    await world.tap(t("btn_pickup", "en"))
    assert world.draft.step == "confirm"  # no name / phone questions
    assert "Phone: 0911223344" in world.last_text()


# --- Step 6: CONFIRM ------------------------------------------------------------

async def test_summary_has_database_price_and_confirm_edit_buttons():
    world = World()
    await up_to_confirm(world)
    summary = world.last_text()
    assert summary.startswith(t("summary_title", "en"))
    assert "Air Force 1 (Nike), White, size 42 × 1 — 5,000 ETB" in summary
    assert t("summary_pickup", "en") in summary and "Name: Abebe Kebede" in summary
    assert world.labels() == [t("btn_confirm", "en"), t("btn_edit", "en"), START_OVER]
    assert world.db.orders == {}


async def test_edit_goes_back_to_a_step_and_keeps_the_rest():
    world = World()
    await up_to_confirm(world)
    await world.tap(t("btn_edit", "en"))
    assert world.last_text() == t("ask_edit", "en")
    await world.tap(t("btn_edit_size", "en"))
    assert world.draft.step == "ask_size" and world.draft.color == "White"  # color kept
    await world.tap("43")
    await world.tap("1")
    assert world.draft.step == "confirm"  # delivery and contact kept
    assert "White, size 43 × 1" in world.last_text()


async def test_edit_color_asks_color_then_size_again():
    world = World()
    await up_to_confirm(world)
    await world.tap(t("btn_edit", "en"))
    await world.tap(t("btn_edit_color", "en"))
    assert world.draft.step == "ask_color"
    await world.tap("Black")  # only 42 in black
    await world.tap("1")
    assert "Black, size 42 × 1" in world.last_text()


async def test_edit_contact_asks_again_even_for_a_known_customer():
    world = World()
    await up_to_confirm(world)
    world.db.customer = world.db.customer.model_copy(update={"phone": "0911000000"})
    await world.tap(t("btn_edit", "en"))
    await world.tap(t("btn_edit_contact", "en"))
    assert world.draft.step == "ask_name"


async def test_old_confirm_button_does_not_place_a_changed_order():
    world = World()
    await up_to_confirm(world)
    old_confirm = dict(world.last_buttons())[t("btn_confirm", "en")]
    await world.tap(t("btn_edit", "en"))
    await world.tap(t("btn_edit_quantity", "en"))
    await world.tap("2")  # the draft changed after that summary
    await world.tap_data(old_confirm)
    assert world.db.orders == {}
    assert world.last_text().startswith(t("option_gone", "en"))


# --- Step 7: PAYMENT (pickup) ------------------------------------------------------

async def test_pickup_order_gets_payment_instructions():
    world = World()
    order = await placed_order(world)
    assert order.total_price == Decimal("5000") and order.contact_phone == "0911223344"
    assert order.fulfillment_method == "pickup"
    assert world.db.variants[AF1_WHITE_42].held == 1  # held for 5 minutes (D19), not sold (D3)
    payment = world.last_text()
    assert payment.startswith(t("order_placed", "en", number=order_number(order.id), total="5,000 ETB"))
    assert PAYMENT_TEXT in payment and t("after_paying", "en") in payment
    assert world.telegram.to(STAFF_CHAT)[-1].startswith("🛒 New order")
    assert world.draft.step == "payment" and world.draft.last_order_id == order.id
    assert not world.conversation.bot_paused
    assert world.db.customer_updates[-1]["phone"] == "0911223344"  # remembered for next time
    assert world.llm.requests == []  # the whole order without a single AI call


async def test_confirm_twice_gives_one_order():
    world = World()
    await up_to_confirm(world)
    confirm = dict(world.last_buttons())[t("btn_confirm", "en")]
    await world.tap_data(confirm)
    await world.tap_data(confirm)
    assert len(world.db.orders) == 1


async def test_typed_yes_confirms_too():
    world = World()
    await up_to_confirm(world)
    await world.say("yes")
    assert len(world.db.orders) == 1


async def test_sold_out_at_confirm_asks_again_without_switching():
    world = World()
    await up_to_confirm(world)  # White 42
    world.db.set_stock(AF1_WHITE_42, 0)
    await world.tap(t("btn_confirm", "en"))
    assert world.db.orders == {}
    assert world.last_text().startswith(t("sold_out_now", "en", item="Air Force 1, White, size 42"))
    assert world.draft.color == "White" and world.draft.size is None  # never switched for them
    assert world.draft.step == "ask_size" and world.labels() == ["43", START_OVER]


async def test_screenshot_after_the_order_goes_to_staff():
    world = World()
    order = await placed_order(world)
    await world.send_photo(caption="paid")
    assert world.last_text() == t("photo_reply", "en")
    assert world.conversation.bot_paused
    alert = world.telegram.to(STAFF_CHAT)[-1]
    assert f"#{order_number(order.id)}" in alert and "paid" in alert


# --- Delivery goes to staff (D29) --------------------------------------------------

async def test_delivery_summary_says_staff_will_call():
    world = World()
    await up_to_confirm(world, fulfillment="btn_delivery")
    assert t("summary_delivery_arranged", "en") in world.last_text()


async def test_delivery_order_is_placed_and_handed_to_staff():
    world = World()
    await up_to_confirm(world, fulfillment="btn_delivery")
    await world.tap(t("btn_confirm", "en"))
    [order] = world.db.orders.values()
    assert order.fulfillment_method == "delivery" and order.delivery_address  # "to be arranged"
    assert world.db.variants[AF1_WHITE_42].held == 1  # held like pickup (D19)

    # The customer: no payment instructions, staff will call.
    assert world.last_text() == t("delivery_handoff", "en", number=order_number(order.id),
                                  total="5,000 ETB", phone="0911223344")
    assert PAYMENT_TEXT not in world.last_text()
    # Staff: the order, the phone, the @username, and the buttons.
    alert_id = world.telegram.last_message_id(STAFF_CHAT)
    alert = world.telegram.to(STAFF_CHAT)[-1]
    assert alert.startswith("🚚 New DELIVERY order") and "0911223344" in alert and "@abebe_k" in alert
    assert [label for label, _ in world.telegram.buttons(alert_id)] == [
        f"✅ Confirm payment #{order_number(order.id)}", "▶️ Hand back to bot"]
    # The bot stays quiet while staff arrange it.
    assert world.conversation.bot_paused
    replies_before = len(world.telegram.to(CUSTOMER))
    await world.say("hello?")
    assert len(world.telegram.to(CUSTOMER)) == replies_before  # no bot reply...
    assert world.telegram.to(STAFF_CHAT)[-1].endswith("hello?")  # ...staff see it instead


# --- Free text, the AI, and start over -----------------------------------------------

async def test_several_answers_at_once_skip_ahead():
    world = World()
    world.script(interp(intent="answer", product="Air Force 1", color="white", size="42"))
    await world.say("AF1 white size 42")
    assert (world.draft.product_id, world.draft.color, world.draft.variant_id) == (AF1, "White", AF1_WHITE_42)
    assert world.draft.step == "ask_quantity"  # the first missing step


async def test_a_size_without_a_color_picks_the_only_color_with_that_size():
    world = World()
    world.script(interp(intent="answer", product="Air Force 1", size="43"))
    await world.say("AF1 43")
    assert world.draft.variant_id == AF1_WHITE_43  # only White comes in 43


async def test_everything_at_once_goes_straight_to_the_summary():
    world = World()
    world.script(interp(intent="answer", product="Air Force 1", size="43", quantity=1,
                        fulfillment="pickup", name="Sara", phone="0922334455", price="1 ETB"))
    await world.say("AF1 43, 1 pair, pickup, Sara 0922334455")
    assert world.draft.step == "confirm"
    assert "5,000 ETB" in world.last_text() and "1 ETB" not in world.last_text()


async def test_ai_details_are_checked_against_stock():
    world = World()
    world.script(interp(intent="answer", product="Air Force 1", color="pink", size="50"))
    await world.say("AF1 size 50 in pink")
    assert world.draft.color is None and world.draft.step == "ask_color"
    assert t("color_unavailable", "en", color="pink") in world.last_text()


async def test_unknown_product_from_the_ai():
    world = World()
    world.script(interp(intent="answer", product="Jordan 4"))
    await world.say("do you have jordan 4?")
    assert world.last_text().startswith(t("no_match", "en", query="Jordan 4"))
    assert world.draft.step == "ask_product"


async def test_side_question_is_answered_then_the_step_repeats():
    world = World()
    await world.say("Air Force 1")
    await world.tap("White")
    world.script(interp(intent="side_question", reply="They usually run a bit large."))
    await world.say("does it run small?")
    text = world.last_text()
    assert text.startswith("They usually run a bit large.")
    assert text.endswith(t("ask_size", "en"))
    assert world.draft.step == "ask_size" and world.labels() == ["42", "43", START_OVER]


async def test_haggling_goes_to_staff():
    world = World()
    await world.say("Air Force 1")
    world.script(interp(intent="handover", reason="asks for a discount"))
    await world.say("4000 birr last price?")
    assert world.last_text() == t("handover_reply", "en")
    assert world.conversation.bot_paused
    alert = world.telegram.to(STAFF_CHAT)[-1]
    assert "asks for a discount" in alert and "Air Force 1" in alert


async def test_unclear_answer_from_the_ai_goes_to_staff():
    world = World()
    world.script(LLMResponse(model="fake", text="I think they mean something"))  # no tool call
    await world.say("hmm maybe the thing from before")
    assert world.conversation.bot_paused


async def test_order_status_is_answered_from_the_database():
    world = World()
    order = await placed_order(world)
    world.script(interp(intent="order_status"))
    await world.say("where is my order?")
    status = t("order_status_line", "en", number=order_number(order.id), status=t("status_pending", "en"))
    assert world.last_text().startswith(status)


async def test_start_over_button_on_every_step():
    world = World()
    await world.say("Air Force 1")
    await world.tap("White")
    assert START_OVER in world.labels()
    await world.tap(START_OVER)
    assert world.draft.product_id is None and world.draft.step == "ask_product"
    assert world.last_text().startswith(t("started_over", "en"))


async def test_old_buttons_are_checked_again():
    world = World()
    await world.say("Air Force 1")
    await world.tap_data(f"f:col:{OTHER_VARIANT}")  # another store's variant
    await world.tap_data("f:size:99")
    await world.tap_data(f"f:prod:{OTHER_SHOE}")
    await world.tap_data("f:cat:7")
    assert world.draft.color is None and world.draft.product_id == AF1
    assert world.draft.step == "ask_color"


async def test_button_taps_are_answered():
    world = World()
    await world.say("Air Force 1")
    await world.tap("White")
    assert any(method == "answerCallbackQuery" for method, _, _ in world.telegram.calls)


async def test_after_an_order_a_new_product_starts_a_new_order():
    world = World()
    order = await placed_order(world)
    await world.say("Samba")
    assert world.draft.product_name == "Samba" and world.draft.last_order_id == order.id


def test_a_store_can_override_any_text():
    class StoreWithTexts:
        text_overrides = {"ask_product": {"en": "Welcome to Selam! What do you need?"}}

    assert t("ask_product", "en", StoreWithTexts()) == "Welcome to Selam! What do you need?"
    assert t("ask_product", "am", StoreWithTexts()) == t("ask_product", "am")  # falls back
    assert t("ask_product", "en", STORE) == t("ask_product", "en")  # today: no overrides
