"""Store onboarding (Phase 9b, decisions D14–D17).

A store owner joins through the dashboard; the backend does each step,
because only the backend may hold the bot token:

1. create_store(): the bot token is checked with Telegram (getMe), a bot
   another store uses is refused, the store is saved as 'pending' (D14)
   with this user as its owner, and the bot is connected to our server
   right away, so the owner can finish setting up while waiting.
2. A platform admin (D15) approves it: only then do customers get answers.
   Until then the bot tells them the shop isn't taking orders yet.
3. new_link_code() + "/link <code>": sent in the staff group or the channel,
   it saves that chat on the store. Nobody has to find or type chat ids.
4. change_bot_token() (D17, owner only): the new token is checked the same
   way; the bot is reconnected.

Staff-facing texts stay in English (like the staff group alerts).
"""
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.agents.messages import bot_profile
from app.models.schemas import AuthUser, Store, TelegramUpdate
from app.services.supabase_service import DuplicateError, SupabaseService
from app.services.telegram_service import TelegramError, TelegramService

logger = logging.getLogger(__name__)

LINK_CODE_MINUTES = 30
# No 0/O or 1/I/L: the owner types the code by hand in Telegram.
LINK_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
LINK_CODE_LENGTH = 8
BOT_TOKEN_PATTERN = re.compile(r"^\d{5,15}:[A-Za-z0-9_-]{30,50}$")
LINK_COMMAND = "/link"
# Also forgiving: "/linkCODE" and "/link <CODE>" (copying the <code> placeholder).
LINK_PATTERN = re.compile(r"/link(?:@\w+)?\s*<?\s*([A-Za-z0-9]*)\s*>?", re.IGNORECASE)


