"""Handles customer messages after the webhook has saved them to the inbox.

The flow for one customer (docs/backend-architecture.md, section 4):

    lock this customer -> wait 2 s for quick follow-ups -> claim their
    waiting inbox rows -> save the messages to history -> bot paused? stop
    -> load recent history -> the scripted order flow (flow.py) -> save the
    conversation (version check) -> save the replies -> alert staff -> send
    the replies -> mark the rows done

Any failure puts the rows back for a retry; after MAX_ATTEMPTS the customer
gets a polite message and staff are alerted.

The recovery sweep (at startup and every minute) picks up rows left behind
by a crash or restart.

The order flow (Phase 8c, D28) asks fixed questions step by step, with
buttons; the AI only interprets messages that go off script.
"""
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import timedelta

from app.agents.flow import OrderFlow, Reply, product_link_code
from app.agents.miniapp import MiniAppAccess
from app.agents.messages import Language, both, detect_language, message_language, t
from app.agents.staff import StaffDesk
from app.agents.tools import StaffAlert, ToolContext
from app.models.schemas import (
    ChatMessage,
    Customer,
    IncomingMessage,
    InboxItem,
    OrderDraft,
    Store,
    TelegramUpdate,
)
from app.services.conversation_service import (
    BURST_WAIT_SECONDS,
    HISTORY_LIMIT,
    DEFAULT_MESSAGES_PER_MINUTE,
    MAX_ATTEMPTS,
    ConversationStore,
    CustomerLocks,
    RateLimiter,
    history_since,
    is_expired,
    utc_now,
)
from app.services.llm_service import LLMProvider
from app.services.supabase_service import SupabaseService, VersionConflictError
from app.services.telegram_service import TelegramError, TelegramService, parse_update
from app.utils.logging import log_context

logger = logging.getLogger(__name__)

# Recovery sweep timing.
RECOVERY_INTERVAL_SECONDS = 60
# Check that channel posts match the database every this many sweeps (D39).
CATALOG_CHECK_EVERY_MINUTES = 5
# A waiting row older than this was missed (or is due for a retry). Normal
# handling claims rows within a few seconds.
WAITING_TOO_LONG = timedelta(seconds=30)
# A row 'processing' for longer than this belongs to a run that died. Must be
# longer than the slowest real run (AI calls with retries, tool rounds).
PROCESSING_TOO_LONG = timedelta(minutes=10)
# At startup every unfinished row is ours. "Before now + a margin" instead of
# "before now": some computers' clocks return the same time twice in a row,
# so a row saved a moment ago may not count as "before now". No new rows can
# arrive yet: startup recovery runs before the server accepts requests.
STARTUP_MARGIN = timedelta(minutes=1)

# How many times to redo a run when the conversation was changed under us.
MAX_VERSION_RETRIES = 3

# Every text sent to customers is in messages.py, in Amharic and English.


@dataclass
class RunResult:
    replies: list[Reply] = field(default_factory=list)  # to send to the customer, in order
    staff_alerts: list[StaffAlert] = field(default_factory=list)


def _to_chat_message(message: IncomingMessage) -> ChatMessage:
    if message.kind == "button":
        # History keeps which button was tapped (the messages table has no
        # "button" kind). Short, so it never changes the detected language.
        return ChatMessage(role="customer", kind="other", content=f"button {message.button_data}",
                           update_id=message.update_id, telegram_message_id=message.message_id)
    return ChatMessage(
        role="customer",
        kind=message.kind,
        content=message.text,
        update_id=message.update_id,
        telegram_message_id=message.message_id,
    )


def _is_permanent(error: TelegramError) -> bool:
    """Retrying won't help: e.g. the customer blocked the bot (403) or the
    chat doesn't exist (400). 429 (too many requests) is worth retrying."""
    return error.status is not None and 400 <= error.status < 500 and error.status != 429


