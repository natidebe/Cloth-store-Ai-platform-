"""Everything that talks to the Telegram Bot API.

The only module that calls Telegram. Each store has its own bot, so every
call takes that store's bot token.

The token is part of Telegram's URL (https://api.telegram.org/bot<TOKEN>/...),
so errors and logs from this module never include the URL.
"""
import logging
from typing import Any
from uuid import UUID

import httpx

from app.models.schemas import IncomingMessage, Store, TelegramUpdate

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"
MAX_MESSAGE_LENGTH = 4096  # Telegram's limit for one text message
MAX_CAPTION_LENGTH = 1024  # ...and for a photo's caption
_TIMEOUT_SECONDS = 15.0


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _keyboard(buttons: list[tuple[str, str]]) -> dict[str, Any]:
    """Buttons under a message, one per row."""
    return {"inline_keyboard": [[{"text": label, "callback_data": data}] for label, data in buttons]}


class TelegramError(Exception):
    """A Telegram call failed. The message never contains the bot token."""

    def __init__(self, method: str, description: str, status: int | None = None):
        super().__init__(f"Telegram {method} failed: {description}")
        self.method = method
        self.description = description
        self.status = status


def parse_update(store_id: UUID, update: TelegramUpdate) -> IncomingMessage | None:
    """Turn a Telegram update into an IncomingMessage.

    Returns None for anything the bot should ignore: edited messages,
    messages from groups or channels, and messages from other bots.
    """
    message = update.message
    if message is None or message.chat.type != "private":
        return None
    sender = message.from_user
    if sender is None or sender.is_bot:
        return None

    if message.photo:
        kind, text = "photo", message.caption
    elif message.text:
        kind, text = "text", message.text
    elif message.sticker:
        kind, text = "sticker", None
    elif message.voice:
        kind, text = "voice", None
    elif message.document:
        kind, text = "document", message.caption
    else:
        kind, text = "other", None

    return IncomingMessage(
        store_id=store_id,
        update_id=update.update_id,
        chat_id=message.chat.id,
        message_id=message.message_id,
        telegram_id=sender.id,
        customer_name=sender.full_name,
        language_code=sender.language_code,
        kind=kind,
        text=text,
        # Telegram sends each photo in several sizes; the last is the largest.
        photo_file_id=message.photo[-1].file_id if message.photo else None,
        sent_at=message.date,
    )


class TelegramService:
    def __init__(self, http: httpx.AsyncClient):
        self._http = http

    @classmethod
    def create(cls) -> "TelegramService":
        """Create once at app startup; all stores share one HTTP client."""
        return cls(httpx.AsyncClient(base_url=TELEGRAM_API, timeout=_TIMEOUT_SECONDS))

    async def close(self) -> None:
        await self._http.aclose()

    async def _call(self, bot_token: str, method: str, payload: dict[str, Any] | None = None) -> Any:
        try:
            response = await self._http.post(f"/bot{bot_token}/{method}", json=payload or {})
        except httpx.HTTPError as error:
            # str(error) can include the URL, and so the token: use only the type.
            raise TelegramError(method, type(error).__name__) from None
        try:
            body = response.json()
        except ValueError:
            raise TelegramError(method, "response was not JSON", response.status_code) from None
        if not body.get("ok"):
            raise TelegramError(method, body.get("description", "unknown error"), response.status_code)
        return body["result"]

    # --- Messages -----------------------------------------------------------

    async def send_message(
        self,
        bot_token: str,
        chat_id: int,
        text: str,
        *,
        buttons: list[tuple[str, str]] | None = None,
        reply_to: int | None = None,
    ) -> int | None:
        """Send a text message. Returns its Telegram message id.

        buttons: (label, data) pairs shown under the message, one per row.
        Pressing one sends us a callback query with that data.
        reply_to: show it as a reply to this message in the same chat.
        """
        payload: dict[str, Any] = {"chat_id": chat_id, "text": _cut(text, MAX_MESSAGE_LENGTH)}
        if buttons:
            payload["reply_markup"] = _keyboard(buttons)
        if reply_to:
            payload["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
        result = await self._call(bot_token, "sendMessage", payload)
        return result.get("message_id") if isinstance(result, dict) else None

    async def send_photo(
        self,
        bot_token: str,
        chat_id: int,
        photo_file_id: str,
        caption: str,
        *,
        buttons: list[tuple[str, str]] | None = None,
    ) -> int | None:
        """Send a photo the bot received earlier (by its file id), with a caption."""
        payload: dict[str, Any] = {
            "chat_id": chat_id, "photo": photo_file_id, "caption": _cut(caption, MAX_CAPTION_LENGTH),
        }
        if buttons:
            payload["reply_markup"] = _keyboard(buttons)
        result = await self._call(bot_token, "sendPhoto", payload)
        return result.get("message_id") if isinstance(result, dict) else None

    async def answer_button(self, bot_token: str, callback_id: str, text: str,
                            *, popup: bool = False) -> None:
        """Answer a button press: a short notice for the person who pressed it
        (popup=True shows a box they must close). Telegram shows a spinner on
        the button until this is called."""
        await self._call(bot_token, "answerCallbackQuery", {
            "callback_query_id": callback_id, "text": _cut(text, 200), "show_alert": popup,
        })

    async def remove_buttons(self, bot_token: str, chat_id: int, message_id: int) -> None:
        """Remove the buttons under a message (once they've been used)."""
        await self._call(bot_token, "editMessageReplyMarkup", {
            "chat_id": chat_id, "message_id": message_id,
            "reply_markup": {"inline_keyboard": []},
        })

    async def notify_staff(self, store: Store, text: str) -> bool:
        """Send a message to the store's staff group.

        Returns False (and logs a warning) if the store has no staff group yet.
        """
        if store.staff_chat_id is None or store.telegram_bot_token is None:
            logger.warning("store has no staff group; staff not notified",
                           extra={"store_id": str(store.id)})
            return False
        await self.send_message(store.telegram_bot_token.get_secret_value(), store.staff_chat_id, text)
        return True

    # --- Bot setup ----------------------------------------------------------

    async def get_me(self, bot_token: str) -> dict[str, Any]:
        """Check a bot token. Returns the bot's id, username, and name."""
        return await self._call(bot_token, "getMe")

    async def set_webhook(self, bot_token: str, url: str, secret: str) -> None:
        """Tell Telegram to send this bot's messages to `url`, with `secret`
        in the X-Telegram-Bot-Api-Secret-Token header."""
        await self._call(bot_token, "setWebhook", {
            "url": url,
            "secret_token": secret,
            # messages, plus button presses in the staff group (Phase 9)
            "allowed_updates": ["message", "callback_query"],
            "drop_pending_updates": True,  # don't replay old messages
        })

    async def delete_webhook(self, bot_token: str) -> None:
        await self._call(bot_token, "deleteWebhook")

    async def get_webhook_info(self, bot_token: str) -> dict[str, Any]:
        """Where Telegram currently sends messages, and the last error, if any."""
        return await self._call(bot_token, "getWebhookInfo")
