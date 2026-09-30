"""Post products to a store's Telegram channel (Phase 8d), without the dashboard.

Run from the backend folder, with the virtual environment active:

    python -m scripts.publish_product "Selam Shoes" P101          post one product (or update its post)
    python -m scripts.publish_product "Selam Shoes" --all         post every product not posted yet
    python -m scripts.publish_product "Selam Shoes" --check       fix posts that differ from the database (all stores)

The store needs channel_id set (send /chatid in the channel) and the bot must
be an admin of the channel who can post and edit messages.
"""
import argparse
import asyncio
import sys

from app.agents.catalog import Catalog
from app.core.config import get_settings
from app.services.supabase_service import SupabaseService
from app.services.telegram_service import TelegramError, TelegramService
from scripts.connect_store import _find_store


async def main() -> None:
    parser = argparse.ArgumentParser(description="Post products to a store's Telegram channel.")
    parser.add_argument("store", help="store name or id")
    parser.add_argument("code", nargs="?", help="product code, e.g. P101")
    parser.add_argument("--all", action="store_true", help="post every product that has no post yet")
    parser.add_argument("--check", action="store_true", help="update posts that don't match the database")
    args = parser.parse_args()
    if bool(args.code) + args.all + args.check != 1:
        parser.error("give a product code, --all, or --check")

    settings = get_settings()
    key = settings.supabase_service_role_key.get_secret_value()
    if not settings.supabase_url or not key:
        sys.exit("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in backend/.env")
    db = await SupabaseService.connect(settings.supabase_url, key)
    telegram = TelegramService.create()
    catalog = Catalog(db, telegram)
    try:
        store = await _find_store(db, args.store)
        if store.channel_id is None:
            sys.exit(f"{store.name} has no channel_id. Add the bot to the channel as an admin, "
                     "send /chatid in the channel, and save the number in stores.channel_id.")
        if args.check:
            print(f"{await catalog.reconcile()} post(s) updated.")
            return
        if args.code:
            product = await db.find_product_by_code(store.id, args.code)
            if product is None:
                sys.exit(f"{store.name} has no product {args.code.upper()}.")
            products = [product]
        else:
            posted = {p.product_id for p in await db.list_product_posts(store.id)}
            products = [p for p in await db.list_catalog(store.id) if p.id not in posted]
            if not products:
                print("Every product is already posted.")
        for product in products:
            try:
                result = await catalog.publish(store, product.id)
            except TelegramError as error:
                sys.exit(f"{product.code}: Telegram refused: {error.description}")
            print(f"{product.code} {product.name}: {result.message}")
    finally:
        await catalog.close()
        await telegram.close()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
