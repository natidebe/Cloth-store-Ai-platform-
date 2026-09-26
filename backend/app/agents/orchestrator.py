"""Handles one customer message after the webhook has answered Telegram.

Phase 5: echoes text back so we can test the Telegram connection.
Phase 8 replaces the echo with the AI tool-calling loop.
"""
import logging

from app.models.schemas import Store, TelegramUpdate
from app.services.telegram_service import TelegramError, TelegramService, parse_update
from app.utils.logging import log_context

logger = logging.getLogger(__name__)

NON_TEXT_REPLY = "Sorry, I can only read text messages for now. Please type your question."


async def handle_update(store: Store, update: TelegramUpdate, telegram: TelegramService) -> None:
    """Runs in the background. Never raises: errors are logged."""
    message = parse_update(store.id, update)
    if message is None:
        logger.info("update ignored", extra={"store_id": str(store.id), "update_id": update.update_id})
        return

    with log_context(store_id=str(store.id), telegram_id=message.telegram_id,
                     update_id=message.update_id):
        logger.info("message received", extra={"kind": message.kind})

        if message.kind == "text":
            reply = f"You said: {message.text}"
        else:
            reply = NON_TEXT_REPLY

        try:
            await telegram.send_message(
                store.telegram_bot_token.get_secret_value(), message.chat_id, reply
            )
            logger.info("reply sent")
        except TelegramError as error:
            logger.error("reply failed", extra={"error": error.description, "status": error.status})
