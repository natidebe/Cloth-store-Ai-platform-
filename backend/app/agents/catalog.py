"""The store's Telegram channel as its catalog (Phase 8d, D30–D40).

- publish():   post a product to the store's channel: its photo, name, price,
               colors and sizes in stock, description, code, and a
               [🛒 Order] button that opens the sales bot on that product
               (https://t.me/<bot>?start=p_<code>).
- sync():      edit the product's posts when stock, price or details change
               (sizes sold out, "SOLD OUT", a new price).
- on_change(): Supabase database webhooks call this for every change to
               products and product_variants, whoever made it (D39). Quick
               changes are grouped: 3 seconds after the last one, the post is
               edited once. A new product is posted automatically (D37).
- reconcile(): the minute sweep's safety net: webhooks aren't retried, so it
               compares every post with the database and fixes any that
               differ (by a fingerprint of the caption).

Captions show stock_quantity, not "available": the 5-minute holds of unpaid
orders don't change posts, so they don't flicker.
Posts are in Amharic and English (channel readers speak either).
"""
import asyncio
import hashlib
import logging
from dataclasses import dataclass
from uuid import UUID

from app.agents.messages import format_price
from app.agents.shop_types import condition_text, labels, lower_word, warranty_text
from app.models.schemas import Product, ProductPost, Store, VariantMatch
from app.services.supabase_service import SupabaseService
from app.services.telegram_service import MAX_CAPTION_LENGTH, TelegramError, TelegramService
from app.utils.logging import log_context

logger = logging.getLogger(__name__)

GROUP_CHANGES_SECONDS = 3.0  # wait for more changes, then edit the post once
ORDER_LABEL = "🛒 እዘዝ / Order"
SOLD_OUT = "❌ ተሽጧል / SOLD OUT"
GONE = "❌ ይህ ምርት አሁን የለም / No longer available"


@dataclass
class PublishResult:
    ok: bool
    message: str


def order_link(bot_username: str, code: str) -> str:
    """The Order button: opens the sales bot, which gets "/start p_<code>"."""
    return f"https://t.me/{bot_username}?start=p_{code}"


def caption(product: Product, variants: list[VariantMatch], store: object | None = None) -> str:
    """The channel post's text, from the database, in the shop's words (Phase 13)."""
    in_stock = [v for v in variants if v.stock_quantity > 0 and v.price is not None]
    title = product.name + (f" ({product.brand})" if product.brand else "")
    lines = []
    if not in_stock:
        lines.append(SOLD_OUT)
    lines.append(f"🆕 {title}")
    prices = sorted({v.price for v in in_stock} or ({product.base_price} if product.base_price else set()))
    if prices:
        price = format_price(prices[0])
        lines.append(f"💰 {'ከ / from ' if len(prices) > 1 else ''}{price}")
    # Electronics (D60): new or used, and the warranty.
    extras = [(condition_text(product.condition, "am"), condition_text(product.condition, "en")),
              (warranty_text(product.warranty_months, "am"), warranty_text(product.warranty_months, "en"))]
    extras = [f"{am} / {en}" for am, en in extras if en]
    if extras:
        lines.append(("✨ " if product.condition == "new" else "🛡️ ") + " · ".join(extras))
    # The two options are optional (a bag, a belt, jewelry): only what exists is
    # shown, under the shop's names for them (Phase 13: Colors & sizes, Storage…).
    first, second = labels(store)
    has_colors = any(v.color for v in in_stock)
    has_sizes = any(v.size for v in in_stock)
    if has_colors and has_sizes:
        lines.append("")
        lines.append(f"{first.icon} {first.am} እና {second.am} / {first.plural} & {lower_word(second.plural)}:")
        colors: dict[str, list[str]] = {}
        for v in in_stock:
            colors.setdefault(v.color or "—", [])
            if v.size and v.size not in colors[v.color or "—"]:
                colors[v.color or "—"].append(v.size)
        for color, sizes in colors.items():
            lines.append(f"• {color}: {', '.join(sizes)}" if sizes else f"• {color}")
    elif has_colors:
        lines.append("")
        lines.append(f"{first.icon} {first.am} / {first.plural}: "
                     f"{', '.join(dict.fromkeys(v.color for v in in_stock if v.color))}")
    elif has_sizes:
        lines.append("")
        lines.append(f"{second.icon} {second.am} / {second.plural}: "
                     f"{', '.join(dict.fromkeys(v.size for v in in_stock if v.size))}")
    if product.description:
        lines.append("")
        lines.append(product.description.strip())
    lines.append("")
    lines.append(f"🔖 {product.code}")
    return "\n".join(lines)[:MAX_CAPTION_LENGTH]


def fingerprint(text: str, link: str | None) -> str:
    return hashlib.sha256(f"{text}\n{link or ''}".encode()).hexdigest()[:32]


