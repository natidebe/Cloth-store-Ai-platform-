"""The store's Mini App: analytics, products & stock, settings (Phase 10b, D42–D43).

Every request carries the Telegram user's signed initData in the
X-Telegram-Init-Data header (Telegram.WebApp.initData). It must be signed by
THIS store's bot, and the user must be in the store's staff group (or have
created the store). Members are "staff", the group's admins "owners":

    everyone   GET  /api/v1/app/stores/{store}/me
               GET  /api/v1/app/stores/{store}/analytics?period=today|week|month|7d|30d
               GET  /api/v1/app/stores/{store}/products[?search=&category=]
               GET  /api/v1/app/stores/{store}/products/{product}
               POST /api/v1/app/stores/{store}/variants/{variant}/stock   {"change": 5} or {"set": 12}
               PATCH /api/v1/app/stores/{store}/products/{product}        details (staff: not the price)
               POST /api/v1/app/stores/{store}/photos                     upload a photo -> its link
               GET  /api/v1/app/stores/{store}/orders?status=all|unpaid|paid   view only
               POST /api/v1/app/stores/{store}/counter-sales              a sale in the shop (Phase 12)
               GET  /api/v1/app/stores/{store}/variants/{variant}/availability   stock + online holds
               POST /api/v1/app/stores/{store}/products                   a product + its grid (staff: no prices)
               PUT  /api/v1/app/stores/{store}/products/{product}/variants   save the grid (staff: no prices)
    owners
               POST /api/v1/app/stores/{store}/products/{product}/off-sale
               DELETE /api/v1/app/stores/{store}/products/{product}       never-ordered products only
               POST /api/v1/app/stores/{store}/products/{product}/publish to the channel
               GET/PUT /api/v1/app/stores/{store}/settings                payment accounts, delivery, ...
               GET  /api/v1/app/stores/{store}/connections                the linked group and channel
               POST /api/v1/app/stores/{store}/orders/export   {"month": "2026-09"}  Excel file sent by the bot
               POST /api/v1/app/stores/{store}/link-code                  /link code for the group/channel
               PUT  /api/v1/app/stores/{store}/bot-token                  change the bot (D17)
"""
import logging
import secrets
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field, field_validator

from app.agents.analytics import Period, store_analytics
from app.agents.catalog import Catalog
from app.agents.counter import CounterSaleError, CounterSales, SaleLine, SaleRequest
from app.agents.export import XLSX, ExportError, build_export, month_bounds
from app.agents.inventory import GridRow, Inventory, InventoryError, summarize
from app.agents.messages import bot_profile
from app.agents.miniapp import AppAccess
from app.agents.shop_types import ShopType, clean_labels, shop_json, shop_type_of, shop_types_json
from app.agents.subscriptions import ai_limit
from app.agents.onboarding import LINK_CODE_MINUTES, LINK_COMMAND, Onboarding, OnboardingError
from app.agents.store_profile import (
    DeliveryArea,
    OpeningWeek,
    PaymentAccount,
    delivery_text,
    hours_text,
    payment_text,
)
from app.agents.tools import ADDRESS_TO_ARRANGE, order_number
from app.agents.orchestrator import Orchestrator
from app.api.v1.catalog import get_catalog
from app.api.v1.webhook import get_db, get_orchestrator
from app.core.config import get_settings
from app.core.telegram_auth import INIT_DATA_HEADER, check_init_data
from app.models.schemas import PROFILE_FIELDS, Store
from app.services.supabase_service import SupabaseService
from app.services.telegram_service import TelegramError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/app/stores/{store_id}", tags=["mini app"])

PHOTO_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
MAX_PHOTO_BYTES = 5 * 1024 * 1024
MAX_GRID_ROWS = 100


# --- Who is calling ----------------------------------------------------------------

