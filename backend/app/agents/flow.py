"""The scripted order flow (Phase 8c, D28/D29): a step-by-step state machine.

Each chat is always at one step, saved in conversations.order_draft:

    choose_language (first time only) -> ask_product -> ask_color -> ask_size
    -> ask_quantity -> ask_delivery -> ask_name -> ask_phone -> confirm
    -> payment (pickup)  or  hand-over to staff (delivery, D29)
                                   \\-> edit (go back to any step from confirm)

The next step is always "the first answer still missing", worked out by
code from the draft and live stock. That's what makes skipping ahead ("AF1
black size 42") and editing simple. Steps with only one possible answer (one
color, one size) are filled in automatically; contact details are skipped
for customers we already know.

Language (D29): the customer chooses it once; everything the bot says uses
it (questions, buttons, summary, the AI's short answers).

Delivery (D29): delivery orders are paid when the customer receives the
items, so after the summary the order is created (address "to be
arranged") and the chat goes to staff, who call the customer. Pickup gets
the store's payment instructions (stores.payment_instructions, set in the
dashboard) and a screenshot is sent back.

/help shows how to order, in the customer's language.

How a message is handled:
- Button taps (data like "f:size:42") are checked against the database
  again, so an old button can't sell something that's gone. A button only
  works at the step that showed it (BUTTON_STEPS): once the chat has moved
  on, an old one is ignored.
- Typed text is first matched by code against the current step ("42",
  "black", "ጥቁር", "2", "pickup", a phone number...). Only if that fails does
  the AI interpret it (interpreter.py): several answers at once, a side
  question (answered briefly, then the step's question again), or a
  hand-over to staff (haggling, complaints, anything unclear).
- Prices and stock always come from the database, never from the AI.

Channel catalog (Phase 8d, D32–D34): the channel post's Order button sends
"/start p_<code>"; a forwarded post or a typed code ("P101") works too. The
product is added to the order (a cart: several items in one order). If the
customer is still choosing another product, they're asked: finish it first
(the new one comes right after) or switch. A sold-out product gets similar products. The
product's photo is shown with the color question.

Every question and button label comes from messages.py (the one config).
"""
import logging
import re
from dataclasses import dataclass, field
from uuid import UUID

from app.agents.interpreter import Interpretation, interpret
from app.agents.messages import format_price, t
from app.agents.tools import (
    ADDRESS_TO_ARRANGE,
    EscalateArgs,
    ToolContext,
    build_summary,
    delivery_message,
    delivery_order_alert,
    describe,
    escalate_to_staff,
    looks_like_yes,
    new_order_alert,
    order_number,
    payment_message,
    place_order,
)
from app.models.schemas import ChatMessage, DraftItem, OrderDraft, Product, VariantMatch, normalize_phone
from app.services.conversation_service import utc_now
from app.services.llm_service import LLMError, LLMProvider
from app.services.supabase_service import DatabaseError, SupabaseService

logger = logging.getLogger(__name__)

BUTTON_PREFIX = "f:"
MAX_QUANTITY = 10  # per order line
MAX_QUANTITY_BUTTONS = 5
MAX_CATEGORY_BUTTONS = 8
MAX_PRODUCT_BUTTONS = 8
MAX_CART_ITEMS = 10
PRODUCT_CODE = re.compile(r"^[Pp]\d{2,6}$")  # generated codes (D40), e.g. P101
# The steps whose question shows each button. A tap on a button from an
# earlier question (the chat has moved on) is ignored. Start over and the
# language buttons work anytime; Confirm checks the summary's revision.
BUTTON_STEPS = {
    "cat": {"ask_product"}, "prod": {"ask_product"}, "col": {"ask_color"}, "size": {"ask_size"},
    "qty": {"ask_quantity"}, "more": {"ask_more", "edit_items"}, "ful": {"ask_delivery"},
    "name": {"ask_name"}, "rm": {"edit_items"}, "edit": {"confirm", "edit"},
    "back": {"edit", "edit_items"}, "sw": {"ask_switch"},
}

# Everyday Amharic words for colors and small numbers.
AMHARIC_COLORS = {
    "ነጭ": "white", "ጥቁር": "black", "ቀይ": "red", "ሰማያዊ": "blue", "አረንጓዴ": "green",
    "ቢጫ": "yellow", "ቡናማ": "brown", "ቡኒ": "brown", "ግራጫ": "grey", "ሮዝ": "pink",
    "ብርቱካናማ": "orange", "ወይን ጠጅ": "purple",
}
AMHARIC_NUMBERS = {"አንድ": 1, "ሁለት": 2, "ሶስት": 3, "ሦስት": 3, "አራት": 4, "አምስት": 5}
DELIVERY_WORDS = ("delivery", "deliver", "ይድረስ", "አድርሱ", "ማድረስ", "ይላክ", "ላኩልኝ")
PICKUP_WORDS = ("pickup", "pick up", "pick-up", "collect", "ከሱቅ", "መውሰድ", "እወስዳለሁ", "መጥቼ")
QUESTION_WORDS = {"how", "what", "when", "where", "why", "which", "who", "can", "do", "does",
                  "is", "are", "will", "ስንት", "ምን", "መቼ", "የት", "ለምን", "እንዴት", "ማን", "የትኛው"}
