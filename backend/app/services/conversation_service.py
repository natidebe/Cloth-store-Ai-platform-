"""Conversation memory: the inbox, each customer's conversation, and history.

Everything is kept in the database (migration 003), not in the server's
memory, so it survives restarts.

ConversationStore is the interface the rest of the app uses. It has two
versions:
- DatabaseConversationStore: the real one. It asks supabase_service to do
  the reads and writes (only supabase_service talks to the database).
- InMemoryConversationStore: for tests. Same behaviour, kept in dicts.

Also here: CustomerLocks, which makes sure one customer's messages are
handled one after another, never at the same time.
"""
import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from itertools import count
from typing import Any
from uuid import UUID, uuid4

from app.models.schemas import ChatMessage, Conversation, InboxItem, OrderDraft
from app.services.supabase_service import (
    NotFoundError,
    SupabaseService,
    VersionConflictError,
)

# Wait this long after a message for more quick messages ("white" / "size 42"
# / "0911…"), then handle them together with one reply (D20).
BURST_WAIT_SECONDS = 2.0

# Only the most recent messages are sent to the AI, to keep cost down.
HISTORY_LIMIT = 20

# After this long without a customer message, the conversation starts fresh:
# the AI no longer sees the old messages and the order draft is cleared.
# Everything stays in the database for staff.
CONVERSATION_EXPIRES_AFTER = timedelta(hours=24)

# Tries before an update is marked failed (the customer gets a polite
# message and staff are alerted).
MAX_ATTEMPTS = 3


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def is_expired(conversation: Conversation, now: datetime) -> bool:
    last = conversation.last_message_at
    return last is not None and now - last > CONVERSATION_EXPIRES_AFTER


def history_since(now: datetime) -> datetime:
    """Messages older than this are not sent to the AI."""
    return now - CONVERSATION_EXPIRES_AFTER


# ---------------------------------------------------------------------------
# The interface
# ---------------------------------------------------------------------------

class ConversationStore(ABC):
    """Every function takes store_id (from the webhook URL) and only ever
    sees that store's data. The two recovery functions are the exception:
    they look across stores to find work, and the work is then done per store."""

    # --- Inbox --------------------------------------------------------------

    @abstractmethod
    async def save_to_inbox(
        self, store_id: UUID, update_id: int, telegram_id: int, payload: dict[str, Any]
    ) -> bool:
        """True if new, False if this update was already saved."""

    @abstractmethod
    async def has_waiting_inbox(self, store_id: UUID, telegram_id: int) -> bool:
        """True if this customer has updates waiting to be handled."""

    @abstractmethod
    async def claim_inbox(self, store_id: UUID, telegram_id: int) -> list[InboxItem]:
        """This customer's waiting updates, now marked processing; oldest first."""

    @abstractmethod
    async def finish_inbox(self, store_id: UUID, ids: list[int]) -> None:
        """Mark updates done."""

    @abstractmethod
    async def release_inbox(
        self, store_id: UUID, ids: list[int], error: str, max_attempts: int
    ) -> list[InboxItem]:
        """Handling failed: back to received for a retry, or failed after
        max_attempts. Returns the rows with their new status."""

    @abstractmethod
    async def reset_stuck_inbox(self, claimed_before: datetime) -> int:
        """Processing since before `claimed_before` -> received. Returns how many."""

    @abstractmethod
    async def find_waiting_inbox(self, received_before: datetime) -> list[tuple[UUID, int]]:
        """(store_id, telegram_id) with updates waiting since before `received_before`."""

    # --- Conversations and messages -----------------------------------------

    @abstractmethod
    async def get_or_create_conversation(self, store_id: UUID, telegram_id: int) -> Conversation:
        ...

    @abstractmethod
    async def save_conversation(self, conversation: Conversation) -> Conversation:
        """Raises VersionConflictError if someone else saved it since it was loaded."""

    @abstractmethod
    async def add_messages(
        self, store_id: UUID, conversation_id: UUID, messages: list[ChatMessage]
    ) -> None:
        """Customer messages already saved (same update_id) are skipped."""

    @abstractmethod
    async def get_recent_messages(
        self, store_id: UUID, conversation_id: UUID, limit: int, since: datetime | None = None
    ) -> list[ChatMessage]:
        """Oldest first."""


