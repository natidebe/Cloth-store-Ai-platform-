"""The staff group: alerts with buttons, staff replies, and handing chats back.

What staff see in their Telegram group, and what they can do there:

- Alerts about a customer (hand-over, payment screenshot, new order, ...).
  Each can have buttons:
    [✅ Confirm payment #…]  reduce stock, mark the order paid, tell the
                             customer, and hand the chat back to the bot
    [▶️ Hand back to bot]    the bot starts answering this customer again
- The order's next steps, each telling the customer (Phase 15/15b, D74–D77):
    pickup:    [✅ Confirm payment] → [📦 Ready for pickup] [✅ Picked up]
    delivery (paid on arrival):
               [🚚 On the way] (stock goes down) → [✅ Delivered & paid] [❌ Not delivered]
  The buttons always follow the order's stage (order_buttons): a stage never
  goes back, and a second tap changes nothing.
- While the bot is paused for a customer, everything the customer writes is
  posted in the group.
- To answer a customer, a staff member uses Telegram's "Reply" on any of
  those messages. The bot sends their text to the customer.
- D9: a chat nobody on the staff side has touched for 2 hours goes back to
  the bot automatically.

Anyone in the staff group may act (decided in Phase 9); we record who did.
Every bot message in the group is recorded in staff_messages, so we know
which customer (and order) a reply or button press is about.
"""
import logging
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from app.agents.messages import detect_language, format_price, t
from app.agents.tools import StaffAlert, order_number
from app.models.schemas import (
    ChatMessage,
    Conversation,
    OrderWithItems,
    StaffMessage,
    Store,
    TelegramCallbackQuery,
    TelegramMessage,
    TelegramUpdate,
)
from app.services.conversation_service import HISTORY_LIMIT, ConversationStore, utc_now
from app.services.supabase_service import (
    NotFoundError,
    OrderRejectedError,
    OutOfStockError,
    SupabaseService,
    VersionConflictError,
)
from app.services.telegram_service import TelegramError, TelegramService
from app.utils.logging import log_context

logger = logging.getLogger(__name__)

# D9: the bot takes a handed-over chat back after this long without staff activity.
BOT_RESUMES_AFTER = timedelta(hours=2)

# What the buttons send back to us (Telegram allows up to 64 characters).
PAY_BUTTON = "pay:"  # + order id
HAND_BACK_BUTTON = "resume:"  # + customer's Telegram id
# Phase 15/15b (D74–D77): the order's next steps, + order id.
SHIP_BUTTON = "ship:"  # delivery: on the way (unpaid: the stock goes down)
READY_BUTTON = "ready:"  # pickup: ready (a message only; the stage stays "confirmed")
DONE_BUTTON = "done:"  # delivered (& paid) / picked up
BACK_BUTTON = "back:"  # delivery: not delivered (the stock comes back, the order is cancelled)

_MAX_SAVE_TRIES = 3


@dataclass
class PaymentResult:
    ok: bool
    message: str  # for the staff member (button notice or API response)
    customer_telegram_id: int | None = None


def order_buttons(order: OrderWithItems, ready_sent: bool = False) -> list[tuple[str, str]]:
    """What staff can do next with this order, from its stage. `ready_sent`:
    "Ready for pickup" was just tapped (it doesn't change the stage)."""
    number, oid = order_number(order.id), order.id
    if order.status in ("cancelled", "delivered"):
        return []
    paid = order.payment_status == "paid"
    if order.fulfillment_method == "delivery":  # paid on arrival (D76)
        if order.status in ("pending", "confirmed"):
            return [(f"🚚 On the way #{number}", f"{SHIP_BUTTON}{oid}")]
        if paid:
            return [(f"✅ Delivered #{number}", f"{DONE_BUTTON}{oid}")]
        return [(f"✅ Delivered & paid #{number}", f"{DONE_BUTTON}{oid}"),
                (f"❌ Not delivered #{number}", f"{BACK_BUTTON}{oid}")]
    if not paid:
        return [(f"✅ Confirm payment #{number}", f"{PAY_BUTTON}{oid}")]
    picked_up = (f"✅ Picked up #{number}", f"{DONE_BUTTON}{oid}")
    return [picked_up] if ready_sent else [(f"📦 Ready for pickup #{number}", f"{READY_BUTTON}{oid}"), picked_up]