class OnboardingError(Exception):
    """A step was refused. `message` is safe to show in the dashboard."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message, self.status_code = message, status_code


@dataclass
class BotCheck:
    bot_id: int
    username: str


@dataclass
class BotConnection:
    store: Store
    connected: bool  # False: saved, but Telegram couldn't be reached (try again later)
    note: str = ""


def new_link_code() -> str:
    return "".join(secrets.choice(LINK_CODE_ALPHABET) for _ in range(LINK_CODE_LENGTH))


def link_command(update: TelegramUpdate) -> tuple[int, str, str] | None:
    """"/link <code>" sent in a group or a channel: (chat id, chat type, code)."""
    message = update.message or update.channel_post
    if message is None or message.chat.type not in ("group", "supergroup", "channel"):
        return None
    # "/link CODE" or "/link@shop_bot CODE" (see LINK_PATTERN for the forgiving forms).
    found = LINK_PATTERN.fullmatch((message.text or "").strip())
    if found is None:
        return None
    return message.chat.id, message.chat.type, found.group(1).upper()


class Onboarding:
    def __init__(self, db: SupabaseService, telegram: TelegramService, public_base_url: str):
        self.db, self.telegram = db, telegram
        self.public_base_url = public_base_url.strip().rstrip("/")

    # --- The bot ----------------------------------------------------------------

    async def check_token(self, token: str) -> BotCheck:
        """Ask Telegram who the bot is. Refuses a token that doesn't work."""
        token = token.strip()
        if not BOT_TOKEN_PATTERN.fullmatch(token):
            raise OnboardingError("That doesn't look like a bot token. Copy the whole token "
                                  "@BotFather gave you, e.g. 123456789:AAH...")
        try:
            me = await self.telegram.get_me(token)
        except TelegramError as error:
            if error.status in (401, 404):
                raise OnboardingError("Telegram doesn't accept this bot token. Check it in @BotFather.")
            raise OnboardingError("Telegram couldn't be reached. Please try again.", 503)
        if not me.get("is_bot") or not me.get("username"):
            raise OnboardingError("This token doesn't belong to a bot.")
        return BotCheck(bot_id=int(me["id"]), username=me["username"])

    async def connect_bot(self, store: Store) -> None:
        """Send this bot's messages to our server, and set its profile
        (description, /start and /help). Raises TelegramError."""
        if not self.public_base_url.startswith("https://"):
            raise OnboardingError("The server's PUBLIC_BASE_URL isn't set, so the bot can't be "
                                  "connected. Please contact the platform admin.", 503)
        token = store.telegram_bot_token.get_secret_value()
        url = f"{self.public_base_url}/api/v1/webhook/{store.id}"
        await self.telegram.set_webhook(token, url, store.webhook_secret.get_secret_value())
        await self.telegram.set_profile(token, *bot_profile(store.name))

    async def _connect(self, store: Store) -> bool:
        try:
            await self.connect_bot(store)
        except TelegramError as error:
            logger.error("bot not connected", extra={"store_id": str(store.id), "error": error.description})
            return False
        return True

    # --- 1. Create a store --------------------------------------------------------

    async def create_store(self, user: AuthUser, name: str, bot_token: str) -> BotConnection:
        """From the dashboard login (Supabase): the user becomes the owner."""
        return await self._create(name, bot_token, lambda *bot: self.db.create_store(*bot, user.id))

    async def create_store_for_telegram(self, telegram_id: int, name: str, bot_token: str) -> BotConnection:
        """From the platform bot's Mini App (Phase 10b, D44): this Telegram
        account is the owner."""
        return await self._create(name, bot_token,
                                  lambda *bot: self.db.create_store_for_telegram(*bot, telegram_id))

    async def _create(self, name: str, bot_token: str, save) -> BotConnection:
        name = " ".join(name.split())
        if not 2 <= len(name) <= 80:
            raise OnboardingError("The store name must be 2 to 80 characters.")
        bot = await self.check_token(bot_token)
        if await self.db.find_store_by_bot(bot.bot_id) is not None:
            raise OnboardingError(f"@{bot.username} is already used by another store. "
                                  "Create a new bot in @BotFather for this store.", 409)
        try:
            store_id = await save(name, bot_token.strip(), bot.bot_id, bot.username, secrets.token_urlsafe(32))
        except DuplicateError:
            raise OnboardingError(f"@{bot.username} is already used by another store.", 409)
        store = await self.db.get_store_any_status(store_id)
        logger.info("store created", extra={"store_id": str(store_id), "bot": bot.username})
        connected = await self._connect(store)
        return BotConnection(store, connected, "" if connected else
                             "The store is saved, but the bot couldn't be connected yet. Try "
                             "again with 'change bot token' (same token) in a few minutes.")

    # --- 4. Change the bot token (D17) ---------------------------------------------

    async def change_bot_token(self, store: Store, bot_token: str) -> BotConnection:
        bot = await self.check_token(bot_token)
        other = await self.db.find_store_by_bot(bot.bot_id)
        if other is not None and other.id != store.id:
            raise OnboardingError(f"@{bot.username} is already used by another store.", 409)
        new_bot = store.telegram_bot_id != bot.bot_id
        old_token = store.telegram_bot_token.get_secret_value() if store.telegram_bot_token else None
        secret = secrets.token_urlsafe(32)
        try:
            await self.db.set_bot(store.id, bot_id=bot.bot_id, bot_username=bot.username,
                                  bot_token=bot_token.strip(), webhook_secret=secret)
        except DuplicateError:
            raise OnboardingError(f"@{bot.username} is already used by another store.", 409)
        if new_bot and old_token:
            try:  # the old bot stops sending messages here (best effort)
                await self.telegram.delete_webhook(old_token)
            except TelegramError:
                pass
        store = await self.db.get_store_any_status(store.id)
        connected = await self._connect(store)
        note = ""
        if new_bot:
            note = (f"Customers now chat with @{bot.username}. Add it as an admin of your channel "
                    "and to your staff group, then send /link codes there again. Posts made by "
                    "the old bot can't be edited by the new one.")
        logger.info("bot token changed", extra={"store_id": str(store.id), "new_bot": new_bot})
        return BotConnection(store, connected, note)

    # --- 3. Link the staff group or the channel -------------------------------------

    async def new_link_code(self, store: Store) -> tuple[str, datetime]:
        code = new_link_code()
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=LINK_CODE_MINUTES)
        await self.db.set_link_code(store.id, code, expires_at)
        return code, expires_at

    async def link(self, store: Store, update: TelegramUpdate) -> None:
        """/link <code> in a group (-> staff group) or channel (-> the store's
        channel). Runs in the background; never raises."""
        found = link_command(update)
        if found is None:
            return
        chat_id, chat_type, code = found
        message = update.message or update.channel_post
        token = store.telegram_bot_token.get_secret_value()
        is_channel = chat_type == "channel"
        field = "channel_id" if is_channel else "staff_chat_id"
        try:
            ok = bool(code) and await self.db.use_link_code(store.id, code, field, chat_id)
            if ok and is_channel:
                # Subscribers don't need to see it: remove the /link post.
                try:
                    await self.telegram.delete_message(token, chat_id, message.message_id)
                except TelegramError:
                    pass
                logger.info("channel linked", extra={"store_id": str(store.id)})
                return
            text = (f"✅ This group is now the staff group of {store.name}. New orders, payment "
                    "screenshots and customer questions will appear here." if ok else
                    "❌ That code is wrong, already used, or expired (codes last "
                    f"{LINK_CODE_MINUTES} minutes and work once). Get a new code in the "
                    f"dashboard and send it like this: {LINK_COMMAND} ABCD2345")
            await self.telegram.send_message(token, chat_id, text, reply_to=message.message_id)
            if ok:
                logger.info("staff group linked", extra={"store_id": str(store.id)})
        except Exception:
            logger.exception("link failed", extra={"store_id": str(store.id)})

    # --- 2. Platform admin: approve, suspend, plan (D14–D16) --------------------------

    async def set_status(self, store: Store, status: str) -> None:
        if store.status == status:
            return
        await self.db.set_store_status(store.id, status)
        logger.info("store status changed", extra={"store_id": str(store.id), "status": status})
        note = {
            "active": f"✅ {store.name} is approved. Customers can now order from the bot.",
            "suspended": f"⛔ {store.name} is suspended. The bot doesn't take orders until "
                         "the platform admin turns it back on.",
        }.get(status)
        if note and store.staff_chat_id is not None:
            try:
                await self.telegram.notify_staff(store, note)
            except TelegramError as error:
                logger.warning("status note not sent", extra={"error": error.description})
