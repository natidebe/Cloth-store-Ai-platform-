"""Connect the platform bot (Phase 10b, D44): shops sign up in its Mini App,
and platform admins approve stores there.

Run from the backend folder, with the virtual environment active:

    python -m scripts.connect_platform_bot

Needs in backend/.env: PLATFORM_BOT_TOKEN (create the bot in @BotFather) and
PUBLIC_BASE_URL (this server's https address). It sends the bot's messages to
this server, puts an "Open" button next to the message box that opens the
Mini App, and sets the bot's description. Run it again when the address
changes (e.g. a new ngrok address).
"""
import asyncio
import sys

from app.api.v1.platform_app import platform_app_url, platform_webhook_secret
from app.core.config import get_settings
from app.services.telegram_service import TelegramError, TelegramService

DESCRIPTION = (
    "🛍 ሱቅዎን በቴሌግራም ይክፈቱ፦ ደንበኞች ከቻናልዎ ይዘዛሉ፣ ቦቱ ትዕዛዞችን ይቀበላል።\n"
    "Open your shop on Telegram: customers order from your channel, your bot takes the orders.\n\n"
    "Press Open / ክፈት to create your store."
)


async def main() -> None:
    settings = get_settings()
    token = settings.platform_bot_token.get_secret_value()
    base_url = settings.public_base_url.strip().rstrip("/")
    if not token:
        sys.exit("Set PLATFORM_BOT_TOKEN in backend/.env first (create the bot in @BotFather).")
    if not base_url.startswith("https://"):
        sys.exit("PUBLIC_BASE_URL in backend/.env must be this server's https address.")

    telegram = TelegramService.create()
    try:
        bot = await telegram.get_me(token)
        await telegram.set_webhook(token, f"{base_url}/api/v1/platform-bot/webhook",
                                   platform_webhook_secret(token))
        await telegram.set_menu_button(token, "Open / ክፈት", platform_app_url())
        await telegram.set_profile(token, DESCRIPTION, "Open your shop on Telegram. ሱቅዎን በቴሌግራም ይክፈቱ።",
                                   [("start", "Open / ክፈት")])
        print(f"Connected @{bot['username']}: messages go to {base_url}, and its menu button opens "
              f"{platform_app_url()}")
    except TelegramError as error:
        sys.exit(f"Telegram said: {error.description}")
    finally:
        await telegram.close()


if __name__ == "__main__":
    asyncio.run(main())
