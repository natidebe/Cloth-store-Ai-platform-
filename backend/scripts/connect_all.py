"""Point every bot at this server: each store's bot and the platform bot.

Run it once after deploying to a new address, or whenever PUBLIC_BASE_URL
changes (e.g. a new ngrok address). From the backend folder:

    python -m scripts.connect_all             connect them all
    python -m scripts.connect_all --check     only show where each bot sends its messages

For each pending or active store with a bot: the webhook (with the store's
own secret), the bot's description and /start, /help menu. Then the platform
bot, if PLATFORM_BOT_TOKEN is set. One store failing doesn't stop the others.
"""
import argparse
import asyncio
import sys

from app.agents.onboarding import Onboarding, OnboardingError
from app.api.v1.platform_app import platform_app_url, platform_webhook_secret
from app.core.config import get_settings
from app.services.supabase_service import SupabaseService
from app.services.telegram_service import TelegramError, TelegramService


async def main() -> None:
    parser = argparse.ArgumentParser(description="Point every bot at this server.")
    parser.add_argument("--check", action="store_true", help="only show where each bot sends its messages")
    args = parser.parse_args()

    settings = get_settings()
    key = settings.supabase_service_role_key.get_secret_value()
    base_url = settings.public_base_url.strip().rstrip("/")
    if not settings.supabase_url or not key:
        sys.exit("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY first.")
    if not base_url.startswith("https://") and not args.check:
        sys.exit("PUBLIC_BASE_URL must be this server's https address.")

    db = await SupabaseService.connect(settings.supabase_url, key)
    telegram = TelegramService.create()
    onboarding = Onboarding(db, telegram, base_url)
    failed = 0
    try:
        stores = await db.stores_to_connect()
        print(f"{len(stores)} store bot(s):")
        for store in stores:
            label = f"  {store.name} (@{store.telegram_bot_username or '?'}, {store.status})"
            try:
                if args.check:
                    info = await telegram.get_webhook_info(store.telegram_bot_token.get_secret_value())
                    print(f"{label}: {info.get('url') or 'not connected'}"
                          + (f" (last error: {info['last_error_message']})" if info.get("last_error_message") else ""))
                    continue
                if store.webhook_secret is None:
                    print(f"{label}: skipped, no webhook secret (run scripts.connect_store for it)")
                    failed += 1
                    continue
                await onboarding.connect_bot(store)
                print(f"{label}: connected")
            except (TelegramError, OnboardingError) as error:
                failed += 1
                print(f"{label}: FAILED ({getattr(error, 'description', None) or error})")

        token = settings.platform_bot_token.get_secret_value()
        if token:
            try:
                if args.check:
                    info = await telegram.get_webhook_info(token)
                    print(f"Platform bot: {info.get('url') or 'not connected'}")
                else:
                    await telegram.set_webhook(token, f"{base_url}/api/v1/platform-bot/webhook",
                                               platform_webhook_secret(token))
                    await telegram.set_menu_button(token, "Open / ክፈት", platform_app_url())
                    print("Platform bot: connected")
            except TelegramError as error:
                failed += 1
                print(f"Platform bot: FAILED ({error.description})")
        else:
            print("Platform bot: not set up (PLATFORM_BOT_TOKEN is empty)")
    finally:
        await telegram.close()
        await db.close()
    if failed:
        sys.exit(f"{failed} failed: see above.")


if __name__ == "__main__":
    asyncio.run(main())
