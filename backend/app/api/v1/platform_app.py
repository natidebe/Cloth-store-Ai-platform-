"""The platform bot's Mini App: shops sign up, platform admins manage stores
(Phase 10b, D44–D45).

The platform bot is mine (PLATFORM_BOT_TOKEN in .env). Its Mini App sends the
user's signed initData in the X-Telegram-Init-Data header, checked with the
platform bot's token:

    anyone           GET  /api/v1/platform-app/me              who am I, my stores, am I an admin
                     POST /api/v1/platform-app/stores          create a store (pending, D14)
    platform admins  GET  /api/v1/platform-app/admin/stores
                     POST /api/v1/platform-app/admin/stores/{store}/approve
                     POST /api/v1/platform-app/admin/stores/{store}/suspend
                     PUT  /api/v1/platform-app/admin/stores/{store}/plan

The platform bot's own messages arrive at POST /api/v1/platform-bot/webhook:
it answers with a button that opens the Mini App.
"""
import hashlib
import hmac
import logging
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from app.agents.miniapp import dashboard_url
from app.agents.shop_types import DEFAULT_TYPE, ShopType, shop_types_json
from app.agents.subscriptions import PaidPlan, Subscriptions
from app.agents.onboarding import Onboarding, OnboardingError
from app.agents.orchestrator import Orchestrator
from app.api.v1.miniapp import get_onboarding
from app.api.v1.webhook import SECRET_HEADER, get_db, get_orchestrator
from app.core.config import get_settings
from app.core.telegram_auth import INIT_DATA_HEADER, MiniAppUser, check_init_data
from app.models.schemas import StorePlan, StoreSummary, TelegramUpdate
from app.services.supabase_service import SupabaseService
from app.services.telegram_service import WEB_APP_PREFIX, TelegramError, TelegramService

logger = logging.getLogger(__name__)
router = APIRouter(tags=["platform app"])


def platform_bot_token() -> str:
    token = get_settings().platform_bot_token.get_secret_value()
    if not token:
        raise HTTPException(status_code=503, detail="the platform bot isn't set up (PLATFORM_BOT_TOKEN)")
    return token


def platform_webhook_secret(token: str) -> str:
    """The secret Telegram sends with the platform bot's updates (derived from
    its token, so there's nothing else to configure)."""
    return hmac.new(token.encode(), b"platform-bot-webhook", hashlib.sha256).hexdigest()[:48]


def platform_app_url() -> str:
    return f"{get_settings().public_base_url.strip().rstrip('/')}/app/platform"


async def platform_user(init_data: str | None = Header(default=None, alias=INIT_DATA_HEADER)) -> MiniAppUser:
    user = check_init_data(init_data, platform_bot_token())
    if user is None:
        raise HTTPException(status_code=401, detail="open this from the platform bot in Telegram")
    return user


async def platform_admin(user: MiniAppUser = Depends(platform_user),
                         db: SupabaseService = Depends(get_db)) -> MiniAppUser:
    if not await db.is_platform_admin_telegram(user.id):
        raise HTTPException(status_code=403, detail="platform admins only")
    return user


