"""Store onboarding for the dashboard (Phase 9b, D14–D17).

Every endpoint needs the user's Supabase login token
(`Authorization: Bearer <token>`). Creating a store and accepting invitations
only need a login; the rest need the user to be the store's OWNER.

    POST   /api/v1/stores                          create a store (you become its owner)
    GET    /api/v1/me/stores                       the stores you belong to, with your role
    POST   /api/v1/me/accept-invites               join the stores that invited your email
    POST   /api/v1/stores/{store}/link-code        a code for /link in the staff group or channel
    POST   /api/v1/stores/{store}/invites          invite staff by email
    DELETE /api/v1/stores/{store}/staff/{user}     remove a staff member
    PUT    /api/v1/stores/{store}/bot-token        change the bot (D17)
"""
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, EmailStr, Field

from app.agents.onboarding import LINK_COMMAND, BotConnection, Onboarding, OnboardingError
from app.agents.orchestrator import Orchestrator
from app.api.v1.webhook import get_db, get_orchestrator
from app.core.config import get_settings
from app.core.security import bearer_token
from app.models.schemas import AuthUser, Store, StoreMembership
from app.services.supabase_service import SupabaseService

router = APIRouter(tags=["stores"])


# --- Who is calling ----------------------------------------------------------------

async def current_user(
    authorization: str | None = Header(default=None),
    db: SupabaseService = Depends(get_db),
) -> AuthUser:
    """The logged-in user. 401 without a valid login."""
    token = bearer_token(authorization)
    user = await db.get_user(token) if token else None
    if user is None:
        raise HTTPException(status_code=401, detail="login required (Authorization: Bearer <token>)")
    return user


async def require_owner(
    store_id: UUID,
    user: AuthUser = Depends(current_user),
    db: SupabaseService = Depends(get_db),
) -> Store:
    """The store, if the user is its owner (any status). 403 otherwise."""
    if await db.staff_role(store_id, user.id) != "owner":
        raise HTTPException(status_code=403, detail="only the store's owner can do this")
    store = await db.get_store_any_status(store_id)
    if store is None:
        raise HTTPException(status_code=404, detail="store not found")
    return store


def get_onboarding(
    db: SupabaseService = Depends(get_db),
    orchestrator: Orchestrator = Depends(get_orchestrator),
) -> Onboarding:
    return Onboarding(db, orchestrator.telegram, get_settings().public_base_url)


def _refused(error: OnboardingError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail=error.message)


# --- Requests and responses ------------------------------------------------------------

class CreateStoreRequest(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    bot_token: str = Field(min_length=20, max_length=100)


class BotTokenRequest(BaseModel):
    bot_token: str = Field(min_length=20, max_length=100)


class StoreResponse(BaseModel):
    store_id: UUID
    name: str
    status: str
    plan: str | None
    bot_username: str | None
    bot_connected: bool
    note: str = ""


class LinkCodeResponse(BaseModel):
    code: str
    expires_at: datetime
    instructions: str


class InviteRequest(BaseModel):
    email: EmailStr


class InviteResponse(BaseModel):
    ok: bool
    email_sent: bool
    message: str


class AcceptInvitesResponse(BaseModel):
    joined: int


def _store_response(result: BotConnection) -> StoreResponse:
    store = result.store
    return StoreResponse(store_id=store.id, name=store.name, status=store.status, plan=store.plan,
                         bot_username=store.telegram_bot_username, bot_connected=result.connected,
                         note=result.note)


# --- Endpoints ---------------------------------------------------------------------------

@router.post("/stores", response_model=StoreResponse, status_code=201)
async def create_store(
    body: CreateStoreRequest,
    user: AuthUser = Depends(current_user),
    onboarding: Onboarding = Depends(get_onboarding),
) -> StoreResponse:
    """Check the bot token with Telegram, save the store as 'pending' (D14)
    with you as its owner, and connect the bot. 409 if another store uses the bot."""
    try:
        result = await onboarding.create_store(user, body.name, body.bot_token)
    except OnboardingError as error:
        raise _refused(error)
    if result.store.status == "pending" and not result.note:
        result.note = "Waiting for approval. Meanwhile, link your staff group and channel and add products."
    return _store_response(result)


@router.get("/me/stores", response_model=list[StoreMembership])
async def my_stores(
    user: AuthUser = Depends(current_user),
    db: SupabaseService = Depends(get_db),
) -> list[StoreMembership]:
    return await db.list_user_stores(user.id)


@router.post("/me/accept-invites", response_model=AcceptInvitesResponse)
async def accept_invites(
    user: AuthUser = Depends(current_user),
    db: SupabaseService = Depends(get_db),
) -> AcceptInvitesResponse:
    """Call after login: join every store that invited this user's email.
    Only a confirmed email counts (someone can't claim another person's invite)."""
    if not user.email or not user.email_confirmed:
        return AcceptInvitesResponse(joined=0)
    return AcceptInvitesResponse(joined=await db.accept_invites(user.id, user.email))


@router.post("/stores/{store_id}/link-code", response_model=LinkCodeResponse)
async def link_code(
    store: Store = Depends(require_owner),
    onboarding: Onboarding = Depends(get_onboarding),
) -> LinkCodeResponse:
    """A one-time code: send "/link <code>" in the staff group (or the channel)
    after adding the bot there. A new code replaces the previous one."""
    code, expires_at = await onboarding.new_link_code(store)
    bot = f"@{store.telegram_bot_username}" if store.telegram_bot_username else "your bot"
    return LinkCodeResponse(
        code=code, expires_at=expires_at,
        instructions=(f"Add {bot} to your staff group (or as an admin of your channel, allowed to "
                      f"post and edit messages), then send there: {LINK_COMMAND} {code}"),
    )


@router.post("/stores/{store_id}/invites", response_model=InviteResponse)
async def invite_staff(
    body: InviteRequest,
    store: Store = Depends(require_owner),
    user: AuthUser = Depends(current_user),
    db: SupabaseService = Depends(get_db),
) -> InviteResponse:
    email = str(body.email).lower()
    await db.invite_staff(store.id, email, user.id)
    sent = await db.send_invite_email(email)
    return InviteResponse(ok=True, email_sent=sent, message=(
        f"{email} is invited. They'll get an email to set a password." if sent else
        f"{email} is invited. If they already have an account, they just log in to join."))


@router.delete("/stores/{store_id}/staff/{user_id}")
async def remove_staff(
    user_id: UUID,
    store: Store = Depends(require_owner),
    db: SupabaseService = Depends(get_db),
) -> dict[str, bool]:
    """Remove a staff member. An owner can't be removed here."""
    if not await db.remove_staff(store.id, user_id):
        raise HTTPException(status_code=404, detail="no staff member with this id (owners can't be removed)")
    return {"ok": True}


@router.put("/stores/{store_id}/bot-token", response_model=StoreResponse)
async def change_bot_token(
    body: BotTokenRequest,
    store: Store = Depends(require_owner),
    onboarding: Onboarding = Depends(get_onboarding),
) -> StoreResponse:
    """D17: check the new token, save it with a new webhook secret, and
    reconnect. The same bot with a regenerated token works too."""
    try:
        return _store_response(await onboarding.change_bot_token(store, body.bot_token))
    except OnboardingError as error:
        raise _refused(error)