async def app_access(
    store_id: UUID,
    init_data: str | None = Header(default=None, alias=INIT_DATA_HEADER),
    db: SupabaseService = Depends(get_db),
    orchestrator: Orchestrator = Depends(get_orchestrator),
) -> AppAccess:
    """401 without valid Telegram initData from this store's bot; 403 if the
    user isn't in the store's staff group (and didn't create the store)."""
    store = await db.get_store_any_status(store_id)
    if store is None:
        raise HTTPException(status_code=404, detail="store not found")
    token = store.telegram_bot_token.get_secret_value() if store.telegram_bot_token else None
    user = check_init_data(init_data, token)
    if user is None:
        raise HTTPException(status_code=401, detail="open the dashboard from the store's bot in Telegram")
    role = await orchestrator.app_access.role(store, user.id)
    if role is None:
        raise HTTPException(status_code=403, detail="only members of this store's staff group can open it")
    return AppAccess(store=store, user=user, role=role)


async def owner_access(access: AppAccess = Depends(app_access)) -> AppAccess:
    if not access.is_owner:
        raise HTTPException(status_code=403, detail="only the store's owners (staff group admins) can do this")
    return access


def get_onboarding(db: SupabaseService = Depends(get_db),
                   orchestrator: Orchestrator = Depends(get_orchestrator)) -> Onboarding:
    return Onboarding(db, orchestrator.telegram, get_settings().public_base_url)


def _price(value: Any) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


def _refused(error: InventoryError | OnboardingError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail=error.message)


# --- Requests ----------------------------------------------------------------------------

def _clean(value: str | None) -> str | None:
    value = " ".join((value or "").split())
    return value or None


