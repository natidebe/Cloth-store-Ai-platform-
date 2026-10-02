"""Who may use a store's Mini App, and the /dashboard command (Phase 10b, D42).

Access comes from Telegram, not passwords:
- the store's creator (stores.owner_telegram_id) is always an owner;
- in the store's linked staff group, the creator and admins are owners and
  members are staff;
- anyone else gets nothing (customers, people of other stores, people who
  left the group).

Telegram is asked (getChatMember) and the answer is remembered for a few
minutes, so the dashboard doesn't ask Telegram on every tap. Removing
someone from the staff group takes away their access within that time.

How people open it: /dashboard (or the "📊 Dashboard" button in the staff
group, which opens a private chat with /start dashboard). Telegram only
allows Mini App buttons in private chats, and the bot only sends one to
someone with access. Customers never see it.
"""
import logging
import time
from dataclasses import dataclass
from typing import Literal

from app.core.telegram_auth import MiniAppUser
from app.models.schemas import Store, TelegramUpdate
from app.services.telegram_service import WEB_APP_PREFIX, TelegramError, TelegramService

logger = logging.getLogger(__name__)

Role = Literal["owner", "staff"]
OWNER_STATUSES = {"creator", "administrator"}
STAFF_STATUSES = {"member", "restricted"}
KNOWN_FOR_SECONDS = 300  # someone with access: ask Telegram again after 5 minutes
UNKNOWN_FOR_SECONDS = 30  # someone without: ask again soon (they may just have been added)
DASHBOARD_COMMAND = "/dashboard"
DASHBOARD_START = "dashboard"  # /start dashboard (from the staff group's button)


@dataclass
class AppAccess:
    """A Mini App request that passed the checks: who, which store, which role."""
    store: Store
    user: MiniAppUser
    role: Role

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"


def dashboard_url(public_base_url: str, store: Store) -> str:
    return f"{public_base_url.strip().rstrip('/')}/app/?store={store.id}"


class MiniAppAccess:
    def __init__(self, telegram: TelegramService, *, clock=time.monotonic):
        self.telegram = telegram
        self.clock = clock
        self._known: dict[tuple, tuple[Role | None, float]] = {}

    async def role(self, store: Store, telegram_id: int) -> Role | None:
        """"owner", "staff", or None (no access). If Telegram can't be
        reached, the last known answer is used, else no access."""
        if store.owner_telegram_id is not None and store.owner_telegram_id == telegram_id:
            return "owner"
        if store.staff_chat_id is None or store.telegram_bot_token is None:
            return None
        key, now = (store.id, store.staff_chat_id, telegram_id), self.clock()
        known = self._known.get(key)
        if known is not None and known[1] > now:
            return known[0]
        try:
            status = await self.telegram.get_chat_member_status(
                store.telegram_bot_token.get_secret_value(), store.staff_chat_id, telegram_id)
        except TelegramError as error:
            logger.warning("staff group membership not checked", extra={"error": error.description})
            return known[0] if known is not None else None
        role: Role | None = ("owner" if status in OWNER_STATUSES
                             else "staff" if status in STAFF_STATUSES else None)
        self._known[key] = (role, now + (KNOWN_FOR_SECONDS if role else UNKNOWN_FOR_SECONDS))
        if len(self._known) > 5000:  # forget expired answers, so memory doesn't grow
            self._known = {k: v for k, v in self._known.items() if v[1] > now}
        return role

    # --- /dashboard -------------------------------------------------------------

    @staticmethod
    def is_dashboard_request(store: Store, update: TelegramUpdate) -> bool:
        """/dashboard in a private chat or the store's staff group, or
        /start dashboard in a private chat."""
        message = update.message
        if message is None or message.from_user is None or message.from_user.is_bot:
            return False
        words = (message.text or "").strip().split()
        if not words:
            return False
        command = words[0].split("@")[0].lower()
        if message.chat.type == "private":
            return command == DASHBOARD_COMMAND or (
                command == "/start" and len(words) == 2 and words[1].lower() == DASHBOARD_START)
        return command == DASHBOARD_COMMAND and message.chat.id == store.staff_chat_id

    async def answer_dashboard(self, store: Store, update: TelegramUpdate, public_base_url: str) -> None:
        """Send the Mini App button to someone with access. Never raises."""
        message = update.message
        token = store.telegram_bot_token.get_secret_value()
        try:
            if message.chat.type != "private":
                # In the staff group: Mini App buttons aren't allowed in groups, so
                # link to the private chat, where /start dashboard shows the button.
                bot = store.telegram_bot_username
                if bot:
                    await self.telegram.send_message(
                        token, message.chat.id, "📊 Open the store dashboard in a private chat with the bot:",
                        buttons=[("📊 Dashboard", f"https://t.me/{bot}?start={DASHBOARD_START}")],
                        reply_to=message.message_id)
                return
            role = await self.role(store, message.from_user.id)
            if role is None:
                await self.telegram.send_message(
                    token, message.chat.id,
                    "The dashboard is for this store's team. Ask the owner to add you to the staff group.")
                return
            url = dashboard_url(public_base_url, store)
            if not url.startswith("https://"):
                logger.error("dashboard needs PUBLIC_BASE_URL (https)")
                return
            await self.telegram.send_message(
                token, message.chat.id,
                f"📊 {store.name} dashboard ({'owner' if role == 'owner' else 'staff'}):",
                buttons=[("📊 Open dashboard", f"{WEB_APP_PREFIX}{url}")])
        except TelegramError as error:
            logger.warning("dashboard button not sent", extra={"error": error.description})
