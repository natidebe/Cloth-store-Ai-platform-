"""The store's Mini App: analytics, products & stock, settings (Phase 10b, D42–D43).

Every request carries the Telegram user's signed initData in the
X-Telegram-Init-Data header (Telegram.WebApp.initData). It must be signed by
THIS store's bot, and the user must be in the store's staff group (or have
created the store). Members are "staff", the group's admins "owners":

    everyone   GET  /api/v1/app/stores/{store}/me
               GET  /api/v1/app/stores/{store}/analytics?period=today|7d|30d
               GET  /api/v1/app/stores/{store}/products[?search=&category=]
               GET  /api/v1/app/stores/{store}/products/{product}
               POST /api/v1/app/stores/{store}/variants/{variant}/stock   {"change": 5} or {"set": 12}
    owners     POST /api/v1/app/stores/{store}/products                   a product + its color × size grid
               PATCH /api/v1/app/stores/{store}/products/{product}        name, price, photo, ...
               PUT  /api/v1/app/stores/{store}/products/{product}/variants   save the grid
               POST /api/v1/app/stores/{store}/products/{product}/off-sale
               DELETE /api/v1/app/stores/{store}/products/{product}       never-ordered products only
               POST /api/v1/app/stores/{store}/products/{product}/publish to the channel
               POST /api/v1/app/stores/{store}/photos                     upload a photo -> its link
               GET/PUT /api/v1/app/stores/{store}/settings                payment accounts, delivery, ...
               POST /api/v1/app/stores/{store}/link-code                  /link code for the group/channel
               PUT  /api/v1/app/stores/{store}/bot-token                  change the bot (D17)
"""
import secrets
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field, field_validator

from app.agents.analytics import Period, store_analytics
from app.agents.catalog import Catalog
from app.agents.inventory import GridRow, Inventory, InventoryError, summarize
from app.agents.miniapp import AppAccess
from app.agents.onboarding import LINK_CODE_MINUTES, LINK_COMMAND, Onboarding, OnboardingError
from app.agents.orchestrator import Orchestrator
from app.api.v1.catalog import get_catalog
from app.api.v1.webhook import get_db, get_orchestrator
from app.core.config import get_settings
from app.core.telegram_auth import INIT_DATA_HEADER, check_init_data
from app.models.schemas import PROFILE_FIELDS
from app.services.supabase_service import SupabaseService

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
    """The store's profile texts (sent to customers). Empty text clears one."""
    opening_hours: str | None = Field(default=None, max_length=1000)
    location: str | None = Field(default=None, max_length=1000)
    delivery_info: str | None = Field(default=None, max_length=1000)
    pickup_instructions: str | None = Field(default=None, max_length=1000)
    payment_instructions: str | None = Field(default=None, max_length=1000)
    return_policy: str | None = Field(default=None, max_length=1000)


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
                  "channel_linked": store.channel_id is not None},
    }


# --- Analytics --------------------------------------------------------------------------

@router.get("/analytics")
async def analytics(
    period: Period = Query(default="today"),
    access: AppAccess = Depends(app_access),
    db: SupabaseService = Depends(get_db),
) -> dict[str, Any]:
    return await store_analytics(db, access.store.id, period, get_settings().ai_daily_calls_per_store)


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
async def create_product(body: NewProduct, access: AppAccess = Depends(owner_access),
                         db: SupabaseService = Depends(get_db)) -> dict[str, Any]:
    """Saving a product of an active store with a linked channel posts it
    there automatically (Phase 8d), with its variants (they're saved within
    the next moment, before the post is made)."""
    fields = body.product.model_dump(exclude_none=True)
    if not fields.get("name"):
        raise HTTPException(status_code=422, detail="the product needs a name")
    inventory = Inventory(db)
    product_id = await db.create_product(access.store.id, fields)
    try:
        await inventory.save_grid(access.store.id, product_id, [v.row() for v in body.variants])
    except InventoryError as error:
        raise _refused(error)
    return summarize(await inventory.product(access.store.id, product_id))


@router.patch("/products/{product_id}")
async def update_product(product_id: UUID, body: ProductFields, access: AppAccess = Depends(owner_access),
                         db: SupabaseService = Depends(get_db)) -> dict[str, Any]:
    inventory = Inventory(db)
    try:
        await inventory.product(access.store.id, product_id)
        changes = body.model_dump(exclude_unset=True)
        if "name" in changes and not changes["name"]:
            raise HTTPException(status_code=422, detail="the product needs a name")
        await db.update_product(access.store.id, product_id, changes)
        return summarize(await inventory.product(access.store.id, product_id))
    except InventoryError as error:
        raise _refused(error)


@router.put("/products/{product_id}/variants")
async def save_variants(product_id: UUID, body: GridIn, access: AppAccess = Depends(owner_access),
                        db: SupabaseService = Depends(get_db)) -> dict[str, Any]:
    """Save the color × size grid: rows are matched to existing variants (by
    id, else color + size), updated or added; `remove` deletes variants that
    were never ordered and takes the others off sale (stock 0)."""
    inventory = Inventory(db)
    try:
        result = await inventory.save_grid(access.store.id, product_id,
                                           [v.row() for v in body.variants], body.remove)
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
async def upload_photo(file: UploadFile = File(...), access: AppAccess = Depends(owner_access),
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


# --- Store settings (owners) ------------------------------------------------------------

@router.get("/settings")
async def get_store_settings(access: AppAccess = Depends(owner_access)) -> dict[str, Any]:
    return {name: getattr(access.store, name) for name in PROFILE_FIELDS}


@router.put("/settings")
async def save_store_settings(body: SettingsIn, access: AppAccess = Depends(owner_access),
                              db: SupabaseService = Depends(get_db)) -> dict[str, Any]:
    changes = {k: ((v or "").strip() or None) for k, v in body.model_dump(exclude_unset=True).items()}
    await db.update_store_profile(access.store.id, changes)
    store = await db.get_store_any_status(access.store.id)
    return {name: getattr(store, name) for name in PROFILE_FIELDS}


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
