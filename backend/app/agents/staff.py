"""The staff group: alerts with buttons, staff replies, and handing chats back.

What staff see in their Telegram group, and what they can do there:

- Alerts about a customer (hand-over, payment screenshot, new order, ...).
  Each can have buttons:
    [✅ Confirm payment #…]  reduce stock, mark the order paid, tell the
                             customer, and hand the chat back to the bot
    [▶️ Hand back to bot]    the bot starts answering this customer again
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

from app.agents.messages import detect_language, t
from app.agents.tools import StaffAlert, order_number
from app.models.schemas import (
    ChatMessage,
    Conversation,
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

_MAX_SAVE_TRIES = 3


@dataclass
class PaymentResult:
    ok: bool
    message: str  # for the staff member (button notice or API response)


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
        buttons = []
        if alert.order_id:
            buttons.append((f"✅ Confirm payment #{order_number(alert.order_id)}",
                            f"{PAY_BUTTON}{alert.order_id}"))
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
        """/chatid sent in a group or channel: until Phase 9b's /link, this is
        how the owner finds the id to put in stores.staff_chat_id (the staff
        group) or stores.channel_id (the store's channel, Phase 8d)."""
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
                await self._note(store, f"{result.message} Confirmed by {staff_name}.",
                                 reply_to=message.message_id)
                try:
                    await self.telegram.remove_buttons(token, message.chat.id, message.message_id)
                except TelegramError:
                    pass  # the buttons stay; pressing again says "already paid"
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
        if customer is not None and customer.telegram_id is not None:
            with log_context(telegram_id=customer.telegram_id):
                await self._tell_customer_paid(store, customer.telegram_id, number)
                await self.hand_back(store, customer.telegram_id)
        logger.info("payment confirmed by staff", extra={"order_id": str(order_id)})
        return PaymentResult(True, f"✅ Payment for order #{number} confirmed. The customer has been told.")

    async def _tell_customer_paid(self, store: Store, telegram_id: int, number: str) -> None:
        conversation = await self.conversations.get_or_create_conversation(store.id, telegram_id)
        history = await self.conversations.get_recent_messages(store.id, conversation.id, HISTORY_LIMIT)
        # The language the customer chose (D29), otherwise a guess from the chat.
        language = conversation.order_draft.language or detect_language(
            m.content for m in reversed(history) if m.role == "customer" and m.kind != "other")
        text = t("payment_confirmed", language, number=number)
        try:
            await self.telegram.send_message(store.telegram_bot_token.get_secret_value(), telegram_id, text)
        except TelegramError as error:
            logger.warning("payment confirmation not delivered", extra={"error": error.description})
            return
        await self.conversations.add_messages(store.id, conversation.id,
                                              [ChatMessage(role="assistant", content=text)])

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