# Not product names, even though "hi" is inside "T-Shirt": these go to the AI.
GREETINGS = {"hi", "hello", "hey", "hii", "selam", "salam", "sup", "yo", "thanks", "thank", "you",
             "good", "morning", "afternoon", "evening", "ok", "okay", "yes", "no",
             "ሰላም", "ጤና", "ይስጥልኝ", "እንደምን", "አደሩ", "ዋሉ", "አመሰግናለሁ", "እሺ", "አዎ"}
_SIZE_ORDER = {"xxs": 0, "xs": 1, "s": 2, "m": 3, "l": 4, "xl": 5, "xxl": 6, "xxxl": 7}


@dataclass
class Reply:
    """One message to the customer, with optional buttons (rows)."""
    text: str
    buttons: list = field(default_factory=list)
    photo_url: str | None = None  # sent as a photo with the text as its caption


def _size_key(size: str) -> tuple:
    s = size.strip().lower()
    try:
        return (0, float(s), "")
    except ValueError:
        return (1, _SIZE_ORDER.get(s, 99), s)


def _rows(buttons: list[tuple[str, str]], per_row: int) -> list[list[tuple[str, str]]]:
    return [buttons[i:i + per_row] for i in range(0, len(buttons), per_row)]


def is_question(text: str) -> bool:
    words = re.findall(r"[\w']+", text.lower())
    return "?" in text or "፧" in text or bool(words and words[0] in QUESTION_WORDS) \
        or any(w in QUESTION_WORDS for w in words if not w.isascii())


def is_greeting(text: str) -> bool:
    words = re.findall(r"[\w']+", text.lower())
    return not words or all(w in GREETINGS for w in words)


def _english_color(text: str) -> str:
    """"ጥቁር" -> "black"; English stays as it is."""
    for amharic, english in AMHARIC_COLORS.items():
        if amharic in text:
            return english
    return text


def _color_key(color: str | None) -> str:
    return (color or "").strip().lower()


def product_link_code(text: str | None) -> str | None:
    """The code in a channel post's Order link: "/start p_P101" -> "P101"."""
    if not text:
        return None
    parts = text.strip().split()
    if len(parts) == 2 and parts[0].lower().split("@")[0] == "/start" and parts[1].lower().startswith("p_"):
        return parts[1][2:].upper() or None
    return None


def parse_quantity(text: str) -> int | None:
    for word, number in AMHARIC_NUMBERS.items():
        if word in text:
            return number
    match = re.fullmatch(r"\s*(\d{1,3})\s*(pcs|pieces|pairs?|x)?\s*[.!]?\s*", text.lower())
    return int(match.group(1)) if match else None


def parse_fulfillment(text: str) -> str | None:
    lowered = text.lower()
    delivery = any(w in lowered for w in DELIVERY_WORDS)
    pickup = any(w in lowered for w in PICKUP_WORDS)
    if delivery == pickup:
        return None  # neither, or both (unclear)
    return "delivery" if delivery else "pickup"


class OrderFlow:
    """Created once at startup, shared by all stores."""

    def __init__(self, db: SupabaseService, llm: LLMProvider | None, ai_daily_limit: int | None = None):
        self.db = db
        self.llm = llm
        # D21: AI calls per store per day (None: no limit). Only typed
        # messages the flow can't read by itself use the AI.
        self.ai_daily_limit = ai_daily_limit

    async def handle(self, ctx: ToolContext, history: list[ChatMessage]) -> list[Reply]:
        """Handle this run's customer messages; returns what to send.
        Changes ctx.conversation (saved by the orchestrator, version-checked)."""
        return await _Run(self, ctx, history).go()


