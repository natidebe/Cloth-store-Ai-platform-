"""Connect a store's Telegram bot to this server.

Run from the backend folder, with the virtual environment active:

    python -m scripts.connect_store "Selam Shoes"            connect (or reconnect)
    python -m scripts.connect_store "Selam Shoes" --info     show where the bot sends messages
    python -m scripts.connect_store "Selam Shoes" --disconnect
    python -m scripts.connect_store "Selam Shoes" --new-secret   connect with a fresh secret

The store can be given by name or by id. It must already exist, be active,
and have its telegram_bot_token set. PUBLIC_BASE_URL in .env must be the
public https address of this server (e.g. your ngrok URL).

Connecting also sets the bot's profile: the "What can this bot do?" text
customers see before pressing Start, its short description, and the
/start and /help menu (in Amharic and English, with the store's name),
and saves the bot's Telegram id and username on the store (one store per bot).

New stores are created in the dashboard (POST /api/v1/stores, Phase 9b). This
script reconnects a bot, e.g. after the ngrok address changed.
"""
import argparse
import asyncio
import secrets
import sys
from uuid import UUID

from pydantic import SecretStr

from app.agents.onboarding import Onboarding
from app.core.config import get_settings
from app.models.schemas import Store
from app.services.supabase_service import SupabaseService
from app.services.telegram_service import TelegramError, TelegramService


async def _find_store(db: SupabaseService, name_or_id: str) -> Store:
    try:
        store = await db.get_store(UUID(name_or_id))
        stores = [store] if store else []
    except ValueError:
        stores = await db.find_store_by_name(name_or_id)
    if not stores:
        sys.exit(f'No active store "{name_or_id}". Check the name/id and that is_active is true.')
    if len(stores) > 1:
        ids = "\n  ".join(str(s.id) for s in stores)
        sys.exit(f'Several stores are called "{name_or_id}". Use the id instead:\n  {ids}')
    return stores[0]


def _print_info(info: dict) -> None:
    print(f"  url:              {info.get('url') or '(not connected)'}")
    print(f"  pending messages: {info.get('pending_update_count', 0)}")
    if info.get("last_error_message"):
        print(f"  last error:       {info['last_error_message']}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Connect a store's Telegram bot to this server.")
    parser.add_argument("store", help="store name or id")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--info", action="store_true", help="only show the current webhook")
    action.add_argument("--disconnect", action="store_true", help="stop sending messages here")
    action.add_argument("--new-secret", action="store_true", help="generate a new webhook secret")
    args = parser.parse_args()

    settings = get_settings()
    key = settings.supabase_service_role_key.get_secret_value()
    if not settings.supabase_url or not key:
        sys.exit("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in backend/.env first.")

    db = await SupabaseService.connect(settings.supabase_url, key)
    telegram = TelegramService.create()
    try:
        store = await _find_store(db, args.store)
        if store.telegram_bot_token is None:
            sys.exit(f'Store "{store.name}" has no telegram_bot_token. Add it in the stores table.')
        token = store.telegram_bot_token.get_secret_value()

        bot = await telegram.get_me(token)
        print(f'Store "{store.name}" ({store.id}) uses bot @{bot["username"]}')

        if args.info:
            _print_info(await telegram.get_webhook_info(token))
            return
        if args.disconnect:
            await telegram.delete_webhook(token)
            print("Disconnected: Telegram no longer sends this bot's messages here.")
            return

        base_url = settings.public_base_url.rstrip("/")
        if not base_url.startswith("https://") or " " in base_url:
            sys.exit(
                "PUBLIC_BASE_URL in backend/.env must be just the https address, e.g.\n"
                "  PUBLIC_BASE_URL=https://ab12-cd34.ngrok-free.app\n"
                "(not the whole ngrok 'Forwarding ... -> http://localhost:8000' line)."
            )

        secret = store.webhook_secret.get_secret_value() if store.webhook_secret else None
        if secret is None or args.new_secret:
            # Letters, digits, - and _ only: what Telegram allows for secret_token.
            secret = secrets.token_urlsafe(32)
            await db.set_webhook_secret(store.id, secret)
            print("Generated and saved a new webhook secret.")

        await db.set_bot(store.id, bot_id=int(bot["id"]), bot_username=bot["username"])
        store = store.model_copy(update={"webhook_secret": SecretStr(secret)})
        await Onboarding(db, telegram, base_url).connect_bot(store)
        print("Connected. Telegram now sends this bot's messages to:")
        _print_info(await telegram.get_webhook_info(token))
        print("Bot description and /start, /help menu set.")
    except TelegramError as error:
        sys.exit(f"Telegram said: {error.description}")
    finally:
        await telegram.close()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