# ---------------------------------------------------------------------------
# Database version (real use)
# ---------------------------------------------------------------------------

class DatabaseConversationStore(ConversationStore):
    def __init__(self, db: SupabaseService):
        self._db = db

    async def save_to_inbox(self, store_id, update_id, telegram_id, payload):
        return await self._db.save_to_inbox(store_id, update_id, telegram_id, payload)

    async def has_waiting_inbox(self, store_id, telegram_id):
        return await self._db.has_waiting_inbox(store_id, telegram_id)

    async def claim_inbox(self, store_id, telegram_id):
        return await self._db.claim_inbox(store_id, telegram_id)

    async def finish_inbox(self, store_id, ids):
        await self._db.finish_inbox(store_id, ids)

    async def release_inbox(self, store_id, ids, error, max_attempts):
        return await self._db.release_inbox(store_id, ids, error, max_attempts)

    async def reset_stuck_inbox(self, claimed_before):
        return await self._db.reset_stuck_inbox(claimed_before)

    async def find_waiting_inbox(self, received_before):
        return await self._db.find_waiting_inbox(received_before)

    async def get_or_create_conversation(self, store_id, telegram_id):
        return await self._db.get_or_create_conversation(store_id, telegram_id)

    async def save_conversation(self, conversation):
        return await self._db.save_conversation(conversation)

    async def add_messages(self, store_id, conversation_id, messages):
        await self._db.add_messages(store_id, conversation_id, messages)

    async def get_recent_messages(self, store_id, conversation_id, limit, since=None):
        return await self._db.get_recent_messages(store_id, conversation_id, limit, since)


# ---------------------------------------------------------------------------
# In-memory version (tests only: everything is lost on restart)
# ---------------------------------------------------------------------------