class NewStore(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    bot_token: str = Field(min_length=20, max_length=100)
    shop_type: ShopType = DEFAULT_TYPE  # Phase 13: asked first, "What kind of shop?"


class PlanIn(BaseModel):
    plan: StorePlan


def _store_card(store) -> dict[str, Any]:
    return {"id": store.id, "name": store.name, "status": store.status, "plan": store.plan,
            "bot_username": store.telegram_bot_username,
            "staff_group_linked": store.staff_chat_id is not None,
            "channel_linked": store.channel_id is not None,
            "dashboard_url": dashboard_url(get_settings().public_base_url, store)}


# --- Anyone: my stores, sign-up ------------------------------------------------------

@router.get("/platform-app/me")
async def me(user: MiniAppUser = Depends(platform_user),
             db: SupabaseService = Depends(get_db)) -> dict[str, Any]:
    support = get_settings().support_username.strip().lstrip("@")
    return {
        "user": {"id": user.id, "name": user.full_name, "username": user.username,
                 "language_code": user.language_code},
        "is_platform_admin": await db.is_platform_admin_telegram(user.id),
        "stores": [_store_card(s) for s in await db.stores_created_by(user.id)],
        "support_url": f"https://t.me/{support}" if support else None,
        "shop_types": shop_types_json(),  # Phase 13: for "What kind of shop?"
    }


@router.post("/platform-app/stores", status_code=201)
async def create_store(body: NewStore, user: MiniAppUser = Depends(platform_user),
                       onboarding: Onboarding = Depends(get_onboarding)) -> dict[str, Any]:
    """Check the bot token with Telegram, save the store as pending (D14) with
    this Telegram account as its owner, and connect its bot."""
    try:
        result = await onboarding.create_store_for_telegram(user.id, body.name, body.bot_token,
                                                            body.shop_type)
    except OnboardingError as error:
        raise HTTPException(status_code=error.status_code, detail=error.message)
    return {**_store_card(result.store), "bot_connected": result.connected,
            "note": result.note or "Waiting for approval. Meanwhile: add your bot to a staff group "
                                   "and your channel, and send the /link codes from the dashboard."}


# --- Platform admins ---------------------------------------------------------------------

@router.get("/platform-app/admin/stores", response_model=list[StoreSummary])
async def admin_stores(_: MiniAppUser = Depends(platform_admin),
                       db: SupabaseService = Depends(get_db)) -> list[StoreSummary]:
    return await db.list_all_stores()


async def _set_status(store_id: UUID, status: str, db: SupabaseService, onboarding: Onboarding,
                      background: BackgroundTasks) -> dict:
    store = await db.get_store_any_status(store_id)
    if store is None:
        raise HTTPException(status_code=404, detail="store not found")
    changed = store.status != status
    await onboarding.set_status(store, status)
    if changed and store.owner_telegram_id is not None:
        # The creator signed up in the platform bot: tell them there.
        background.add_task(_tell_creator, onboarding.telegram, store.owner_telegram_id, store.name, status)
    return {"store_id": store.id, "status": status, "plan": store.plan}


STATUS_MESSAGES = {
    "active": "✅ {name} is approved! Customers can now order from your bot.\n"
              "✅ {name} ጸድቋል! ደንበኞች አሁን ከቦትዎ ማዘዝ ይችላሉ።",
    "suspended": "⛔ {name} is suspended: the bot doesn't take orders for now. Contact support.\n"
                 "⛔ {name} ታግዷል፤ ቦቱ ለጊዜው ትዕዛዝ አይቀበልም። ድጋፍ ያግኙ።",
}


async def _tell_creator(telegram: TelegramService, chat_id: int, name: str, status: str) -> None:
    text = STATUS_MESSAGES.get(status)
    token = get_settings().platform_bot_token.get_secret_value()
    if not text or not token:
        return
    try:
        await telegram.send_message(token, chat_id, text.format(name=name))
    except TelegramError as error:  # e.g. they never started the platform bot
        logger.warning("store owner not told", extra={"error": error.description})


@router.post("/platform-app/admin/stores/{store_id}/approve")
async def approve(store_id: UUID, background: BackgroundTasks, _: MiniAppUser = Depends(platform_admin),
                  db: SupabaseService = Depends(get_db),
                  onboarding: Onboarding = Depends(get_onboarding)) -> dict[str, Any]:
    """Approve a pending store, or reactivate a suspended one."""
    return await _set_status(store_id, "active", db, onboarding, background)


@router.post("/platform-app/admin/stores/{store_id}/suspend")
async def suspend(store_id: UUID, background: BackgroundTasks, _: MiniAppUser = Depends(platform_admin),
                  db: SupabaseService = Depends(get_db),
                  onboarding: Onboarding = Depends(get_onboarding)) -> dict[str, Any]:
    return await _set_status(store_id, "suspended", db, onboarding, background)


class PaymentIn(BaseModel):
    """A subscription payment the platform admin received (D65)."""
    plan: PaidPlan
    amount: Decimal = Field(ge=0, le=10_000_000)
    method: str | None = Field(default=None, max_length=60)
    reference: str | None = Field(default=None, max_length=120)
    months: int = Field(default=3, ge=1, le=24)


def get_subscriptions(db: SupabaseService = Depends(get_db),
                      orchestrator: Orchestrator = Depends(get_orchestrator)) -> Subscriptions:
    settings = get_settings()
    return Subscriptions(db, orchestrator.telegram,
                         platform_token=settings.platform_bot_token.get_secret_value(),
                         payment_info=settings.platform_payment_info, support=settings.support_username)


@router.post("/platform-app/admin/stores/{store_id}/payments", status_code=201)
async def record_payment(store_id: UUID, body: PaymentIn, user: MiniAppUser = Depends(platform_admin),
                         db: SupabaseService = Depends(get_db),
                         subscriptions: Subscriptions = Depends(get_subscriptions)) -> dict[str, Any]:
    """D65: 3 months more from the current end (or today), an unpaid pause
    turned back on, and the shop thanked."""
    store = await db.get_store_any_status(store_id)
    if store is None:
        raise HTTPException(status_code=404, detail="store not found")
    if store.status == "pending":
        raise HTTPException(status_code=409, detail="Approve the store first.")
    return await subscriptions.record_payment(store, body.plan, body.amount, body.method, body.reference,
                                              user.id, body.months)


@router.get("/platform-app/admin/stores/{store_id}/payments")
async def list_payments(store_id: UUID, _: MiniAppUser = Depends(platform_admin),
                        db: SupabaseService = Depends(get_db)) -> list[dict[str, Any]]:
    return await db.subscription_payments(store_id)


@router.put("/platform-app/admin/stores/{store_id}/plan")
async def change_plan(store_id: UUID, body: PlanIn, _: MiniAppUser = Depends(platform_admin),
                      db: SupabaseService = Depends(get_db)) -> dict[str, Any]:
    store = await db.get_store_any_status(store_id)
    if store is None:
        raise HTTPException(status_code=404, detail="store not found")
    await db.set_store_plan(store.id, body.plan)
    return {"store_id": store.id, "status": store.status, "plan": body.plan}


# --- The platform bot's chat -------------------------------------------------------------

@router.post("/platform-bot/webhook")
async def platform_bot_webhook(
    request: Request,
    background: BackgroundTasks,
    secret: str | None = Header(default=None, alias=SECRET_HEADER),
    orchestrator: Orchestrator = Depends(get_orchestrator),
) -> dict[str, bool]:
    """Any private message to the platform bot gets the Mini App button."""
    token = platform_bot_token()
    if not secret or not hmac.compare_digest(secret, platform_webhook_secret(token)):
        raise HTTPException(status_code=401, detail="invalid secret")
    try:
        update = TelegramUpdate.model_validate(await request.json())
    except (ValueError, ValidationError):
        return {"ok": True}
    message = update.message
    if message is not None and message.chat.type == "private" and message.from_user \
            and not message.from_user.is_bot:
        background.add_task(_send_app_button, orchestrator.telegram, token, message.chat.id)
    return {"ok": True}


async def _send_app_button(telegram: TelegramService, token: str, chat_id: int) -> None:
    url = platform_app_url()
    if not url.startswith("https://"):
        logger.error("platform app needs PUBLIC_BASE_URL (https)")
        return
    try:
        await telegram.send_message(
            token, chat_id,
            "👋 Open your shop on Telegram: create your store, or manage the ones you have.\n"
            "👋 ሱቅዎን በቴሌግራም ይክፈቱ፤ አዲስ ሱቅ ይፍጠሩ ወይም ያለዎትን ያስተዳድሩ።",
            buttons=[("🛍 Open / ክፈት", f"{WEB_APP_PREFIX}{url}")])
    except TelegramError as error:
        logger.warning("platform app button not sent", extra={"error": error.description})