class Catalog:
    """Created once at startup, shared by all stores."""

    def __init__(self, db: SupabaseService, telegram: TelegramService,
                 group_wait: float = GROUP_CHANGES_SECONDS):
        self.db = db
        self.telegram = telegram
        self.group_wait = group_wait
        self._bot_usernames: dict[UUID, str] = {}
        self._pending: dict[tuple, bool] = {}  # (store, product) -> post it if it has no post yet
        self._tasks: set[asyncio.Task] = set()

    async def bot_username(self, store: Store) -> str:
        if store.id not in self._bot_usernames:
            me = await self.telegram.get_me(store.telegram_bot_token.get_secret_value())
            self._bot_usernames[store.id] = me["username"]
        return self._bot_usernames[store.id]

    # --- Posting and editing ---------------------------------------------------

    async def publish(self, store: Store, product_id: UUID) -> PublishResult:
        """Post the product to the channel, or update its post if it has one."""
        if store.channel_id is None:
            return PublishResult(False, "The store has no channel (set stores.channel_id).")
        product = await self.db.get_product(store.id, product_id)
        if product is None:
            return PublishResult(False, "Product not found.")
        if await self.db.list_product_posts(store.id, product_id=product.id):
            await self.sync(store, product.id)
            return PublishResult(True, f"{product.code} was already posted; its post is up to date.")

        variants = await self.db.get_product_variants(store.id, product.id)
        text = caption(product, variants, store)
        link = order_link(await self.bot_username(store), product.code)
        token = store.telegram_bot_token.get_secret_value()
        buttons = [(ORDER_LABEL, link)]
        if product.photo_url:
            message_id = await self.telegram.send_photo(token, store.channel_id, product.photo_url, text,
                                                        buttons=buttons)
        else:
            message_id = await self.telegram.send_message(token, store.channel_id, text, buttons=buttons)
        await self.db.save_product_post(store.id, product, store.channel_id, message_id,
                                        bool(product.photo_url), fingerprint(text, link))
        logger.info("product posted to the channel", extra={"product": product.code})
        return PublishResult(True, f"{product.code} is posted in the channel.")

    async def sync(self, store: Store, product_id: UUID) -> int:
        """Bring this product's posts up to date. Returns how many were edited."""
        product = await self.db.get_product(store.id, product_id)
        posts = await self.db.list_product_posts(store.id, product_id=product_id)
        if product is None or not posts:
            return 0
        variants = await self.db.get_product_variants(store.id, product.id)
        text = caption(product, variants, store)
        link = order_link(await self.bot_username(store), product.code)
        return await self._edit(store, posts, text, [(ORDER_LABEL, link)], fingerprint(text, link))

    async def sync_deleted(self, store: Store, code: str | None) -> int:
        """The product was deleted: its posts say it's no longer available."""
        if not code:
            return 0
        posts = await self.db.list_product_posts(store.id, code=code)
        text = f"{GONE}\n\n🔖 {code}"
        return await self._edit(store, posts, text, [], fingerprint(text, None))

    async def _edit(self, store: Store, posts: list[ProductPost], text: str, buttons: list,
                    wanted: str) -> int:
        edited = 0
        token = store.telegram_bot_token.get_secret_value()
        for post in posts:
            if post.caption_hash == wanted:
                continue  # already says this
            try:
                await self.telegram.edit_post(token, post.channel_id, post.message_id, text,
                                              has_photo=post.has_photo, buttons=buttons)
            except TelegramError as error:
                logger.warning("channel post not updated", extra={"error": error.description,
                                                                  "message_id": post.message_id})
                continue
            await self.db.set_post_hash(store.id, post.id, wanted)
            edited += 1
        if edited:
            logger.info("channel posts updated", extra={"count": edited})
        return edited

    # --- Database webhooks (D39) -----------------------------------------------

    async def on_change(self, change: dict) -> None:
        """A Supabase database webhook: a row of products or product_variants
        was inserted, updated or deleted. Never raises."""
        table, kind = change.get("table"), change.get("type")
        record = change.get("record") or change.get("old_record") or {}
        try:
            store_id = UUID(record["store_id"])
            if table == "products" and kind == "DELETE":
                store = await self.db.get_store(store_id)
                if store is not None:
                    await self.sync_deleted(store, record.get("code"))
                return
            if table == "products":
                product_id, post_if_new = UUID(record["id"]), kind == "INSERT"
            elif table == "product_variants":
                product_id, post_if_new = UUID(record["product_id"]), False
            else:
                return
        except (KeyError, TypeError, ValueError):
            logger.warning("unreadable catalog webhook ignored", extra={"table": table})
            return
        self._group(store_id, product_id, post_if_new)

    def _group(self, store_id: UUID, product_id: UUID, post_if_new: bool) -> None:
        """Handle this product once, a moment after its last change."""
        key = (store_id, product_id)
        if key in self._pending:
            self._pending[key] = self._pending[key] or post_if_new
            return
        self._pending[key] = post_if_new
        task = asyncio.create_task(self._handle_later(key))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _handle_later(self, key: tuple) -> None:
        if self.group_wait:
            await asyncio.sleep(self.group_wait)
        post_if_new = self._pending.pop(key, False)
        store_id, product_id = key
        with log_context(store_id=str(store_id)):
            try:
                store = await self.db.get_store(store_id)
                if store is None or store.channel_id is None:
                    return
                if post_if_new and not await self.db.list_product_posts(store_id, product_id=product_id):
                    await self.publish(store, product_id)  # D37: new products are posted
                else:
                    await self.sync(store, product_id)
            except Exception:
                logger.exception("catalog update failed; the sweep will try again")

    async def settle(self) -> None:
        """Wait for grouped changes still pending (tests, shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    # --- The sweep's safety net -----------------------------------------------

    async def reconcile(self) -> int:
        """Fix every post that doesn't match the database. Only existing posts
        are checked: old products aren't posted automatically (that could
        flood a channel the moment it's connected)."""
        fixed = 0
        for store in await self.db.stores_with_channel():
            with log_context(store_id=str(store.id)):
                try:
                    posts = await self.db.list_product_posts(store.id)
                    products = {p.id: p for p in await self.db.list_catalog(store.id)}
                    for product_id in {p.product_id for p in posts if p.product_id}:
                        if product_id in products:
                            fixed += await self.sync(store, product_id)
                    for code in {p.product_code for p in posts if p.product_id is None}:
                        fixed += await self.sync_deleted(store, code)
                except Exception:
                    logger.exception("catalog check failed for a store")
        if fixed:
            logger.info("catalog check fixed posts", extra={"count": fixed})
        return fixed

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
