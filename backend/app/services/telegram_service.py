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


Button = tuple[str, str]  # (label, data)
WEB_APP_PREFIX = "webapp:"  # ("📊 Dashboard", "webapp:https://…") opens a Mini App


def _button(label: str, data: str) -> dict[str, Any]:
    # A link opens in Telegram (e.g. the channel post's "Order" button, which
    # opens the sales bot on that product); "webapp:<https link>" opens a Mini
    # App (private chats only); anything else is sent back to us.
    if data.startswith(WEB_APP_PREFIX):
        return {"text": label, "web_app": {"url": data.removeprefix(WEB_APP_PREFIX)}}
    if data.startswith("https://"):
        return {"text": label, "url": data}
    return {"text": label, "callback_data": data}


def _keyboard(buttons: list[Button | list[Button]]) -> dict[str, Any]:
    """Buttons under a message. A (label, data) pair gets its own row; a
    list of pairs is one row (e.g. sizes side by side)."""
    rows = [row if isinstance(row, list) else [row] for row in buttons]
    return {"inline_keyboard": [[_button(label, data) for label, data in row] for row in rows]}


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
    A customer tapping one of the bot's buttons becomes kind "button".
    """
    press = update.callback_query
    if press is not None:
        chat_message = press.message
        if chat_message is None or chat_message.chat.type != "private" or not press.data:
            return None  # staff-group buttons are handled by staff.py
        return IncomingMessage(
            store_id=store_id,
            update_id=update.update_id,
            chat_id=chat_message.chat.id,
            message_id=chat_message.message_id,
            telegram_id=press.from_user.id,
            customer_name=press.from_user.full_name,
            customer_username=press.from_user.username,
            language_code=press.from_user.language_code,
            kind="button",
            button_data=press.data,
            callback_id=press.id,
            sent_at=chat_message.date,
        )

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
        customer_username=sender.username,
        language_code=sender.language_code,
        kind=kind,
        text=text,
        # Telegram sends each photo in several sizes; the last is the largest.
        photo_file_id=message.photo[-1].file_id if message.photo else None,
        forwarded_post=message.forwarded_from_channel(),
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
        buttons: list[Button | list[Button]] | None = None,
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
        """Send a photo (a file id the bot received earlier, or a public URL) with a caption."""
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

    async def edit_post(
        self,
        bot_token: str,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        has_photo: bool,
        buttons: list[Button | list[Button]] | None = None,
    ) -> None:
        """Change a message the bot sent: the caption of a photo, or the
        text of a text message (Phase 8d: keeping channel posts up to date)."""
        payload: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id,
                                   "reply_markup": _keyboard(buttons or [])}
        if has_photo:
            payload["caption"] = _cut(text, MAX_CAPTION_LENGTH)
            method = "editMessageCaption"
        else:
            payload["text"] = _cut(text, MAX_MESSAGE_LENGTH)
            method = "editMessageText"
        try:
            await self._call(bot_token, method, payload)
        except TelegramError as error:
            if "message is not modified" not in error.description:
                raise  # "not modified" means it already says this: fine

    async def delete_message(self, bot_token: str, chat_id: int, message_id: int) -> None:
        await self._call(bot_token, "deleteMessage", {"chat_id": chat_id, "message_id": message_id})

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
            # messages, button presses (Phase 9), and channel posts (for
            # /chatid in the store's channel, Phase 8d)
            "allowed_updates": ["message", "callback_query", "channel_post"],
            "drop_pending_updates": True,  # don't replay old messages
        })

    async def set_profile(self, bot_token: str, description: str, short_description: str,
                          commands: list[tuple[str, str]]) -> None:
        """The bot's "What can this bot do?" text (shown before Start, up to
        512 characters), its short profile text (120), and the command menu."""
        await self._call(bot_token, "setMyDescription", {"description": description[:512]})
        await self._call(bot_token, "setMyShortDescription",
                         {"short_description": short_description[:120]})
        await self._call(bot_token, "setMyCommands", {
            "commands": [{"command": name, "description": text[:256]} for name, text in commands],
        })

    async def get_chat_member_status(self, bot_token: str, chat_id: int, user_id: int) -> str | None:
        """The user's status in a group: "creator", "administrator", "member",
        "restricted", "left" or "kicked"; None if Telegram doesn't know them
        there. Raises TelegramError if Telegram can't be reached."""
        try:
            member = await self._call(bot_token, "getChatMember", {"chat_id": chat_id, "user_id": user_id})
        except TelegramError as error:
            if error.status == 400:  # e.g. "user not found", "PARTICIPANT_ID_INVALID"
                return None
            raise
        status = member.get("status")
        if status == "restricted" and not member.get("is_member", True):
            return "left"  # restricted, but no longer in the group
        return status

    async def get_chat_title(self, bot_token: str, chat_id: int) -> str | None:
        """A group's or channel's name, or None if the bot can't see it (removed)."""
        try:
            chat = await self._call(bot_token, "getChat", {"chat_id": chat_id})
        except TelegramError as error:
            if error.status in (400, 403):
                return None
            raise
        return chat.get("title")

    async def set_menu_button(self, bot_token: str, text: str, url: str, chat_id: int | None = None) -> None:
        """The button next to the message box that opens a Mini App: in one
        person's private chat (chat_id), or in every private chat (None)."""
        params: dict[str, Any] = {
            "menu_button": {"type": "web_app", "text": text[:64], "web_app": {"url": url}},
        }
        if chat_id is not None:
            params["chat_id"] = chat_id
        await self._call(bot_token, "setChatMenuButton", params)

    async def reset_menu_button(self, bot_token: str, chat_id: int) -> None:
        """Back to Telegram's usual menu (the bot's commands) in this private chat."""
        await self._call(bot_token, "setChatMenuButton",
                         {"chat_id": chat_id, "menu_button": {"type": "default"}})

    async def pin_message(self, bot_token: str, chat_id: int, message_id: int) -> None:
        """Pin quietly (needs the "pin messages" right in a group)."""
        await self._call(bot_token, "pinChatMessage",
                         {"chat_id": chat_id, "message_id": message_id, "disable_notification": True})

    async def delete_webhook(self, bot_token: str) -> None:
        await self._call(bot_token, "deleteWebhook")

    async def get_webhook_info(self, bot_token: str) -> dict[str, Any]:
        """Where Telegram currently sends messages, and the last error, if any."""
        return await self._call(bot_token, "getWebhookInfo")