class ProductFields(BaseModel):
    """A product's details. On PATCH, only the fields sent are changed."""
    name: str | None = Field(default=None, min_length=1, max_length=80)
    brand: str | None = Field(default=None, max_length=60)
    category: str | None = Field(default=None, max_length=40)
    base_price: Decimal | None = Field(default=None, ge=0, le=10_000_000)
    description: str | None = Field(default=None, max_length=700)
    search_keywords: str | None = Field(default=None, max_length=500)
    photo_url: str | None = Field(default=None, max_length=1000)
    # Electronics (Phase 13, D60/D62): new or used, and months of warranty (0 = none).
    condition: Literal["new", "used"] | None = None
    warranty_months: int | None = Field(default=None, ge=0, le=120)

    @field_validator("name", "brand", "search_keywords")
    @classmethod
    def _one_line(cls, value: str | None) -> str | None:
        return _clean(value)

    @field_validator("category")
    @classmethod
    def _category(cls, value: str | None) -> str | None:
        value = _clean(value)
        return value.lower() if value else None

    @field_validator("description")
    @classmethod
    def _description(cls, value: str | None) -> str | None:
        return (value or "").strip() or None

    @field_validator("photo_url")
    @classmethod
    def _photo(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        if value and not value.startswith("https://"):
            raise ValueError("the photo must be an https:// link (upload it with /photos)")
        return value or None


class VariantIn(BaseModel):
    """One cell of the color × size grid."""
    id: UUID | None = None
    color: str | None = Field(default=None, max_length=30)
    size: str | None = Field(default=None, max_length=20)
    stock: int = Field(ge=0, le=100_000)
    price: Decimal | None = Field(default=None, ge=0, le=10_000_000)  # None: the product's price

    @field_validator("color", "size")
    @classmethod
    def _text(cls, value: str | None) -> str | None:
        return _clean(value)

    def row(self) -> GridRow:
        return GridRow(color=self.color, size=self.size, stock=self.stock, price=self.price, id=self.id)


class NewProduct(BaseModel):
    product: ProductFields
    variants: list[VariantIn] = Field(default_factory=list, max_length=MAX_GRID_ROWS)


class GridIn(BaseModel):
    variants: list[VariantIn] = Field(default_factory=list, max_length=MAX_GRID_ROWS)
    remove: list[UUID] = Field(default_factory=list, max_length=MAX_GRID_ROWS)


class StockChange(BaseModel):
    change: int | None = Field(default=None, ge=-100_000, le=100_000)  # +5 arrived, -1 sold at the counter
    set: int | None = Field(default=None, ge=0, le=100_000)  # the exact count


class SettingsIn(BaseModel):
    """The Store profile screen. Only the fields sent change; an empty text
    clears one. The lists also rewrite the texts the bot sends (migration 011)."""
    payment_accounts: list[PaymentAccount] | None = Field(default=None, max_length=10)
    delivery_areas: list[DeliveryArea] | None = Field(default=None, max_length=30)
    opening_week: OpeningWeek | None = None
    location: str | None = Field(default=None, max_length=1000)
    pickup_instructions: str | None = Field(default=None, max_length=1000)
    return_policy: str | None = Field(default=None, max_length=1000)
    # Counter sales (D53): how far below the listed price staff may go, in percent.
    staff_discount_percent: Decimal | None = Field(default=None, ge=0, le=100)
    # Shop types (Phase 13, D58/D59/D61): the kind of shop, and the owner's own
    # words for the two options ({"option2": {"en": "Model", "am": "ሞዴል"}}; null = the type's).
    shop_type: ShopType | None = None
    option_labels: dict[str, dict[str, str]] | None = None
    # Phase 15 (D70): the owner's morning summary, in Amharic or English, or off.
    daily_summary: Literal["am", "en", "off"] | None = None


class BotTokenIn(BaseModel):
    bot_token: str = Field(min_length=20, max_length=100)


# --- Who am I ---------------------------------------------------------------------------

@router.get("/me")
async def me(access: AppAccess = Depends(app_access)) -> dict[str, Any]:
    store = access.store
    return {
        "user": {"id": access.user.id, "name": access.user.full_name, "username": access.user.username,
                 "language_code": access.user.language_code},
        "role": access.role,
        "store": {"id": store.id, "name": store.name, "status": store.status, "plan": store.plan,
                  "bot_username": store.telegram_bot_username,
                  "staff_group_linked": store.staff_chat_id is not None,
                  "channel_linked": store.channel_id is not None,
                  # Counter sales (Phase 12): staff's lowest price, and how walk-ins can pay.
                  "staff_discount_percent": store.staff_discount_percent,
                  "payment_methods": payment_methods(store),
                  # Phase 13: the shop's type and its words for the two options.
                  **shop_json(store),
                  # Phase 14: the plan and when it ends (None: not approved yet).
                  "plan_ends_at": store.plan_ends_at,
                  "suspended_reason": store.suspended_reason,
                  # Phase 15: the owner's morning summary ('am', 'en' or 'off').
                  "daily_summary": store.daily_summary},
    }


def payment_methods(store: Store) -> list[str]:
    """Cash, then the store's own payment accounts (D56: anything else is "Other")."""
    names = ["Cash"]
    for account in store.payment_accounts:
        name = str(account.get("name") or "").strip()
        if name and name.lower() not in {n.lower() for n in names}:
            names.append(name)
    return names


# --- Analytics --------------------------------------------------------------------------

@router.get("/analytics")
async def analytics(
    period: Period = Query(default="today"),
    access: AppAccess = Depends(app_access),
    db: SupabaseService = Depends(get_db),
) -> dict[str, Any]:
    return await store_analytics(db, access.store.id, period,
                                 ai_limit(access.store, get_settings().ai_daily_calls_per_store))


# --- Products and stock -----------------------------------------------------------------

@router.get("/products")
async def list_products(
    search: str | None = Query(default=None, max_length=60),
    category: str | None = Query(default=None, max_length=40),
    access: AppAccess = Depends(app_access),
    db: SupabaseService = Depends(get_db),
) -> dict[str, Any]:
    products = [summarize(p) for p in await db.list_products_with_variants(access.store.id)]
    categories = sorted({p["category"] for p in products if p["category"]})
    if category:
        products = [p for p in products if p["category"] == category.strip().lower()]
    if search and search.strip():
        words = search.lower().split()
        products = [p for p in products if all(
            w in " ".join(str(p[k] or "") for k in ("name", "brand", "code", "search_keywords")).lower()
            for w in words)]
    return {"products": products, "categories": categories}


@router.get("/products/{product_id}")
async def get_product(product_id: UUID, access: AppAccess = Depends(app_access),
                      db: SupabaseService = Depends(get_db)) -> dict[str, Any]:
    try:
        return summarize(await Inventory(db).product(access.store.id, product_id))
    except InventoryError as error:
        raise _refused(error)


@router.post("/products", status_code=201)
async def create_product(body: NewProduct, access: AppAccess = Depends(app_access),
                         db: SupabaseService = Depends(get_db)) -> dict[str, Any]:
    """Saving a product of an active store with a linked channel posts it
    there automatically (Phase 8d), with its variants (they're saved within
    the next moment, before the post is made). Staff add products without
    prices; the owner sets them."""
    fields = body.product.model_dump(exclude_none=True)
    if not fields.get("name"):
        raise HTTPException(status_code=422, detail="the product needs a name")
    if not access.is_owner and ("base_price" in fields or any(v.price is not None for v in body.variants)):
        raise HTTPException(status_code=403, detail="only the owner can change prices")
    inventory = Inventory(db)
    product_id = await db.create_product(access.store.id, fields)
    try:
        await inventory.save_grid(access.store.id, product_id, [v.row() for v in body.variants])
    except InventoryError as error:
        raise _refused(error)
    return summarize(await inventory.product(access.store.id, product_id))


@router.patch("/products/{product_id}")
async def update_product(product_id: UUID, body: ProductFields, access: AppAccess = Depends(app_access),
                         db: SupabaseService = Depends(get_db)) -> dict[str, Any]:
    """Owners change anything; staff change the details but not the price."""
    inventory = Inventory(db)
    try:
        current = await inventory.product(access.store.id, product_id)
        changes = body.model_dump(exclude_unset=True)
        if "name" in changes and not changes["name"]:
            raise HTTPException(status_code=422, detail="the product needs a name")
        if not access.is_owner and "base_price" in changes and \
                _price(changes["base_price"]) != _price(current.get("base_price")):
            raise HTTPException(status_code=403, detail="only the owner can change prices")
        await db.update_product(access.store.id, product_id, changes)
        return summarize(await inventory.product(access.store.id, product_id))
    except InventoryError as error:
        raise _refused(error)


@router.put("/products/{product_id}/variants")
async def save_variants(product_id: UUID, body: GridIn, access: AppAccess = Depends(app_access),
                        db: SupabaseService = Depends(get_db)) -> dict[str, Any]:
    """Save the color × size grid: rows are matched to existing variants (by
    id, else color + size), updated or added; `remove` deletes variants that
    were never ordered and takes the others off sale (stock 0). Staff may
    change stock and sizes, not prices (403)."""
    inventory = Inventory(db)
    try:
        result = await inventory.save_grid(access.store.id, product_id,
                                           [v.row() for v in body.variants], body.remove,
                                           prices_locked=not access.is_owner)
        product = summarize(await inventory.product(access.store.id, product_id))
    except InventoryError as error:
        raise _refused(error)
    note = ""
    if result.kept_off_sale:
        note = (f"{', '.join(result.kept_off_sale)}: ordered before, so kept with stock 0 "
                "instead of deleted.")
    return {"product": product, "added": result.added, "updated": result.updated,
            "removed": result.removed, "note": note}


@router.post("/variants/{variant_id}/stock")
async def change_stock(variant_id: UUID, body: StockChange, access: AppAccess = Depends(app_access),
                       db: SupabaseService = Depends(get_db)) -> dict[str, Any]:
    """Staff and owners: add/remove stock (`change`) or set the counted
    number (`set`). Stock never goes below 0."""
    try:
        stock = await Inventory(db).change_stock(access.store.id, variant_id,
                                                 change=body.change, set_to=body.set)
    except InventoryError as error:
        raise _refused(error)
    return {"variant_id": variant_id, "stock": stock}


@router.post("/products/{product_id}/off-sale")
async def take_off_sale(product_id: UUID, access: AppAccess = Depends(owner_access),
                        db: SupabaseService = Depends(get_db)) -> dict[str, Any]:
    try:
        changed = await Inventory(db).take_off_sale(access.store.id, product_id)
    except InventoryError as error:
        raise _refused(error)
    return {"ok": True, "variants": changed}


@router.delete("/products/{product_id}")
async def delete_product(product_id: UUID, access: AppAccess = Depends(owner_access),
                         db: SupabaseService = Depends(get_db)) -> dict[str, bool]:
    try:
        await Inventory(db).delete(access.store.id, product_id)
    except InventoryError as error:
        raise _refused(error)
    return {"ok": True}


@router.post("/products/{product_id}/publish")
async def publish(product_id: UUID, access: AppAccess = Depends(owner_access),
                  catalog: Catalog = Depends(get_catalog)) -> dict[str, Any]:
    """Post the product to the store's channel (or update its post)."""
    if access.store.status != "active":
        raise HTTPException(status_code=409, detail="the store isn't approved yet")
    result = await catalog.publish(access.store, product_id)
    if not result.ok:
        raise HTTPException(status_code=409, detail=result.message)
    return {"ok": True, "message": result.message}


@router.post("/photos", status_code=201)
async def upload_photo(file: UploadFile = File(...), access: AppAccess = Depends(app_access),
                       db: SupabaseService = Depends(get_db)) -> dict[str, str]:
    """A product photo (JPEG, PNG or WebP, up to 5 MB) -> its public link,
    to put in the product's photo_url."""
    extension = PHOTO_TYPES.get((file.content_type or "").lower())
    if extension is None:
        raise HTTPException(status_code=415, detail="send a JPEG, PNG or WebP photo")
    data = await file.read(MAX_PHOTO_BYTES + 1)
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(status_code=413, detail="the photo is larger than 5 MB")
    if not data:
        raise HTTPException(status_code=422, detail="the photo is empty")
    url = await db.upload_photo(access.store.id, f"{secrets.token_hex(12)}.{extension}", data,
                                file.content_type)
    return {"photo_url": url}


# --- Orders (view only, D46: confirming stays in the staff group) ------------------------

def _order(row: dict[str, Any]) -> dict[str, Any]:
    items = []
    for item in row.get("order_items") or []:
        variant = item.get("product_variants") or {}
        product = variant.get("products") or {}
        items.append({"name": product.get("name"), "code": product.get("code"),
                      "color": variant.get("color"), "size": variant.get("size"),
                      "quantity": item["quantity"], "price": item["price"],
                      "list_price": item.get("list_price")})
    address = row.get("delivery_address")
    return {
        "id": row["id"], "number": order_number(UUID(row["id"])),
        "status": row["status"], "payment_status": row["payment_status"],
        "total": row["total_price"], "currency": row.get("currency") or "ETB",
        "fulfillment": row.get("fulfillment_method"),
        "customer": {"name": row.get("contact_name"), "phone": row.get("contact_phone")},
        "delivery_address": None if address == ADDRESS_TO_ARRANGE else address,
        "created_at": row["created_at"], "items": items,
        # Phase 12: where it was sold; for counter sales who sold it and how it was paid.
        "channel": row.get("channel") or "telegram",
        "payment_method": row.get("payment_method"), "payment_note": row.get("payment_note"),
        "sold_by": row.get("sold_by_name"), "note": row.get("note"),
    }


@router.get("/orders")
async def list_orders(
    status: Literal["all", "unpaid", "paid"] = Query(default="all"),
    channel: Literal["all", "telegram", "in_shop"] = Query(default="all"),
    before: datetime | None = Query(default=None),
    limit: int = Query(default=30, ge=1, le=100),
    access: AppAccess = Depends(app_access),
    db: SupabaseService = Depends(get_db),
) -> dict[str, Any]:
    """Newest first. For more, pass the last order's created_at as `before`.
    `channel`: Telegram orders or sales in the shop (Phase 12)."""
    rows = await db.list_orders(access.store.id, None if status == "all" else status, limit, before,
                                None if channel == "all" else channel)
    orders = [_order(r) for r in rows]
    return {"orders": orders, "more": len(orders) == limit}


class ExportIn(BaseModel):
    month: str = Field(pattern=r"^\d{4}-\d{2}$")  # "2026-09"


@router.post("/orders/export")
async def export_orders(body: ExportIn, access: AppAccess = Depends(owner_access),
                        db: SupabaseService = Depends(get_db),
                        orchestrator: Orchestrator = Depends(get_orchestrator)) -> dict[str, Any]:
    """Owners (D73): a month's sales and orders as an Excel file (Phase 15,
    D72), sent by the shop's bot to the owner's private chat: files opened
    inside the Mini App often don't download. 409 if the bot can't write to
    them (they never pressed Start in it)."""
    store = access.store
    try:
        start, end = month_bounds(body.month)
    except ExportError as error:
        raise HTTPException(status_code=422, detail=error.message)
    export = build_export(store, await db.orders_for_export(store.id, start, end), start)
    try:
        await orchestrator.telegram.send_document(
            store.telegram_bot_token.get_secret_value(), access.user.id, export.filename, export.data,
            caption=f"📊 {store.name}: sales and orders, {start:%B %Y}", content_type=XLSX)
    except TelegramError as error:
        logger.info("export not delivered", extra={"error": error.description})
        bot = f"@{store.telegram_bot_username}" if store.telegram_bot_username else "the shop's bot"
        raise HTTPException(status_code=409, detail=f"The bot can't send you the file yet. Open {bot}, "
                                                    "press Start, then try again.")
    logger.info("export sent", extra={"orders": export.orders, "month": body.month})
    return {"sent": True, "file": export.filename, "orders": export.orders, "revenue": export.revenue}


# --- Counter sales (Phase 12, D53–D57) ----------------------------------------------------

class CounterLineIn(BaseModel):
    variant_id: UUID
    quantity: int = Field(ge=1, le=1000)
    price: Decimal = Field(ge=0, le=10_000_000)  # the price agreed at the counter, per item


class CounterSaleIn(BaseModel):
    items: list[CounterLineIn] = Field(min_length=1, max_length=50)
    payment_method: str = Field(min_length=1, max_length=60)  # "Cash", "Telebirr", "Other"...
    payment_note: str | None = Field(default=None, max_length=300)
    customer_name: str | None = Field(default=None, max_length=80)
    customer_phone: str | None = Field(default=None, max_length=30)
    note: str | None = Field(default=None, max_length=500)
    allow_held: bool = False  # sell even what an online order is holding (D55)
    request_id: UUID  # made by the app per sale: pressing Confirm twice saves once

    @field_validator("payment_method", "payment_note", "customer_name", "customer_phone", "note")
    @classmethod
    def _text(cls, value: str | None) -> str | None:
        return (value or "").strip() or None


def get_counter(db: SupabaseService = Depends(get_db),
                orchestrator: Orchestrator = Depends(get_orchestrator)) -> CounterSales:
    return CounterSales(db, orchestrator.telegram)


@router.get("/variants/{variant_id}/availability")
async def availability(variant_id: UUID, access: AppAccess = Depends(app_access),
                       counter: CounterSales = Depends(get_counter)) -> dict[str, Any]:
    """Before a counter sale: stock, and online orders holding it (D55)."""
    try:
        return await counter.availability(access.store, variant_id)
    except CounterSaleError as error:
        raise HTTPException(status_code=error.status_code, detail=error.message)


@router.post("/counter-sales", status_code=201)
async def counter_sale(body: CounterSaleIn, access: AppAccess = Depends(app_access),
                       counter: CounterSales = Depends(get_counter)) -> dict[str, Any]:
    """A sale in the shop: staff and owners (staff within the discount limit,
    checked by the database). Errors: 403 discount too large, 409 not enough
    stock / held by an online order (send again with allow_held: true)."""
    if body.payment_method is None:
        raise HTTPException(status_code=422, detail="choose how the customer paid")
    sale = SaleRequest(
        lines=[SaleLine(i.variant_id, i.quantity, i.price) for i in body.items],
        payment_method=body.payment_method, request_id=str(body.request_id),
        payment_note=body.payment_note, customer_name=body.customer_name,
        customer_phone=body.customer_phone, note=body.note, allow_held=body.allow_held,
    )
    try:
        result = await counter.sell(access, sale)
    except CounterSaleError as error:
        raise HTTPException(status_code=error.status_code, detail=error.message,
                            headers={"X-Error-Code": error.code})
    return {"order_id": result.order_id, "number": result.number, "total": result.total,
            "list_total": result.list_total, "discount": result.discount,
            "already_saved": result.already_saved, "held_orders": result.held_orders}


# --- Store settings (owners) ------------------------------------------------------------

def _settings(store: Store) -> dict[str, Any]:
    """The lists for the screen, plus the texts customers get (for a preview,
    and for older stores whose profile is still text only)."""
    return {
        "payment_accounts": store.payment_accounts, "delivery_areas": store.delivery_areas,
        "opening_week": store.opening_week, "staff_discount_percent": store.staff_discount_percent,
        **{name: getattr(store, name) for name in PROFILE_FIELDS},
        # Phase 13: the type, the owner's renames, and every type's own words.
        "shop_type": shop_type_of(store), "option_labels": store.option_labels,
        "shop_types": shop_types_json(),
        "daily_summary": store.daily_summary,  # Phase 15
    }


@router.get("/settings")
async def get_store_settings(access: AppAccess = Depends(owner_access)) -> dict[str, Any]:
    return _settings(access.store)


@router.put("/settings")
async def save_store_settings(body: SettingsIn, access: AppAccess = Depends(owner_access),
                              db: SupabaseService = Depends(get_db),
                              orchestrator: Orchestrator = Depends(get_orchestrator)) -> dict[str, Any]:
    sent = body.model_fields_set
    changes: dict[str, Any] = {}
    if "shop_type" in sent and body.shop_type is not None:
        changes["shop_type"] = body.shop_type
    if "option_labels" in sent:
        changes["option_labels"] = clean_labels(body.option_labels)
    for name in ("location", "pickup_instructions", "return_policy"):
        if name in sent:
            changes[name] = (getattr(body, name) or "").strip() or None
    if "daily_summary" in sent and body.daily_summary is not None:
        changes["daily_summary"] = body.daily_summary
    if "staff_discount_percent" in sent and body.staff_discount_percent is not None:
        changes["staff_discount_percent"] = body.staff_discount_percent
    if "payment_accounts" in sent:
        accounts = body.payment_accounts or []
        changes["payment_accounts"] = [a.model_dump(exclude_none=True) for a in accounts]
        changes["payment_instructions"] = payment_text(accounts)
    if "delivery_areas" in sent:
        areas = body.delivery_areas or []
        changes["delivery_areas"] = [{"area": a.area, "fee": float(a.fee)} for a in areas]
        changes["delivery_info"] = delivery_text(areas)
    if "opening_week" in sent:
        changes["opening_week"] = body.opening_week.as_json() if body.opening_week else None
        changes["opening_hours"] = hours_text(body.opening_week)
    await db.update_store_profile(access.store.id, changes)
    store = await db.get_store_any_status(access.store.id)
    if "shop_type" in changes or "option_labels" in changes:
        # The bot's description in Telegram names the options too (Phase 13).
        await refresh_bot_profile(orchestrator.telegram, store)
    return _settings(store)


async def refresh_bot_profile(telegram, store: Store) -> None:
    """Best effort: a failure here mustn't undo the saved settings."""
    if store.telegram_bot_token is None:
        return
    try:
        await telegram.set_profile(store.telegram_bot_token.get_secret_value(),
                                   *bot_profile(store.name, store))
    except TelegramError as error:
        logger.warning("bot description not updated", extra={"error": error.description})


@router.get("/connections")
async def connections(access: AppAccess = Depends(owner_access),
                      orchestrator: Orchestrator = Depends(get_orchestrator)) -> dict[str, Any]:
    """The linked staff group and channel with their names (the Connect
    screen asks again every few seconds while a /link code is waiting)."""
    store, token = access.store, access.store.telegram_bot_token.get_secret_value()

    async def chat(chat_id: int | None) -> dict[str, Any] | None:
        if chat_id is None:
            return None
        try:
            title = await orchestrator.telegram.get_chat_title(token, chat_id)
        except TelegramError:
            title = None
        return {"id": chat_id, "title": title, "bot_can_see": title is not None}

    return {"staff_group": await chat(store.staff_chat_id), "channel": await chat(store.channel_id)}


@router.post("/link-code")
async def link_code(access: AppAccess = Depends(owner_access),
                    onboarding: Onboarding = Depends(get_onboarding)) -> dict[str, Any]:
    code, expires_at = await onboarding.new_link_code(access.store)
    return {"code": code, "expires_at": expires_at, "command": f"{LINK_COMMAND} {code}",
            "minutes": LINK_CODE_MINUTES}


@router.put("/bot-token")
async def change_bot_token(body: BotTokenIn, access: AppAccess = Depends(owner_access),
                           onboarding: Onboarding = Depends(get_onboarding)) -> dict[str, Any]:
    try:
        result = await onboarding.change_bot_token(access.store, body.bot_token)
    except OnboardingError as error:
        raise _refused(error)
    return {"bot_username": result.store.telegram_bot_username, "bot_connected": result.connected,
            "note": result.note}