class StaffDesk:
    """Created once at startup, shared by all stores."""

    def __init__(self, db: SupabaseService, conversations: ConversationStore,
                 telegram: TelegramService):
        self.db = db
        self.conversations = conversations
        self.telegram = telegram

    # --- Posting in the staff group -----------------------------------------

    async def send_alert(self, store: Store, alert: StaffAlert) -> bool:
        """Post an alert in the store's staff group, with its buttons, and
        remember which customer it's about. False if the store has no staff
        group yet (then staff are not told: set stores.staff_chat_id)."""
        if store.staff_chat_id is None or store.telegram_bot_token is None:
            logger.warning("store has no staff group; staff not notified",
                           extra={"store_id": str(store.id)})
            return False
        token = store.telegram_bot_token.get_secret_value()
        buttons: list[tuple[str, str]] = []
        if alert.order_id:
            order = await self.db.get_order(store.id, alert.order_id)
            if order is not None:  # its next step: Confirm payment, On the way, Delivered & paid...
                buttons.extend(order_buttons(order))
        if alert.hand_back:
            buttons.append(("▶️ Hand back to bot", f"{HAND_BACK_BUTTON}{alert.telegram_id}"))
        if alert.photo_file_id:
            message_id = await self.telegram.send_photo(
                token, store.staff_chat_id, alert.photo_file_id, alert.text, buttons=buttons)
        else:
            message_id = await self.telegram.send_message(
                token, store.staff_chat_id, alert.text, buttons=buttons)
        if message_id:
            await self.conversations.save_staff_message(StaffMessage(
                store_id=store.id, staff_chat_id=store.staff_chat_id, message_id=message_id,
                telegram_id=alert.telegram_id, order_id=alert.order_id,
            ))
        return True

    async def _note(self, store: Store, text: str, reply_to: int | None = None) -> None:
        """A short note in the staff group (best effort)."""
        try:
            await self.telegram.send_message(store.telegram_bot_token.get_secret_value(),
                                             store.staff_chat_id, text, reply_to=reply_to)
        except TelegramError as error:
            logger.error("staff note not delivered", extra={"error": error.description})

    # --- Updates from the staff group (called by the webhook) ----------------

    @staticmethod
    def is_chat_id_request(update: TelegramUpdate) -> bool:
        """/chatid sent in a group or channel: replies with its id (for
        stores.staff_chat_id or stores.channel_id by hand). The dashboard way
        is /link <code> (Phase 9b, agents/onboarding.py)."""
        message = update.message or update.channel_post
        return (message is not None and message.chat.type in ("group", "supergroup", "channel")
                and (message.text or "").split("@")[0].strip().lower() == "/chatid")

    async def send_chat_id(self, store: Store, update: TelegramUpdate) -> None:
        message = update.message or update.channel_post
        chat_id = message.chat.id
        field_name = "channel_id" if message.chat.type == "channel" else "staff_chat_id"
        try:
            await self.telegram.send_message(
                store.telegram_bot_token.get_secret_value(), chat_id,
                f"This chat's id is {chat_id}\n"
                f"Put this number, including the minus sign, in {field_name} for your store.",
                reply_to=message.message_id)
        except TelegramError as error:
            logger.error("chat id not sent", extra={"error": error.description})

    @staticmethod
    def is_staff_update(store: Store, update: TelegramUpdate) -> bool:
        """A button pressed in a group (the staff group's buttons), or a
        message in this store's staff group. A customer tapping buttons in
        their private chat is NOT a staff update: it goes through the inbox
        like any customer message (the order flow's buttons)."""
        press = update.callback_query
        if press is not None:
            return press.message is not None and press.message.chat.type != "private"
        message = update.message
        return (message is not None and store.staff_chat_id is not None
                and message.chat.id == store.staff_chat_id)

    async def handle(self, store: Store, update: TelegramUpdate) -> None:
        """Runs in the background; never raises (errors are logged)."""
        with log_context(store_id=str(store.id), update_id=update.update_id):
            try:
                if update.callback_query is not None:
                    await self._button(store, update.callback_query)
                elif update.message is not None:
                    await self._reply(store, update.message)
            except Exception:
                logger.exception("staff update failed")

    async def _reply(self, store: Store, message: TelegramMessage) -> None:
        """A staff member replied to one of our messages: send it to the customer."""
        target = message.reply_to_message
        sender = message.from_user
        if target is None or sender is None or sender.is_bot:
            return  # ordinary chat between staff
        link = await self.conversations.find_staff_message(store.id, message.chat.id, target.message_id)
        if link is None:
            return  # a reply to something that isn't about a customer
        token = store.telegram_bot_token.get_secret_value()
        if not message.text:
            await self._note(store, "Only text replies can be sent to the customer for now.",
                             reply_to=message.message_id)
            return

        with log_context(telegram_id=link.telegram_id):
            try:
                await self.telegram.send_message(token, link.telegram_id, message.text)
            except TelegramError as error:
                await self._note(store, f"⚠️ Not delivered to the customer: {error.description}",
                                 reply_to=message.message_id)
                return
            # Staff are now talking to this customer: the bot stays out of it.
            conversation, was_paused = await self._set_paused(store.id, link.telegram_id, True)
            await self.conversations.add_messages(
                store.id, conversation.id, [ChatMessage(role="staff", content=message.text)])
            logger.info("staff reply sent", extra={"staff": sender.full_name})
            if not was_paused:
                await self.send_alert(store, StaffAlert(
                    text=(f"✋ {sender.full_name} took over this chat. The bot stays silent "
                          "until you hand it back (or after 2 hours without staff replies)."),
                    telegram_id=link.telegram_id, hand_back=True,
                ))

    async def _button(self, store: Store, press: TelegramCallbackQuery) -> None:
        token = store.telegram_bot_token.get_secret_value()
        message = press.message
        if (message is None or store.staff_chat_id is None
                or message.chat.id != store.staff_chat_id or not press.data):
            await self.telegram.answer_button(token, press.id, "This button can't be used here.")
            return
        staff_name = press.from_user.full_name

        if press.data.startswith(HAND_BACK_BUTTON):
            telegram_id = int(press.data.removeprefix(HAND_BACK_BUTTON))
            with log_context(telegram_id=telegram_id):
                resumed = await self.hand_back(store, telegram_id)
            if resumed:
                await self.telegram.answer_button(token, press.id, "▶️ The bot is answering this customer again.")
                await self._note(store, f"▶️ Handed back to the bot by {staff_name}.",
                                 reply_to=message.message_id)
            else:
                await self.telegram.answer_button(token, press.id, "The bot was already answering this customer.")

        elif press.data.startswith(PAY_BUTTON):
            order_id = UUID(press.data.removeprefix(PAY_BUTTON))
            result = await self.confirm_payment(
                store, order_id, confirmer_telegram_id=press.from_user.id, confirmer_name=staff_name)
            # A refusal (e.g. sold out) shows as a box the staff member must close.
            await self.telegram.answer_button(token, press.id, result.message, popup=not result.ok)
            if result.ok:
                await self._next_steps_note(store, order_id, result.customer_telegram_id,
                                            f"{result.message} Confirmed by {staff_name}.",
                                            reply_to=message.message_id)
                try:
                    await self.telegram.remove_buttons(token, message.chat.id, message.message_id)
                except TelegramError:
                    pass  # the buttons stay; pressing again says "already paid"

        elif press.data.startswith((SHIP_BUTTON, READY_BUTTON, DONE_BUTTON, BACK_BUTTON)):
            step, _, raw_id = press.data.partition(":")
            order_id = UUID(raw_id)
            result = await self.advance_order(store, order_id, step, staff_telegram_id=press.from_user.id,
                                              staff_name=staff_name)
            await self.telegram.answer_button(token, press.id, result.message, popup=not result.ok)
            if result.ok:
                await self._note(store, f"{result.message} ({staff_name})", reply_to=message.message_id)
                order = await self.db.get_order(store.id, order_id)
                try:  # only what's still to do
                    await self.telegram.set_buttons(
                        token, message.chat.id, message.message_id,
                        order_buttons(order, ready_sent=step == "ready") if order else [])
                except TelegramError:
                    pass  # the old buttons stay; pressing again changes nothing
        else:
            await self.telegram.answer_button(token, press.id, "Unknown button.")

    # --- Actions (also used by the dashboard endpoints) ----------------------

    async def confirm_payment(
        self,
        store: Store,
        order_id: UUID,
        *,
        amount: Decimal | None = None,
        method: str | None = None,
        staff_user_id: UUID | None = None,
        confirmer_telegram_id: int | None = None,
        confirmer_name: str | None = None,
    ) -> PaymentResult:
        """Staff confirmed a payment: reduce stock, mark the order paid (one
        step, in the database), tell the customer, and hand the chat back to
        the bot. Refused, with nothing changed, if an item sold out (D3)."""
        order = await self.db.get_order(store.id, order_id)
        if order is None:
            return PaymentResult(False, "Order not found.")
        number = order_number(order.id)
        amount = amount if amount is not None else order.total_price
        if amount is None or amount <= 0:
            return PaymentResult(False, f"Order #{number} has no total; confirm it with an amount.")

        try:
            payment_id = await self.db.record_payment(store.id, order_id, amount, method, staff_user_id)
        except OutOfStockError as error:
            item = next((f"{i.product_name}, {i.color or '-'}, size {i.size or '-'}"
                         for i in order.items if str(i.variant_id) == error.detail), "an item")
            return PaymentResult(False, f"Can't confirm #{number}: {item} is sold out now. Nothing "
                                        "was changed. Please contact the customer.")
        except OrderRejectedError as error:
            reasons = {"already_paid": f"Order #{number} is already paid.",
                       "order_cancelled": f"Order #{number} was cancelled."}
            return PaymentResult(False, reasons.get(error.code, f"Refused ({error.code})."))
        except NotFoundError:
            return PaymentResult(False, "Order not found.")

        if confirmer_telegram_id is not None:
            try:
                await self.db.note_payment_confirmer(store.id, payment_id, confirmer_telegram_id,
                                                     confirmer_name or "")
            except Exception:
                logger.warning("could not record who confirmed the payment", exc_info=True)

        customer = await self.db.get_customer(store.id, order.customer_id) if order.customer_id else None
        customer_telegram_id = customer.telegram_id if customer is not None else None
        if customer_telegram_id is not None:
            with log_context(telegram_id=customer_telegram_id):
                await self._tell_customer(store, customer_telegram_id,
                                          lambda language: t("payment_confirmed", language, number=number))
                await self.hand_back(store, customer_telegram_id)
        logger.info("payment confirmed by staff", extra={"order_id": str(order_id)})
        return PaymentResult(True, f"✅ Payment for order #{number} confirmed. The customer has been told.",
                             customer_telegram_id)

    async def _next_steps_note(self, store: Store, order_id: UUID, customer_telegram_id: int | None, text: str,
                               reply_to: int | None = None) -> None:
        """After a payment: a note in the staff group with the order's next
        steps (Phase 15), remembered so staff can Reply to it too."""
        order = await self.db.get_order(store.id, order_id)
        try:
            message_id = await self.telegram.send_message(
                store.telegram_bot_token.get_secret_value(), store.staff_chat_id, text,
                buttons=order_buttons(order) if order else None, reply_to=reply_to)
        except TelegramError as error:
            logger.error("staff note not delivered", extra={"error": error.description})
            return
        if message_id and customer_telegram_id is not None:
            await self.conversations.save_staff_message(StaffMessage(
                store_id=store.id, staff_chat_id=store.staff_chat_id, message_id=message_id,
                telegram_id=customer_telegram_id, order_id=order_id,
            ))

    async def advance_order(self, store: Store, order_id: UUID, step: str, *,
                            staff_telegram_id: int | None = None, staff_name: str | None = None) -> PaymentResult:
        """The order's next step, and the customer is told:
            "ship"   delivery: on the way (unpaid: the stock goes down now, D77)
            "done"   delivery: delivered (& paid, D76); pickup: picked up (paid first)
            "back"   delivery: not delivered: the stock comes back, the order is cancelled
            "ready"  pickup: ready (a message only, D75)
        A stage never goes back: a step that doesn't fit the order's stage is
        refused, and nothing is sent."""
        order = await self.db.get_order(store.id, order_id)
        if order is None:
            return PaymentResult(False, "Order not found.")
        number = order_number(order.id)
        delivery = order.fulfillment_method == "delivery"
        paid = order.payment_status == "paid"
        finished = f"Order #{number} was already {'delivered' if delivery else 'picked up'}."
        if order.status == "cancelled":
            return PaymentResult(False, f"Order #{number} was cancelled.")
        if order.status == "delivered":
            return PaymentResult(False, finished)
        if (step in ("ship", "back") and not delivery) or (step == "ready" and delivery):
            return PaymentResult(False, "This button doesn't fit this order.")
        if not delivery and not paid:
            return PaymentResult(False, f"Order #{number} isn't paid yet. Confirm the payment first.")
        by = {"by_telegram_id": staff_telegram_id, "by_name": staff_name}
        total = order.total_price

        try:
            if step == "ship":
                await self.db.dispatch_order(store.id, order_id, **by)
                done = f"🚚 Order #{number} is on the way." + ("" if paid else " The stock went down.")

                def text(language) -> str:
                    if paid:
                        return t("order_on_the_way", language, number=number)
                    lines = [t("order_on_the_way_pay", language, number=number,
                               total=format_price(total, language))]
                    if store.payment_instructions:
                        lines.append(f"\n{t('you_can_pay_with', language)}\n{store.payment_instructions}")
                        lines.append(f"\n{t('screenshot_on_delivery', language)}")
                    return "\n".join(lines)
            elif step == "done" and delivery:
                if order.status != "out_for_delivery":
                    return PaymentResult(False, f"Tap 🚚 On the way for order #{number} first.")
                await self.db.deliver_order(store.id, order_id, None if paid else total, None, **by)
                done = f"✅ Order #{number} was delivered" + (" and paid." if not paid else ".")
                key = "order_delivered" if paid else "order_delivered_paid"

                def text(language) -> str:
                    return t(key, language, number=number, shop=store.name)
            elif step == "back":
                await self.db.return_order(store.id, order_id, **by)
                done = f"❌ Order #{number} wasn't delivered: cancelled, and the stock is back."

                def text(language) -> str:
                    return t("order_not_delivered", language, number=number)
            elif step == "done":  # pickup
                if not await self.db.set_order_status(store.id, order_id, "delivered", ["confirmed"],
                                                      staff_telegram_id, staff_name):
                    return PaymentResult(False, finished)
                done = f"✅ Order #{number} was picked up."

                def text(language) -> str:
                    return t("order_picked_up", language, number=number, shop=store.name)
            elif step == "ready":
                done = f"📦 Order #{number} is ready for pickup."

                def text(language) -> str:
                    lines = [t("order_ready", language, number=number)]
                    if store.location:  # where and when, from the store's profile
                        lines.append(t("pickup_where", language, location=store.location))
                    if store.opening_hours:
                        lines.append(t("pickup_hours", language, hours=store.opening_hours))
                    return "\n".join(lines)
            else:
                return PaymentResult(False, "Unknown button.")
        except OutOfStockError as error:
            item = next((f"{i.product_name}, {i.color or '-'}, {i.size or '-'}"
                         for i in order.items if str(i.variant_id) == error.detail), "an item")
            return PaymentResult(False, f"Can't send #{number}: {item} is sold out now. Nothing was "
                                        "changed. Please contact the customer.")
        except OrderRejectedError as error:
            reasons = {"already_dispatched": f"Order #{number} is already on the way.",
                       "already_delivered": finished, "order_cancelled": f"Order #{number} was cancelled.",
                       "not_on_the_way": f"Order #{number} isn't on the way.",
                       "already_paid": f"Order #{number} is paid: refund the customer first, then cancel it."}
            return PaymentResult(False, reasons.get(error.code, f"Refused ({error.code})."))
        except NotFoundError:
            return PaymentResult(False, "Order not found.")

        customer = await self.db.get_customer(store.id, order.customer_id) if order.customer_id else None
        told = False
        if customer is not None and customer.telegram_id is not None:
            with log_context(telegram_id=customer.telegram_id):
                told = await self._tell_customer(store, customer.telegram_id, text)
                if step in ("done", "back"):  # finished: the bot answers this customer again
                    await self.hand_back(store, customer.telegram_id)
        logger.info("order moved on by staff", extra={"order_id": str(order_id), "step": step})
        after = "The customer has been told." if told else "⚠️ The customer couldn't be told."
        return PaymentResult(True, f"{done} {after}")

    async def _tell_customer(self, store: Store, telegram_id: int, build) -> bool:
        """Send the customer `build(language)` and keep it in the chat history.
        False if it wasn't delivered (e.g. the customer blocked the bot)."""
        conversation = await self.conversations.get_or_create_conversation(store.id, telegram_id)
        history = await self.conversations.get_recent_messages(store.id, conversation.id, HISTORY_LIMIT)
        # The language the customer chose (D29), otherwise a guess from the chat.
        language = conversation.order_draft.language or detect_language(
            m.content for m in reversed(history) if m.role == "customer" and m.kind != "other")
        text = build(language)
        try:
            await self.telegram.send_message(store.telegram_bot_token.get_secret_value(), telegram_id, text)
        except TelegramError as error:
            logger.warning("customer message not delivered", extra={"error": error.description})
            return False
        await self.conversations.add_messages(store.id, conversation.id,
                                              [ChatMessage(role="assistant", content=text)])
        return True

    async def hand_back(self, store: Store, telegram_id: int, *, idle_before=None) -> bool:
        """The bot answers this customer again. False if it already was.
        idle_before: only if staff haven't acted since then (the 2-hour sweep)."""
        _, resumed = await self._set_paused(store.id, telegram_id, False, idle_before=idle_before)
        if resumed:
            logger.info("chat handed back to the bot")
        return resumed

    async def _set_paused(self, store_id: UUID, telegram_id: int, paused: bool,
                          *, idle_before=None) -> tuple[Conversation, bool]:
        """Pause (staff take over; counts as staff activity) or resume the bot.
        Returns the saved conversation and whether it was paused before.
        Uses the version check, like every conversation save."""
        for _ in range(_MAX_SAVE_TRIES):
            conversation = await self.conversations.get_or_create_conversation(store_id, telegram_id)
            was_paused = conversation.bot_paused
            if paused:
                now = utc_now()
                conversation.bot_paused = True
                conversation.paused_at = conversation.paused_at if was_paused else now
                conversation.staff_active_at = now
            else:
                if not was_paused:
                    return conversation, False
                last_activity = conversation.staff_active_at or conversation.paused_at
                if idle_before is not None and last_activity is not None and last_activity >= idle_before:
                    return conversation, False  # staff acted meanwhile: leave it with them
                conversation.bot_paused = False
                conversation.paused_at = None
                conversation.staff_active_at = None
            try:
                return await self.conversations.save_conversation(conversation), was_paused
            except VersionConflictError:
                continue
        raise VersionConflictError("version_conflict", "conversation kept changing")

    # --- D9: hand idle chats back to the bot (called by the minute sweep) ----

    async def resume_idle(self) -> int:
        """Hand back chats with no staff activity for 2 hours. Returns how many."""
        cutoff = utc_now() - BOT_RESUMES_AFTER
        resumed = 0
        stores: dict[UUID, Store | None] = {}
        for store_id, telegram_id in await self.conversations.find_paused_before(cutoff):
            if store_id not in stores:
                stores[store_id] = await self.db.get_store(store_id)
            store = stores[store_id]
            if store is None:
                continue  # switched-off store
            with log_context(store_id=str(store_id), telegram_id=telegram_id):
                if not await self.hand_back(store, telegram_id, idle_before=cutoff):
                    continue
                resumed += 1
                logger.info("chat handed back to the bot after 2 hours without staff")
                try:
                    await self.send_alert(store, StaffAlert(
                        text=(f"▶️ No staff reply for 2 hours: the bot is answering this customer "
                              f"(Telegram id {telegram_id}) again. Reply to this message to take over."),
                        telegram_id=telegram_id,
                    ))
                except TelegramError as error:
                    logger.error("staff note not delivered", extra={"error": error.description})
        return resumed