class InMemoryConversationStore(ConversationStore):
    def __init__(self) -> None:
        self.inbox: dict[int, InboxItem] = {}
        self.conversations: dict[tuple[UUID, int], Conversation] = {}
        self.messages: dict[UUID, list[tuple[UUID, ChatMessage]]] = {}  # conversation id -> (store, message)
        self._ids = count(1)

    # --- Inbox --------------------------------------------------------------

    async def save_to_inbox(self, store_id, update_id, telegram_id, payload):
        if any(i.store_id == store_id and i.update_id == update_id for i in self.inbox.values()):
            return False
        item_id = next(self._ids)
        self.inbox[item_id] = InboxItem(
            id=item_id, store_id=store_id, update_id=update_id, telegram_id=telegram_id,
            payload=payload, received_at=utc_now(),
        )
        return True

    async def has_waiting_inbox(self, store_id, telegram_id):
        return any(i.store_id == store_id and i.telegram_id == telegram_id and i.status == "received"
                   for i in self.inbox.values())

    async def claim_inbox(self, store_id, telegram_id):
        claimed = []
        for item in self.inbox.values():
            if item.store_id == store_id and item.telegram_id == telegram_id and item.status == "received":
                item.status, item.attempts, item.claimed_at = "processing", item.attempts + 1, utc_now()
                claimed.append(item.model_copy())
        return sorted(claimed, key=lambda item: item.update_id)

    async def finish_inbox(self, store_id, ids):
        for item_id in ids:
            item = self.inbox.get(item_id)
            if item and item.store_id == store_id:
                item.status, item.finished_at, item.last_error = "done", utc_now(), None

    async def release_inbox(self, store_id, ids, error, max_attempts):
        released = []
        for item_id in ids:
            item = self.inbox.get(item_id)
            if item and item.store_id == store_id and item.status == "processing":
                failed = item.attempts >= max_attempts
                item.status = "failed" if failed else "received"
                item.finished_at = utc_now() if failed else None
                item.last_error = error
                released.append(item.model_copy())
        return released

    async def reset_stuck_inbox(self, claimed_before):
        stuck = [i for i in self.inbox.values()
                 if i.status == "processing" and i.claimed_at and i.claimed_at < claimed_before]
        for item in stuck:
            item.status = "received"
        return len(stuck)

    async def find_waiting_inbox(self, received_before):
        waiting = sorted(
            (i for i in self.inbox.values() if i.status == "received" and i.received_at < received_before),
            key=lambda item: item.received_at,
        )
        return list({(i.store_id, i.telegram_id): None for i in waiting})

    # --- Conversations and messages -----------------------------------------

    async def get_or_create_conversation(self, store_id, telegram_id):
        key = (store_id, telegram_id)
        if key not in self.conversations:
            self.conversations[key] = Conversation(
                id=uuid4(), store_id=store_id, telegram_id=telegram_id, created_at=utc_now(),
            )
        return self.conversations[key].model_copy(deep=True)

    async def save_conversation(self, conversation):
        key = (conversation.store_id, conversation.telegram_id)
        current = self.conversations.get(key)
        if current is None or current.id != conversation.id:
            raise NotFoundError("conversation_not_found", str(conversation.id))
        if current.version != conversation.version:
            raise VersionConflictError("version_conflict", str(conversation.id))
        saved = current.model_copy(deep=True, update={
            "order_draft": OrderDraft.model_validate(conversation.order_draft.model_dump()),
            "last_message_at": conversation.last_message_at,
            "version": conversation.version + 1,
            "updated_at": utc_now(),
        })
        self.conversations[key] = saved
        return saved.model_copy(deep=True)

    async def add_messages(self, store_id, conversation_id, messages):
        history = self.messages.setdefault(conversation_id, [])
        saved_updates = {(s, m.update_id) for s, m in history if m.update_id is not None}
        for message in messages:
            if message.update_id is not None and (store_id, message.update_id) in saved_updates:
                continue
            history.append((store_id, message.model_copy(update={"created_at": utc_now()})))

    async def get_recent_messages(self, store_id, conversation_id, limit, since=None):
        history = [m for s, m in self.messages.get(conversation_id, []) if s == store_id]
        if since is not None:
            history = [m for m in history if m.created_at >= since]
        return history[-limit:]

    # Test helper: what staff will do in Phase 9.
    def pause_bot(self, store_id: UUID, telegram_id: int) -> None:
        conversation = self.conversations[(store_id, telegram_id)]
        conversation.bot_paused, conversation.version = True, conversation.version + 1


# ---------------------------------------------------------------------------
# One customer at a time
# ---------------------------------------------------------------------------

class CustomerLocks:
    """One lock per (store_id, telegram_id).

    While one run handles a customer's messages, a second run for the same
    customer waits. Different customers never wait for each other.

    These locks live in this server's memory, so they only work while we run
    ONE server. Before running several, they must move to the database
    (e.g. Postgres advisory locks) or Redis.
    """

    def __init__(self) -> None:
        self._locks: dict[tuple[UUID, int], asyncio.Lock] = {}
        self._users: dict[tuple[UUID, int], int] = {}  # runs holding or waiting

    def is_busy(self, store_id: UUID, telegram_id: int) -> bool:
        return self._users.get((store_id, telegram_id), 0) > 0

    @asynccontextmanager
    async def hold(self, store_id: UUID, telegram_id: int) -> AsyncIterator[None]:
        key = (store_id, telegram_id)
        lock = self._locks.setdefault(key, asyncio.Lock())
        self._users[key] = self._users.get(key, 0) + 1
        try:
            async with lock:
                yield
        finally:
            self._users[key] -= 1
            if self._users[key] == 0:  # nobody waiting: forget it, so memory doesn't grow
                del self._users[key]
                del self._locks[key]
