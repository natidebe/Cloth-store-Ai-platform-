"""Handles customer messages after the webhook has saved them to the inbox.

The flow for one customer (docs/backend-architecture.md, section 4):

    lock this customer -> wait 2 s for quick follow-ups -> claim their
    waiting inbox rows -> save the messages to history -> bot paused? stop
    -> load recent history -> the agent loop (ask the AI, run the tools it
    asks for, repeat) -> save the conversation (version check) -> save the
    replies -> alert staff -> send the replies -> mark the rows done

Any failure puts the rows back for a retry; after MAX_ATTEMPTS the customer
gets a polite message and staff are alerted.

The recovery sweep (at startup and every minute) picks up rows left behind
by a crash or restart.

The agent loop: the AI gets the instructions, the recent history, and the
tools. It either answers, or asks for tools; we run them and send the
results back, up to MAX_TOOL_ROUNDS times. If it still hasn't answered, the
chat is handed to staff.
"""
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import timedelta

from app.agents.prompts import build_system_prompt
from app.agents.tools import TOOL_DEFINITIONS, EscalateArgs, ToolContext, escalate_to_staff, run_tool
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
    MAX_ATTEMPTS,
    ConversationStore,
    CustomerLocks,
    history_since,
    is_expired,
    utc_now,
)
from app.services.llm_service import LLMError, LLMMessage, LLMProvider
from app.services.supabase_service import SupabaseService, VersionConflictError
from app.services.telegram_service import TelegramError, TelegramService, parse_update
from app.utils.logging import log_context

logger = logging.getLogger(__name__)

FALLBACK_REPLY = (
    "Sorry, something went wrong on our side. Our team has been notified "
    "and will reply to you soon."
)

# Recovery sweep timing.
RECOVERY_INTERVAL_SECONDS = 60
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

# Safety limit on AI calls per run (each round: one AI call + its tools),
# so a confused AI can't run up costs.
MAX_TOOL_ROUNDS = 5
STUCK_REPLY = "Let me get a team member to help you with this. They'll reply here soon."
EMPTY_REPLY = "Sorry, could you say that again?"


@dataclass
class RunResult:
    replies: list[str] = field(default_factory=list)  # to send to the customer, in order
    staff_alerts: list[str] = field(default_factory=list)


def to_llm_message(message: ChatMessage) -> LLMMessage:
    """A saved history message, as the AI sees it."""
    if message.role == "customer":
        if message.kind == "text":
            return LLMMessage(role="user", content=message.content or "")
        note = f"[The customer sent a {message.kind}]"
        return LLMMessage(role="user", content=f"{note} {message.content}" if message.content else note)
    if message.role == "staff":
        return LLMMessage(role="assistant", content=f"(Written by our staff) {message.content or ''}")
    return LLMMessage(role="assistant", content=message.content or "")