class _Run:
    """One run: the new messages of one customer, handled in order."""

    def __init__(self, flow: OrderFlow, ctx: ToolContext, history: list[ChatMessage]):
        self.db, self.llm, self.ctx, self.history = flow.db, flow.llm, ctx, history
        self.ai_daily_limit = flow.ai_daily_limit
        self.store = ctx.store
        self.notes: list[str] = []  # shown above the next question
        self.before: list[Reply] = []  # sent before the question (the payment message)
        self.product_options: list[Product] = []  # "Which one?" buttons
        self.handover_text: str | None = None
        self.finished = False  # an order was placed: its message says what's next
        self._variants: dict[UUID, list[VariantMatch]] = {}
        self._products: dict[UUID, Product | None] = {}

    @property
    def draft(self) -> OrderDraft:
        return self.ctx.conversation.order_draft

    @property
    def language(self):
        """The language the customer chose (D29); until then, a best guess."""
        return self.draft.language or self.ctx.language

    def t(self, key: str, **values) -> str:
        return t(key, self.language, self.store, **values)

    # --- The run -------------------------------------------------------------

    async def go(self) -> list[Reply]:
        for message in self.ctx.new_messages:
            if self.handover_text or self.finished:
                break
            code = product_link_code(message.text)
            d = self.draft
            if d.switch_product_id and not (message.button_data or "").startswith(f"{BUTTON_PREFIX}sw:"):
                # Carried on without answering "finish or switch?": the other
                # product comes next, so it's never silently dropped.
                d.next_product_id, d.switch_product_id = d.switch_product_id, None
                d.step = await self.next_step()  # typed answers are for the current product
            if message.kind == "button":
                await self.on_button(message.button_data or "")
            elif self.draft.language is None:
                if code:  # opened right after the language is chosen
                    self.draft.pending_product_code = code
                continue  # the language question comes first (D29)
            elif code:
                await self.open_product_code(code)
            elif message.forwarded_post:
                await self.on_forwarded_post(*message.forwarded_post)
            elif message.kind == "photo":
                await self.on_photo(message.text)
            elif message.kind == "text":
                await self.on_text(message.text or "")
            else:  # sticker, voice, document...
                self.notes.append(self.t("please_type"))

        if self.handover_text:
            return [*self.before, Reply(self.handover_text)]
        step = await self.next_step()
        self.draft.step = step
        if self.finished:
            return self.before  # the order message already says what happens next
        question = await self.question(step)
        text = "\n\n".join([*self.notes, question.text])
        return [*self.before, Reply(text, question.buttons, question.photo_url)]

    # --- Changing the draft --------------------------------------------------

    def changed(self) -> None:
        """Any change makes an earlier summary (and its Confirm button) invalid."""
        d = self.draft
        d.revision += 1
        d.summary_revision = None
        d.summary_message_id = None
        d.customer_confirmed = False

    def new_draft(self, **fields) -> None:
        """Start a fresh order. The revision keeps counting up (order keys use
        it); the chosen language stays."""
        old = self.draft
        self.ctx.conversation.order_draft = OrderDraft(
            revision=old.revision + 1, last_order_id=old.last_order_id, language=old.language, **fields)
        self.product_options = []

    async def product(self, product_id: UUID) -> Product | None:
        if product_id not in self._products:
            self._products[product_id] = await self.db.get_product(self.store.id, product_id)
        return self._products[product_id]

    def clear_pick(self) -> None:
        """Forget the item being picked (the cart's finished items stay)."""
        d = self.draft
        d.product_id = d.product_name = None
        self.clear_choice()

    def clear_choice(self, *, keep_color: bool = False) -> None:
        d = self.draft
        if not keep_color:
            d.color = None
        d.size = d.variant_id = d.quantity = None

    async def variants(self, product_id: UUID) -> list[VariantMatch]:
        """This product's variants that can be sold now (in stock, with a price)."""
        if product_id not in self._variants:
            found = await self.db.get_product_variants(self.store.id, product_id)
            self._variants[product_id] = [v for v in found if v.available > 0 and v.price is not None]
        return self._variants[product_id]

    async def of_color(self) -> list[VariantMatch]:
        """The chosen product's variants in the chosen color."""
        d = self.draft
        if d.product_id is None or d.color is None:
            return []
        return [v for v in await self.variants(d.product_id) if _color_key(v.color) == _color_key(d.color)]

    async def select_product(self, product_id: UUID, *, ask_first: bool = False) -> bool:
        """Start picking this product. The cart's finished items stay (D32);
        after an order, a new order starts. An unfinished pick of another
        product is replaced, or with ask_first (a tap in the channel) the
        customer is asked first: finish it, or switch."""
        variants = await self.variants(product_id)
        if not variants:
            await self.sold_out(product_id)
            return False
        if self.draft.step == "payment":
            self.new_draft()
        d = self.draft
        if ask_first and d.product_id is not None and d.product_id != product_id:
            d.switch_product_id = product_id
            return True
        if d.product_id != product_id:
            d.product_id, d.product_name = product_id, variants[0].product_name
            self.clear_choice()
            self.changed()
        d.cart_closed = False  # the new item goes through "Add another?" too
        self.product_options = []
        return True

    async def sold_out(self, product_id: UUID) -> None:
        """D34: say so, and offer similar products (same category, in stock)."""
        product = await self.product(product_id)
        if product is None:
            self.notes.append(self.t("option_gone"))
            return
        self.notes.append(self.t("sold_out_product", product=product.name))
        similar = [p for p in await self.db.list_products_in_stock(self.store.id, product.category)
                   if p.id != product.id] if product.category else []
        if similar:
            self.notes.append(self.t("similar_products"))
            self.product_options = similar[:MAX_PRODUCT_BUTTONS]

    async def open_product_code(self, code: str) -> None:
        """A product link from the channel (/start p_<code>) or a typed code."""
        product = await self.db.find_product_by_code(self.store.id, code)
        if product is None:
            self.notes.append(self.t("option_gone"))
            return
        await self.select_product(product.id, ask_first=True)

    async def on_forwarded_post(self, channel_id: int, message_id: int) -> None:
        """A channel post forwarded to the bot: if it's one of the store's
        bot posts, open that product; an old hand-made post goes to staff (D38)."""
        post = await self.db.find_post(self.store.id, channel_id, message_id)
        if post is not None and post.product_id is not None:
            await self.select_product(post.product_id, ask_first=True)
            return
        await self.handover("the customer forwarded a channel post the bot doesn't know",
                            f"Forwarded post {message_id} from chat {channel_id}")

    async def select_product_by_name(self, query: str) -> bool:
        """Search the catalog; one product -> selected, several -> buttons."""
        found = await self.db.search_variants(self.store.id, query, limit=50)
        product_ids = list(dict.fromkeys(v.product_id for v in found if v.available > 0))
        if not product_ids:
            if found:  # the product exists but is sold out
                self.notes.append(self.t("option_gone"))
            return False
        if len(product_ids) == 1:
            return await self.select_product(product_ids[0])
        names = {v.product_id: v for v in found}
        self.product_options = [
            Product(id=pid, store_id=self.store.id, name=names[pid].product_name, brand=names[pid].brand)
            for pid in product_ids[:MAX_PRODUCT_BUTTONS]
        ]
        return True

    async def set_color(self, color: str) -> bool:
        """The color, by name ("black", "ጥቁር") — any size of it in stock."""
        d = self.draft
        if d.product_id is None:
            return False
        wanted = _english_color(color).strip().lower()
        for variant in await self.variants(d.product_id):
            name = _color_key(variant.color)
            if name and (wanted in name or name in wanted):
                return self.choose_color(variant.color)
        self.notes.append(self.t("color_unavailable", color=color.strip()))
        return False

    def choose_color(self, color: str | None) -> bool:
        d = self.draft
        if _color_key(d.color) != _color_key(color) or d.color is None:
            d.color = color or ""
            d.size = d.variant_id = d.quantity = None
            self.changed()
        return True

    async def set_color_by_variant(self, variant_id: UUID) -> bool:
        """A color button: its data is one variant of that color."""
        d = self.draft
        variant = next((v for v in await self.variants(d.product_id) if v.variant_id == variant_id),
                       None) if d.product_id else None
        if variant is None:
            self.notes.append(self.t("option_gone"))
            return False
        return self.choose_color(variant.color)

    async def set_size(self, size: str) -> bool:
        """A size of the chosen color."""
        d = self.draft
        if d.product_id is None or d.color is None:
            return False
        wanted = size.strip().lower().removeprefix("size").strip()
        variant = next((v for v in await self.of_color() if (v.size or "").lower() == wanted), None)
        if variant is None:
            self.notes.append(self.t("size_unavailable", size=size.strip()))
            return False
        if d.variant_id != variant.variant_id:
            d.size, d.variant_id = variant.size, variant.variant_id
            if d.quantity and d.quantity > variant.available:
                d.quantity = None
            self.changed()
        return True

    async def set_quantity(self, quantity: int) -> bool:
        d = self.draft
        variant = await self.chosen_variant()
        if variant is None or quantity < 1:
            return False
        if quantity > min(variant.available, MAX_QUANTITY):
            self.notes.append(self.t("too_many"))
            return False
        if d.quantity != quantity:
            d.quantity = quantity
            self.changed()
        return True

    def set_fulfillment(self, method: str) -> None:
        d = self.draft
        if d.fulfillment_method != method:
            d.fulfillment_method = method
            if method == "pickup":
                d.delivery_address = None
            self.changed()

    def set_text_field(self, name: str, value: str) -> None:
        d = self.draft
        value = value.strip()
        if value and getattr(d, name) != value:
            setattr(d, name, value)
            self.changed()

    def set_phone(self, text: str) -> bool:
        try:
            phone = normalize_phone(text)
        except ValueError:
            self.notes.append(self.t("invalid_phone"))
            return False
        if self.draft.contact_phone != phone:
            self.draft.contact_phone = phone
            self.changed()
        return True

    async def chosen_variant(self) -> VariantMatch | None:
        d = self.draft
        if d.product_id is None or d.variant_id is None:
            return None
        return next((v for v in await self.variants(d.product_id) if v.variant_id == d.variant_id), None)

    def chosen_label(self) -> str:
        """The customer's choice in words, e.g. "Air Force 1, White, size 42"."""
        d = self.draft
        parts = [d.product_name or "", d.color or ""]
        if d.size:
            parts.append(self.t("size", size=d.size))
        return ", ".join(p for p in parts if p)

    def edit(self, part: str) -> None:
        """Go back to a step from the summary: clear it (and what depends on it)."""
        d = self.draft
        if part == "product":
            d.product_id = d.product_name = None
            self.clear_choice()
        elif part == "color":
            self.clear_choice()
        elif part == "size":
            self.clear_choice(keep_color=True)
        elif part == "quantity":
            d.quantity = None
        elif part == "delivery":
            d.fulfillment_method = d.delivery_address = None
        elif part == "contact":
            d.contact_name = d.contact_phone = None
            d.ask_contact_again = True
        elif part == "items":
            d.step = "edit_items"
            return
        else:
            self.notes.append(self.t("option_gone"))
            return
        d.step = "ask_product"  # anything but "edit"; next_step() finds the real one
        self.changed()

    # --- Handling messages ---------------------------------------------------

    async def on_button(self, data: str) -> None:
        if not data.startswith(BUTTON_PREFIX):
            self.notes.append(self.t("option_gone"))
            return
        action, _, value = data.removeprefix(BUTTON_PREFIX).partition(":")
        d = self.draft
        if action == "lang":
            if value in ("am", "en"):
                d.language = value
                if d.pending_product_code:  # a product link that came before the language
                    code, d.pending_product_code = d.pending_product_code, None
                    await self.open_product_code(code)
            else:
                d.language = None  # "change language": ask again
            return
        if d.language is None:
            return  # the language question comes first
        if action in BUTTON_STEPS and d.step not in BUTTON_STEPS[action]:
            # A button from an earlier question: the chat has moved on.
            self.notes.append(self.t("option_gone"))
            return
        try:
            if action == "restart":
                self.new_draft()
                self.notes.append(self.t("started_over"))
            elif action == "cat":
                category = d.category_options[int(value)]
                if d.step == "payment":
                    self.new_draft()
                self.draft.category = category
                products = await self.db.list_products_in_stock(self.store.id, category)
                if len(products) == 1:
                    await self.select_product(products[0].id)
                elif products:
                    self.product_options = products[:MAX_PRODUCT_BUTTONS]
                else:
                    self.notes.append(self.t("option_gone"))
            elif action == "prod":
                await self.select_product(UUID(value))
            elif action == "sw" and d.switch_product_id:  # "finish first" or "switch now"
                other, d.switch_product_id = d.switch_product_id, None
                if value == "now":
                    self.clear_pick()
                    await self.select_product(other)
                else:
                    d.next_product_id = other
            elif action == "col":
                await self.set_color_by_variant(UUID(value))
            elif action == "size":
                await self.set_size(value)
            elif action == "qty":
                await self.set_quantity(int(value))
            elif action == "ful" and value in ("delivery", "pickup"):
                self.set_fulfillment(value)
            elif action == "name" and self.ctx.customer.name:
                self.set_text_field("contact_name", self.ctx.customer.name)
            elif action == "confirm":
                # Only the Confirm button of the CURRENT summary places the order.
                if d.step == "confirm" and d.summary_revision == d.revision == int(value):
                    await self.place()
                else:
                    self.notes.append(self.t("option_gone"))
            elif action == "more" and value == "add":  # D32: add another item
                d.adding_item, d.cart_closed = True, False
                self.clear_pick()
                d.step = "ask_product"
            elif action == "more" and value == "done":
                d.cart_closed, d.adding_item = True, False
            elif action == "rm":  # remove a cart item (from the edit screen)
                del d.items[int(value)]
                self.changed()
                d.step = "edit_items" if d.items else "ask_product"
            elif action == "edit" and not value:
                d.step = "edit"
            elif action == "edit" and value == "items":
                d.step = "edit_items"
            elif action == "edit":
                self.edit(value)
            elif action == "back":
                d.step = "confirm"
            else:
                self.notes.append(self.t("option_gone"))
        except (ValueError, IndexError):
            self.notes.append(self.t("option_gone"))

    async def on_photo(self, caption: str | None = None) -> None:
        if self.draft.last_order_id:
            # After an order, a photo is usually the payment screenshot: staff check it.
            summary = f"Photo after order #{order_number(self.draft.last_order_id)}"
            if caption:
                summary += f". Caption: {caption[:300]}"
            await self.handover("customer sent a photo (check it, e.g. a payment screenshot)",
                                summary, reply_key="photo_reply")
        else:
            self.notes.append(self.t("photo_not_product"))

    async def on_text(self, text: str) -> None:
        text = text.strip()
        command = text.lower().split("@")[0]
        if command == "/start":  # Telegram's "Start" button
            self.new_draft()
            return
        if command == "/help":  # how to order; the current question follows
            self.notes.append(self.t("help"))
            return
        if await self.direct_answer(text):
            return
        await self.ask_ai(text)

    async def direct_answer(self, text: str) -> bool:
        """Does the text simply answer the current step? (No AI needed.)"""
        d = self.draft
        step = d.step
        question = is_question(text)
        if step in ("ask_product", "payment") and PRODUCT_CODE.fullmatch(text):
            await self.open_product_code(text)  # a code from a channel post, e.g. P101
            return True
        if (step in ("ask_product", "payment") and not question and 3 <= len(text) <= 60
                and not is_greeting(text)):
            return await self.select_product_by_name(text)
        if step == "ask_color" and d.product_id and len(text.split()) <= 3 and not question:
            colors = {_color_key(v.color) for v in await self.variants(d.product_id)}
            wanted = _english_color(text).lower()
            if any(c and (wanted in c or c in wanted) for c in colors):
                return await self.set_color(text)
        if step == "ask_size" and d.product_id and len(text.split()) <= 3:
            wanted = text.lower().replace("ቁጥር", "").replace("size", "").strip()
            # A size, or anything that looks like one ("50", "XXL"): say if
            # it isn't available instead of asking the AI.
            if re.fullmatch(r"\d{1,3}(\.5)?|x{0,3}[sml]|xx?l", wanted) or \
                    wanted in {(v.size or "").lower() for v in await self.of_color()}:
                await self.set_size(wanted)
                return True
        if step == "ask_quantity":
            quantity = parse_quantity(text)
            if quantity is not None:
                await self.set_quantity(quantity)
                return True
        if step == "ask_delivery" and len(text.split()) <= 4:
            method = parse_fulfillment(text)
            if method:
                self.set_fulfillment(method)
                return True
        if step == "ask_name" and not question and not re.search(r"\d", text) \
                and 1 <= len(text.split()) <= 4 and len(text) <= 60:
            self.set_text_field("contact_name", text)
            return True
        if step == "ask_phone" and not question and (
                re.fullmatch(r"[\d\s+\-().]+", text) or len(re.sub(r"\D", "", text)) >= 7):
            self.set_phone(text)  # says so if it isn't a valid number
            return True
        if step == "confirm" and looks_like_yes(text) and d.summary_revision == d.revision:
            await self.place()
            return True
        return False

    async def ask_ai(self, text: str) -> None:
        if self.llm is None:
            await self.handover("the bot couldn't understand the message (AI not configured)", text)
            return
        if not await self.ai_allowed(text):
            return
        products = await self.db.list_products(self.store.id)
        try:
            result = await interpret(self.llm, self.store, products, self.draft, self.history, text,
                                     self.language)
        except LLMError as error:
            # The AI is down: don't keep the customer waiting for retries.
            logger.warning("AI unavailable; handed to staff", extra={"reason": error.reason})
            await self.handover("the bot's AI is unavailable right now, so it couldn't read this", text)
            return
        logger.info("message interpreted", extra={"intent": result.intent})
        if result.intent == "handover":
            await self.handover(result.reason or "needs a person", text)
        elif result.intent == "start_over":
            self.new_draft()
            self.notes.append(self.t("started_over"))
        elif result.intent == "order_status":
            await self.order_status()
        elif result.intent == "side_question":
            if result.reply:
                self.notes.append(result.reply.strip())
            else:
                await self.handover("a question the bot couldn't answer", text)
        else:
            await self.apply(result)

    async def apply(self, found: Interpretation) -> None:
        """Use the details the AI found, each checked against the database."""
        d = self.draft
        if found.product:
            current = (d.product_name or "").lower()
            if d.step == "payment" or not current or found.product.lower() not in current:
                if not await self.select_product_by_name(found.product):
                    self.notes.append(self.t("no_match", query=found.product))
                    return
                if self.product_options:
                    return  # the customer picks which one first
        if self.draft.product_id:
            if found.color:
                await self.set_color(found.color)
            elif found.size and self.draft.color is None:
                # A size but no color: if only one color comes in that size, use it.
                wanted = found.size.strip().lower()
                colors = {_color_key(v.color): v.color for v in await self.variants(self.draft.product_id)
                          if (v.size or "").lower() == wanted}
                if len(colors) == 1:
                    self.choose_color(next(iter(colors.values())))
            await self.autopick()
            if found.size:
                await self.set_size(found.size)
            await self.autopick()
            if found.quantity:
                await self.set_quantity(found.quantity)
        if found.fulfillment:
            self.set_fulfillment(found.fulfillment)
            d.cart_closed = True  # moving on to delivery: no "add another item?"
        if found.address:
            self.set_text_field("delivery_address", found.address)
        if found.name:
            self.set_text_field("contact_name", found.name)
        if found.phone:
            self.set_phone(found.phone)

    async def autopick(self) -> None:
        """Only one color, or only one size of the chosen color? Choose it."""
        d = self.draft
        if d.product_id is None:
            return
        if d.color is None:
            colors = {_color_key(v.color): v.color for v in await self.variants(d.product_id)}
            if len(colors) == 1:
                self.choose_color(next(iter(colors.values())))
        if d.color is not None and d.variant_id is None:
            sizes = await self.of_color()
            if len(sizes) == 1:
                d.size, d.variant_id = sizes[0].size, sizes[0].variant_id
                self.changed()

    async def ai_allowed(self, text: str) -> bool:
        """D21: count this AI call against the store's daily limit. Over the
        limit, the message goes to staff (the first time each day, the alert
        says why). If the counter can't be reached, the call is allowed."""
        if self.ai_daily_limit is None:
            return True
        try:
            calls = await self.db.use_ai_call(self.store.id)
        except DatabaseError:
            logger.warning("AI budget not checked (database error)")
            return True
        if calls <= self.ai_daily_limit:
            return True
        reason = "the store's daily AI limit is reached"
        if calls == self.ai_daily_limit + 1:
            logger.warning("daily AI limit reached", extra={"limit": self.ai_daily_limit})
            reason += (f" ({self.ai_daily_limit} AI calls today). Until midnight, typed messages the "
                       "bot can't read come to you; buttons and orders still work")
        await self.handover(reason, text)
        return False

    async def handover(self, reason: str, summary: str, reply_key: str = "handover_reply") -> None:
        await escalate_to_staff(EscalateArgs(
            reason=reason,
            summary=f"{summary}\n(Order step: {self.draft.step}; chosen so far: {self.chosen_label() or 'nothing yet'})",
        ), self.ctx)
        self.handover_text = self.t(reply_key)

    async def order_status(self) -> None:
        orders = await self.db.get_customer_orders(self.store.id, self.ctx.customer.id, limit=3)
        if not orders:
            self.notes.append(self.t("no_orders"))
            return
        self.notes.append("\n".join(
            self.t("order_status_line", number=order_number(o.id), status=self.t(f"status_{o.status}"))
            for o in orders))

    async def place(self) -> None:
        d = self.draft
        if not await self.cart_items_available() or not d.items or                 (d.fulfillment_method == "pickup" and d.missing_fields()):
            return  # sold out meanwhile: next_step() shows the cart again
        delivery = d.fulfillment_method == "delivery"
        if delivery and not d.delivery_address:
            d.delivery_address = ADDRESS_TO_ARRANGE  # staff arrange it by phone (D29)
        result = await place_order(self.ctx)
        if result.order is not None:
            order = result.order
            if delivery:
                # D29: pay on delivery: staff call the customer about the address.
                self.ctx.staff_alerts[-1] = delivery_order_alert(
                    self.ctx.customer, order, self.ctx.new_messages[-1].customer_username, self.store)
                self.before.append(Reply(delivery_message(self.store, order, self.language)))
                conversation = self.ctx.conversation
                conversation.bot_paused, conversation.paused_at = True, utc_now()
                conversation.staff_active_at = None
            else:
                self.before.append(Reply(payment_message(self.store, order, self.language),
                                         [(self.t("btn_start_over"), "f:restart")]))
            self.new_draft(step="payment")
            self.draft.last_order_id = order.id
            self.finished = True
        elif result.sold_out_variant is not None:
            if delivery and d.delivery_address == ADDRESS_TO_ARRANGE:
                d.delivery_address = None
            # Stock changed since the check: remove that item and show the cart again.
            gone = next((i for i in d.items if i.variant_id == result.sold_out_variant), None)
            if gone is not None:
                [variant] = await self.db.get_variants(self.store.id, [gone.variant_id]) or [None]
                self.drop_item(gone, variant)
        else:
            await self.handover(f"the order was refused ({result.refused})", "placing the order failed")

    # --- Which step, and its question ----------------------------------------

    async def next_step(self) -> str:
        """The first answer still missing (or confirm / edit / payment)."""
        d = self.draft
        if d.language is None:
            return "choose_language"
        if d.step == "payment" and d.product_id is None:
            return "payment"
        if d.switch_product_id:
            if d.product_id is not None:
                return "ask_switch"
            d.next_product_id, d.switch_product_id = d.switch_product_id, None  # nothing to finish
        if d.step in ("edit", "edit_items"):
            return d.step
        if d.product_id is None:
            if d.next_product_id:  # "finish first": now the other product
                other, d.next_product_id = d.next_product_id, None
                if await self.select_product(other):
                    return await self.next_step()
            return await self.cart_step()
        variants = await self.variants(d.product_id)
        if not variants:  # sold out since it was chosen
            self.notes.append(self.t("option_gone"))
            d.product_id = d.product_name = None
            self.clear_choice()
            self.changed()
            return "ask_product"

        # Color first (D29), then the sizes of that color.
        colors = {_color_key(v.color): v.color for v in variants}
        if d.color is not None and _color_key(d.color) not in colors:
            self.notes.append(self.t("sold_out_now", item=self.chosen_label()))
            self.clear_choice()
            self.changed()
        if d.color is None:
            if len(colors) > 1:
                return "ask_color"
            self.choose_color(next(iter(colors.values())))
        of_color = await self.of_color()
        if d.variant_id not in {v.variant_id for v in of_color}:
            if d.variant_id is not None:
                # The customer's choice sold out meanwhile: never switch it for
                # them; say so and ask again.
                self.notes.append(self.t("sold_out_now", item=self.chosen_label()))
                d.size = d.variant_id = d.quantity = None
                self.changed()
                return "ask_size"
            if len(of_color) > 1:
                return "ask_size"
            d.size, d.variant_id = of_color[0].size, of_color[0].variant_id
            self.changed()
        variant = await self.chosen_variant()
        if d.quantity is None or d.quantity > variant.available:
            d.quantity = None
            return "ask_quantity"
        self.add_to_cart(variant, d.quantity)
        return await self.next_step()  # a product kept for later comes next, or the cart

    def add_to_cart(self, variant: VariantMatch, quantity: int) -> None:
        """The picked item goes into the cart (the same variant twice: added up)."""
        d = self.draft
        for item in d.items:
            if item.variant_id == variant.variant_id:
                item.quantity = min(item.quantity + quantity, variant.available, MAX_QUANTITY)
                break
        else:
            if len(d.items) < MAX_CART_ITEMS:
                d.items.append(DraftItem(variant_id=variant.variant_id, quantity=quantity,
                                         description=describe(variant, store=self.store)))
        self.clear_pick()
        d.adding_item = False
        self.changed()

    async def cart_items_available(self) -> bool:
        """Check the cart against the database. False if something sold out
        meanwhile: it is removed from the cart."""
        d = self.draft
        found = {v.variant_id: v for v in await self.db.get_variants(
            self.store.id, [i.variant_id for i in d.items])}
        gone = [i for i in d.items if (v := found.get(i.variant_id)) is None
                or v.price is None or v.available < i.quantity]
        for item in gone:
            self.drop_item(item, found.get(item.variant_id))
        return not gone

    def drop_item(self, item: DraftItem, variant: VariantMatch | None) -> None:
        """A cart item sold out. If it was the only one, that product is opened
        again so the customer chooses another size (never switched for them);
        otherwise it is removed with a note and the summary shows the rest."""
        d = self.draft
        d.items.remove(item)
        self.changed()
        if not d.items and d.product_id is None and variant is not None:
            d.product_id, d.product_name, d.color = variant.product_id, variant.product_name, variant.color
            d.size, d.variant_id = variant.size, variant.variant_id  # next_step() says it sold out
            self._variants.pop(variant.product_id, None)
            return
        self.notes.append(self.t("sold_out_now", item=item.description))

    async def cart_step(self) -> str:
        """No item being picked: pick one, ask for more, or go on to delivery."""
        d = self.draft
        if not d.items or d.adding_item:
            return "ask_product"
        if not d.cart_closed:
            return "ask_more"
        if d.fulfillment_method is None:
            return "ask_delivery"
        customer = self.ctx.customer
        if not d.ask_contact_again and customer.phone and not d.contact_phone:
            # A customer we already know: skip the contact questions (D28).
            d.contact_phone = customer.phone
            d.contact_name = d.contact_name or customer.name
        if not d.contact_name:
            return "ask_name"
        if not d.contact_phone:
            return "ask_phone"
        if not await self.cart_items_available():  # sold out meanwhile
            return await self.next_step()
        if not d.items:
            d.cart_closed = False
            return "ask_product"
        return "confirm"

    def cart_lines(self) -> str:
        return "\n".join(f"• {i.description} × {i.quantity}" for i in self.draft.items)

    def with_start_over(self, buttons: list) -> list:
        return [*buttons, (self.t("btn_start_over"), "f:restart")]

    async def question(self, step: str) -> Reply:
        d = self.draft
        if step == "choose_language":
            return Reply(self.t("choose_language"),
                         [[(t("btn_lang_am", "am"), "f:lang:am"), (t("btn_lang_en", "en"), "f:lang:en")]])

        if step == "ask_product":
            if self.product_options:
                buttons = [(p.name + (f" ({p.brand})" if p.brand else ""), f"f:prod:{p.id}")
                           for p in self.product_options]
                return Reply(self.t("ask_product_pick"), self.with_start_over(_rows(buttons, 1)))
            categories = (await self.db.list_categories(self.store.id))[:MAX_CATEGORY_BUTTONS]
            d.category_options = categories
            buttons = _rows([(c.capitalize(), f"f:cat:{i}") for i, c in enumerate(categories)], 2)
            buttons.append((self.t("btn_change_language"), "f:lang:"))
            key = "ask_product" if categories else "ask_product_no_categories"
            return Reply(self.t(key), self.with_start_over(buttons))

        if step == "ask_color":
            variants = await self.variants(d.product_id)
            prices = {v.price for v in variants}
            price = format_price(min(prices), self.language)
            if len(prices) > 1:
                price = self.t("price_from", price=price)
            one_per_color = {}
            for v in variants:  # a color button carries one variant of that color
                one_per_color.setdefault(_color_key(v.color), v)
            buttons = [(v.color or "—", f"f:col:{v.variant_id}") for v in one_per_color.values()]
            product = await self.product(d.product_id)
            return Reply(self.t("ask_color", product=d.product_name, price=price),
                         self.with_start_over(_rows(buttons, 2)),
                         photo_url=product.photo_url if product else None)

        if step == "ask_size":
            sizes = sorted({v.size for v in await self.of_color() if v.size}, key=_size_key)
            buttons = [(s, f"f:size:{s}") for s in sizes]
            return Reply(self.t("ask_size"), self.with_start_over(_rows(buttons, 4)))

        if step == "ask_quantity":
            variant = await self.chosen_variant()
            most = min(variant.available, MAX_QUANTITY_BUTTONS)
            buttons = [(str(n), f"f:qty:{n}") for n in range(1, most + 1)]
            return Reply(self.t("ask_quantity"), self.with_start_over([buttons]))

        if step == "ask_switch":
            other = await self.product(d.switch_product_id)
            current, new = d.product_name or "", other.name if other else ""
            buttons = [[(self.t("btn_switch_finish", product=current)[:60], "f:sw:finish")],
                       [(self.t("btn_switch_now", product=new)[:60], "f:sw:now")]]
            return Reply(self.t("ask_switch", current=current, new=new), self.with_start_over(buttons))

        if step == "ask_more":
            buttons = [[(self.t("btn_add_item"), "f:more:add"), (self.t("btn_continue"), "f:more:done")]]
            return Reply(self.t("ask_more", items=self.cart_lines()), self.with_start_over(buttons))

        if step == "ask_delivery":
            buttons = [(self.t("btn_delivery"), "f:ful:delivery"), (self.t("btn_pickup"), "f:ful:pickup")]
            return Reply(self.t("ask_delivery"), self.with_start_over([buttons]))

        if step == "ask_name":
            known = self.ctx.customer.name
            if not known:
                return Reply(self.t("ask_name"), self.with_start_over([]))
            return Reply(f"{self.t('ask_name')}\n{self.t('ask_name_known')}",
                         self.with_start_over([(self.t("btn_use", value=known[:40]), "f:name:saved")]))

        if step == "ask_phone":
            return Reply(self.t("ask_phone"), self.with_start_over([]))

        if step == "confirm":
            variants = {v.variant_id: v for v in await self.db.get_variants(
                self.store.id, [i.variant_id for i in d.items])}
            summary = build_summary(d, variants, self.language, self.store,
                                    closing_key="summary_buttons")
            d.summary_revision = d.revision
            buttons = [[(self.t("btn_confirm"), f"f:confirm:{d.revision}"), (self.t("btn_edit"), "f:edit")]]
            return Reply(summary, self.with_start_over(buttons))

        if step == "edit":
            parts = ["items", "delivery", "contact"]
            buttons = [(self.t(f"btn_edit_{p}"), f"f:edit:{p}") for p in parts]
            return Reply(self.t("ask_edit"), self.with_start_over(
                [buttons, (self.t("btn_back_to_summary"), "f:back")]))

        if step == "edit_items":
            buttons = [(self.t("btn_remove", item=f"{i.description} × {i.quantity}")[:60], f"f:rm:{n}")
                       for n, i in enumerate(d.items)]
            buttons += [(self.t("btn_add_item"), "f:more:add"), (self.t("btn_back_to_summary"), "f:back")]
            return Reply(self.t("ask_edit_items", items=self.cart_lines()), self.with_start_over(buttons))

        # payment: waiting for the screenshot
        return Reply(self.t("payment_waiting"), self.with_start_over([]))