class Orchestrator:
    """Created once at startup and shared by all stores."""

    def __init__(
        self,
        db: SupabaseService,
        conversations: ConversationStore,
        telegram: TelegramService,
        llm: LLMProvider | None,
        *,
        locks: CustomerLocks | None = None,
        burst_wait: float = BURST_WAIT_SECONDS,
        staff: StaffDesk | None = None,
        flow: OrderFlow | None = None,  # tests of the plumbing pass a simpler one
        catalog=None,  # the channel catalog (Phase 8d), checked by the sweep
        ai_daily_limit: int | None = None,  # D21 (Phase 10); None: no limit
        rate_limit: RateLimiter | None = None,  # messages per customer per minute
    ):
        self.db = db
        self.conversations = conversations
        self.telegram = telegram
        self.llm = llm
        self.locks = locks or CustomerLocks()
        self.burst_wait = burst_wait
        self.staff = staff or StaffDesk(db, conversations, telegram)
        self.flow = flow or OrderFlow(db, llm, ai_daily_limit)
        self.rate_limit = rate_limit or RateLimiter(DEFAULT_MESSAGES_PER_MINUTE)
        self.app_access = MiniAppAccess(telegram)  # who may open the Mini App (Phase 10b)
        self.catalog = catalog
        self._tasks: set[asyncio.Task] = set()

    # --- Handling one customer ----------------------------------------------

    async def process_customer(self, store: Store, telegram_id: int) -> None:
        """Handle everything this customer has waiting. Runs in the
        background; never raises (errors are logged)."""
        with log_context(store_id=str(store.id), telegram_id=telegram_id):
            try:
                async with self.locks.hold(store.id, telegram_id):
                    # An earlier run may already have handled everything
                    # (a burst of 5 messages starts 5 runs; the first takes
                    # all 5). Then there's nothing to wait for.
                    if not await self.conversations.has_waiting_inbox(store.id, telegram_id):
                        return
                    # Let quick follow-up messages arrive, so they get one reply.
                    if self.burst_wait:
                        await asyncio.sleep(self.burst_wait)
                    items = await self.conversations.claim_inbox(store.id, telegram_id)
                    if not items:
                        return  # an earlier run already handled them
                    await self._handle_batch(store, items)
            except Exception:
                # Only reached if claiming failed; the rows are still waiting
                # and the recovery sweep will pick them up.
                logger.exception("could not process customer")

    async def _handle_batch(self, store: Store, items: list[InboxItem]) -> None:
        ids = [item.id for item in items]
        messages = []
        for item in items:
            try:
                message = parse_update(store.id, TelegramUpdate.model_validate(item.payload))
            except ValueError:  # includes Pydantic's ValidationError
                logger.warning("unreadable inbox row skipped", extra={"inbox_id": item.id})
                continue
            if message is not None:
                messages.append(message)
        if not messages:
            await self.conversations.finish_inbox(store.id, ids)
            return

        telegram_id, chat_id = messages[-1].telegram_id, messages[-1].chat_id
        # For the fallback message only; the run itself also looks at history.
        fallback_language = next(
            (lang for lang in (message_language(m.text) for m in reversed(messages)) if lang), None)
        logger.info("handling messages", extra={"count": len(messages),
                                                "update_ids": ",".join(str(m.update_id) for m in messages)})
        await self._answer_taps(store, messages)
        try:
            customer = await self.db.get_or_create_customer(store.id, telegram_id, messages[-1].customer_name)
            result = await self._run(store, customer, chat_id, messages)
            # Staff first: if a reply to the customer then fails and the run
            # is retried, the retry won't alert staff a second time.
            for alert in result.staff_alerts:
                await self._alert_staff(store, alert)
            for reply in result.replies:
                await self._send(store, chat_id, reply)
            await self.conversations.finish_inbox(store.id, ids)
        except TelegramError as error:
            if _is_permanent(error):
                logger.warning("reply not delivered", extra={"error": error.description, "status": error.status})
                await self.conversations.finish_inbox(store.id, ids)
            else:
                await self._fail(store, items, chat_id, f"telegram: {error.description}", fallback_language)
        except Exception as error:
            logger.exception("handling failed")
            await self._fail(store, items, chat_id, f"{type(error).__name__}: {error}",
                             fallback_language)

    async def _run(
        self, store: Store, customer: Customer, chat_id: int, messages: list[IncomingMessage]
    ) -> RunResult:
        """Save the messages, run the agent, save the conversation.
        Returns the replies to send (none if the bot must stay silent)."""
        telegram_id = customer.telegram_id
        conversation = await self.conversations.get_or_create_conversation(store.id, telegram_id)
        # Saved before anything else, so they're never lost even if the run
        # fails. Saving them again on a retry does nothing.
        await self.conversations.add_messages(
            store.id, conversation.id, [_to_chat_message(m) for m in messages]
        )

        for attempt in range(MAX_VERSION_RETRIES):
            if attempt:
                conversation = await self.conversations.get_or_create_conversation(store.id, telegram_id)
            resumed = False
            if conversation.bot_paused and await self._opens_channel_order(store, messages):
                # D33: the customer tapped Order on a channel post: the bot
                # takes the chat back and starts the order (staff are told).
                conversation.bot_paused, conversation.paused_at = False, None
                conversation.staff_active_at = None
                resumed = True
            if conversation.bot_paused:
                # Staff have this chat: the bot stays silent, and staff see
                # what the customer wrote (they Reply to it to answer).
                logger.info("bot paused; staff handle this chat")
                last_order = conversation.order_draft.last_order_id
                return RunResult(staff_alerts=[
                    StaffAlert(
                        text=(f"💬 {customer.name or 'Customer'} (Telegram id {customer.telegram_id}):\n"
                              f"{m.text or f'[{m.kind}]' if m.kind != 'button' else '[tapped a button]'}"),
                        telegram_id=customer.telegram_id,
                        order_id=last_order if m.kind == "photo" else None,  # a screenshot?
                        photo_file_id=m.photo_file_id,
                    )
                    for m in messages
                ])

            now = utc_now()
            if is_expired(conversation, now):
                logger.info("conversation expired; starting fresh")
                # The revision keeps counting up (order idempotency keys use it).
                old = conversation.order_draft
                conversation.order_draft = OrderDraft(revision=old.revision + 1, language=old.language)
            history = await self.conversations.get_recent_messages(
                store.id, conversation.id, HISTORY_LIMIT, since=history_since(now)
            )
            language = detect_language(
                # Button taps (kind "other") say nothing about the language.
                (m.content for m in reversed(history) if m.role == "customer" and m.kind != "other"),
                messages[-1].language_code,
            )
            ctx = ToolContext(store=store, customer=customer, conversation=conversation,
                              new_messages=messages, chat_id=chat_id, db=self.db,
                              telegram=self.telegram, language=language)
            if resumed:
                ctx.staff_alerts.append(StaffAlert(
                    text=(f"🛍 {customer.name or 'Customer'} (Telegram id {customer.telegram_id}) "
                          "started an order from the channel. The bot is answering them again."),
                    telegram_id=customer.telegram_id))
            replies = await self.flow.handle(ctx, history)
            conversation.last_message_at = now

            try:
                conversation = await self.conversations.save_conversation(conversation)
            except VersionConflictError:
                # Changed while we worked (e.g. staff paused the bot): redo
                # the run with fresh data instead of overwriting it.
                logger.info("conversation changed during run; retrying", extra={"attempt": attempt + 1})
                continue

            if not replies:
                replies = [Reply(t("empty_reply", ctx.language, store))]
            await self.conversations.add_messages(
                store.id, conversation.id,
                [ChatMessage(role="assistant", content=reply.text) for reply in replies],
            )
            return RunResult(replies=replies, staff_alerts=ctx.staff_alerts)

        raise VersionConflictError("version_conflict", "conversation kept changing")

    async def _opens_channel_order(self, store: Store, messages: list[IncomingMessage]) -> bool:
        """A product link from the channel's Order button, or a forwarded bot post (D33)."""
        for message in messages:
            if product_link_code(message.text):
                return True
            if message.forwarded_post and self.db is not None:
                post = await self.db.find_post(store.id, *message.forwarded_post)
                if post is not None and post.product_id is not None:
                    return True
        return False

    async def _send(self, store: Store, chat_id: int, reply: Reply | str) -> None:
        if isinstance(reply, str):
            reply = Reply(reply)
        token = store.telegram_bot_token.get_secret_value()
        if reply.photo_url:  # the product photo, with the question as its caption
            try:
                await self.telegram.send_photo(token, chat_id, reply.photo_url, reply.text,
                                               buttons=reply.buttons or None)
                logger.info("reply sent")
                return
            except TelegramError as error:  # a broken photo link: send the text alone
                logger.warning("product photo not sent", extra={"error": error.description})
        await self.telegram.send_message(token, chat_id, reply.text, buttons=reply.buttons or None)
        logger.info("reply sent")

    async def _answer_taps(self, store: Store, messages: list[IncomingMessage]) -> None:
        """Answer the customer's button taps (Telegram shows a spinner on the
        button until then). Best effort."""
        token = store.telegram_bot_token.get_secret_value()
        for message in messages:
            if message.callback_id:
                try:
                    await self.telegram.answer_button(token, message.callback_id, "")
                except TelegramError:
                    pass  # an old tap can't be answered any more; that's fine

    async def _alert_staff(self, store: Store, alert: StaffAlert) -> None:
        """Best effort: a failed staff alert is logged, not retried."""
        try:
            await self.staff.send_alert(store, alert)
        except TelegramError as error:
            logger.error("staff alert not delivered", extra={"error": error.description})

    async def _fail(self, store: Store, items: list[InboxItem], chat_id: int, error: str,
                    language: Language | None = None) -> None:
        """Put the rows back for a retry, or give up after MAX_ATTEMPTS."""
        try:
            released = await self.conversations.release_inbox(
                store.id, [item.id for item in items], error, MAX_ATTEMPTS
            )
        except Exception:
            # The rows stay 'processing'; the sweep resets them later.
            logger.exception("could not release inbox rows")
            return

        failed = [item for item in released if item.status == "failed"]
        if not failed:
            logger.warning("will retry", extra={"error": error})
            return

        logger.error("giving up on messages", extra={"error": error, "attempts": failed[0].attempts})
        try:
            await self._send(store, chat_id, t("fallback_reply", language) if language
                             else both("fallback_reply"))
        except TelegramError as send_error:
            logger.error("fallback not delivered", extra={"error": send_error.description})
        try:
            await self.staff.send_alert(store, StaffAlert(
                text=(f"⚠️ The bot could not answer a customer (Telegram id {failed[0].telegram_id}) "
                      f"after {failed[0].attempts} tries. Reply to this message to answer them."),
                telegram_id=failed[0].telegram_id,
            ))
        except TelegramError as send_error:
            logger.error("staff alert not delivered", extra={"error": send_error.description})

    # --- Recovery sweep -----------------------------------------------------

    def schedule(self, store: Store, telegram_id: int) -> None:
        """Start process_customer in the background (outside a request)."""
        task = asyncio.create_task(self.process_customer(store, telegram_id))
        self._tasks.add(task)  # keep a reference so it isn't garbage collected
        task.add_done_callback(self._tasks.discard)

    async def recover(self, *, startup: bool = False) -> int:
        """Find inbox rows left behind and handle them. Returns how many
        customers were scheduled.

        At startup nothing can be running yet, so every 'processing' row is
        stuck and every waiting row needs handling.
        """
        now = utc_now()
        reset = await self.conversations.reset_stuck_inbox(
            now + STARTUP_MARGIN if startup else now - PROCESSING_TOO_LONG
        )
        if reset:
            logger.warning("reset stuck messages", extra={"count": reset})

        waiting = await self.conversations.find_waiting_inbox(
            now + STARTUP_MARGIN if startup else now - WAITING_TOO_LONG
        )
        stores: dict = {}
        scheduled = 0
        for store_id, telegram_id in waiting:
            if self.locks.is_busy(store_id, telegram_id):
                continue  # a run for this customer is already going
            if store_id not in stores:
                stores[store_id] = await self.db.get_store(store_id)
            store = stores[store_id]
            if store is None:
                # The store was switched off: nobody can answer these.
                items = await self.conversations.claim_inbox(store_id, telegram_id)
                await self.conversations.release_inbox(
                    store_id, [i.id for i in items], "store not active", max_attempts=0
                )
                continue
            self.schedule(store, telegram_id)
            scheduled += 1
        if scheduled:
            logger.info("recovering waiting messages", extra={"customers": scheduled})
        return scheduled

    async def run_recovery_loop(self) -> None:
        """Run the sweep every minute until the server stops."""
        minutes = 0
        while True:
            await asyncio.sleep(RECOVERY_INTERVAL_SECONDS)
            minutes += 1
            if self.catalog is not None and minutes % CATALOG_CHECK_EVERY_MINUTES == 0:
                try:  # D39's safety net: fix channel posts that missed a webhook
                    await self.catalog.reconcile()
                except Exception:
                    logger.exception("catalog check failed")
            try:
                await self.recover()
            except Exception:
                logger.exception("recovery sweep failed")
            try:  # D9: chats staff haven't touched for 2 hours go back to the bot
                await self.staff.resume_idle()
            except Exception:
                logger.exception("hand-back sweep failed")

    async def close(self) -> None:
        """At shutdown: stop running work. Unfinished rows are recovered at
        the next startup."""
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