def _to_chat_message(message: IncomingMessage) -> ChatMessage:
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
    ):
        self.db = db
        self.conversations = conversations
        self.telegram = telegram
        self.llm = llm
        self.locks = locks or CustomerLocks()
        self.burst_wait = burst_wait
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
        logger.info("handling messages", extra={"count": len(messages),
                                                "update_ids": ",".join(str(m.update_id) for m in messages)})
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
                await self._fail(store, items, chat_id, f"telegram: {error.description}")
        except Exception as error:
            logger.exception("handling failed")
            await self._fail(store, items, chat_id, f"{type(error).__name__}: {error}")

    async def _run(
        self, store: Store, customer: Customer, chat_id: int, messages: list[IncomingMessage]
    ) -> RunResult:
        """Save the messages, run the agent, save the conversation.
        Returns the replies to send (none if the bot must stay silent)."""
        telegram_id = customer.telegram_id
        conversation = await self.conversations.get_or_create_conversation(store.id, telegram_id)
        products = await self.db.list_products(store.id)  # for the AI's product-name list
        # Saved before anything else, so they're never lost even if the run
        # fails. Saving them again on a retry does nothing.
        await self.conversations.add_messages(
            store.id, conversation.id, [_to_chat_message(m) for m in messages]
        )

        for attempt in range(MAX_VERSION_RETRIES):
            if attempt:
                conversation = await self.conversations.get_or_create_conversation(store.id, telegram_id)
            if conversation.bot_paused:
                logger.info("bot paused; staff handle this chat")
                return RunResult()

            now = utc_now()
            if is_expired(conversation, now):
                logger.info("conversation expired; starting fresh")
                # The revision keeps counting up (order idempotency keys use it).
                conversation.order_draft = OrderDraft(revision=conversation.order_draft.revision + 1)
            history = await self.conversations.get_recent_messages(
                store.id, conversation.id, HISTORY_LIMIT, since=history_since(now)
            )
            ctx = ToolContext(store=store, customer=customer, conversation=conversation,
                              new_messages=messages, chat_id=chat_id, db=self.db,
                              telegram=self.telegram, products=products)
            reply = await self._agent_reply(ctx, history)
            conversation.last_message_at = now

            try:
                conversation = await self.conversations.save_conversation(conversation)
            except VersionConflictError:
                # Changed while we worked (e.g. staff paused the bot): redo
                # the run with fresh data instead of overwriting it.
                logger.info("conversation changed during run; retrying", extra={"attempt": attempt + 1})
                continue

            replies = ([reply] if reply else []) + ctx.after_reply
            if not replies and not ctx.sent:
                replies = [EMPTY_REPLY]
            await self.conversations.add_messages(
                store.id, conversation.id,
                [ChatMessage(role="assistant", content=text) for text in ctx.sent + replies],
            )
            return RunResult(replies=replies, staff_alerts=ctx.staff_alerts)

        raise VersionConflictError("version_conflict", "conversation kept changing")

    async def _agent_reply(self, ctx: ToolContext, history: list[ChatMessage]) -> str | None:
        """The agent loop. Returns the AI's final text (None if it had nothing to add)."""
        if self.llm is None:
            raise LLMError("AI provider not configured (check LLM_PROVIDER / LLM_API_KEY)")
        messages = [to_llm_message(m) for m in history]
        for _ in range(MAX_TOOL_ROUNDS):
            # Rebuilt every round: it shows the order draft, which tools change.
            system_prompt = build_system_prompt(ctx.store, ctx.customer, ctx.conversation.order_draft,
                                                ctx.products)
            response = await self.llm.complete(system_prompt, messages, TOOL_DEFINITIONS)
            if not response.tool_calls:
                return (response.text or "").strip() or None
            messages.append(LLMMessage(role="assistant", content=response.text,
                                       tool_calls=response.tool_calls))
            for call in response.tool_calls:
                result = await run_tool(call, ctx)
                messages.append(LLMMessage(role="tool", tool_call_id=call.id, content=result))

        # Still asking for tools after the limit: let a person take over.
        logger.warning("agent reached the round limit; handing over to staff")
        last_text = next((m.text for m in reversed(ctx.new_messages) if m.text), "(no text)")
        await escalate_to_staff(EscalateArgs(
            reason="the assistant could not finish answering",
            summary=f"Last customer message: {last_text[:500]}",
        ), ctx)
        return STUCK_REPLY

    async def _send(self, store: Store, chat_id: int, text: str) -> None:
        await self.telegram.send_message(store.telegram_bot_token.get_secret_value(), chat_id, text)
        logger.info("reply sent")

    async def _alert_staff(self, store: Store, text: str) -> None:
        """Best effort: a failed staff alert is logged, not retried."""
        try:
            await self.telegram.notify_staff(store, text)
        except TelegramError as error:
            logger.error("staff alert not delivered", extra={"error": error.description})

    async def _fail(self, store: Store, items: list[InboxItem], chat_id: int, error: str) -> None:
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
            await self._send(store, chat_id, FALLBACK_REPLY)
        except TelegramError as send_error:
            logger.error("fallback not delivered", extra={"error": send_error.description})
        try:
            await self.telegram.notify_staff(
                store,
                f"⚠️ The bot could not answer a customer (Telegram id {failed[0].telegram_id}) "
                f"after {failed[0].attempts} tries. Please reply to them yourself.",
            )
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
        while True:
            await asyncio.sleep(RECOVERY_INTERVAL_SECONDS)
            try:
                await self.recover()
            except Exception:
                logger.exception("recovery sweep failed")

    async def close(self) -> None:
        """At shutdown: stop running work. Unfinished rows are recovered at
        the next startup."""
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
