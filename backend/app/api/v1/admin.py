"""Staff actions for the dashboard (Phase 9).

The same actions exist as buttons in the staff Telegram group; these are for
the dashboard. Every endpoint needs the staff member's Supabase login token
(`Authorization: Bearer <token>`), and that user must be staff of the store
in the URL: a staff member of another store is refused.
"""
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.agents.catalog import Catalog
from app.agents.orchestrator import Orchestrator
from app.api.v1.catalog import get_catalog
from app.api.v1.webhook import get_db, get_orchestrator
from app.core.security import login_token
from app.models.schemas import Store
from app.services.supabase_service import SupabaseService

router = APIRouter(prefix="/admin", tags=["admin"])


async def require_staff(
    store_id: UUID,
    token: str | None = Depends(login_token),
    db: SupabaseService = Depends(get_db),
) -> UUID:
    """The logged-in staff member's user id. 401 without a valid login,
    403 if the user isn't staff of this store."""
    if token is None:
        raise HTTPException(status_code=401, detail="login required (Authorization: Bearer <token>)")
    user_id = await db.verify_staff(store_id, token)
    if user_id is None:
        raise HTTPException(status_code=403, detail="not staff of this store")
    return user_id


async def _active_store(store_id: UUID, db: SupabaseService) -> Store:
    store = await db.get_store(store_id)
    if store is None:
        raise HTTPException(status_code=404, detail="store not found")
    return store


class ConfirmPaymentRequest(BaseModel):
    amount: Decimal | None = Field(default=None, gt=0)  # default: the order total
    method: str | None = Field(default=None, max_length=100)  # e.g. "Telebirr" (D6: free text)


class ActionResponse(BaseModel):
    ok: bool
    message: str


@router.post("/stores/{store_id}/orders/{order_id}/confirm-payment", response_model=ActionResponse)
async def confirm_payment(
    store_id: UUID,
    order_id: UUID,
    body: ConfirmPaymentRequest | None = None,
    staff_user_id: UUID = Depends(require_staff),
    db: SupabaseService = Depends(get_db),
    orchestrator: Orchestrator = Depends(get_orchestrator),
) -> ActionResponse:
    """Reduce stock, mark the order paid, tell the customer, hand the chat back
    to the bot. 409 (nothing changed) if refused, e.g. an item sold out."""
    store = await _active_store(store_id, db)
    body = body or ConfirmPaymentRequest()
    result = await orchestrator.staff.confirm_payment(
        store, order_id, amount=body.amount, method=body.method, staff_user_id=staff_user_id)
    if not result.ok:
        raise HTTPException(status_code=409, detail=result.message)
    return ActionResponse(ok=True, message=result.message)


@router.post("/stores/{store_id}/products/{product_id}/publish", response_model=ActionResponse)
async def publish_product(
    store_id: UUID,
    product_id: UUID,
    staff_user_id: UUID = Depends(require_staff),
    db: SupabaseService = Depends(get_db),
    catalog: Catalog = Depends(get_catalog),
) -> ActionResponse:
    """Post the product to the store's channel now (or update its post).
    New products are also posted automatically by the database webhook (D37);
    this is for posting older products, or trying again."""
    store = await _active_store(store_id, db)
    result = await catalog.publish(store, product_id)
    if not result.ok:
        raise HTTPException(status_code=409, detail=result.message)
    return ActionResponse(ok=True, message=result.message)


@router.post("/stores/{store_id}/conversations/{telegram_id}/hand-back", response_model=ActionResponse)
async def hand_back(
    store_id: UUID,
    telegram_id: int,
    staff_user_id: UUID = Depends(require_staff),
    db: SupabaseService = Depends(get_db),
    orchestrator: Orchestrator = Depends(get_orchestrator),
) -> ActionResponse:
    """The bot starts answering this customer again."""
    store = await _active_store(store_id, db)
    resumed = await orchestrator.staff.hand_back(store, telegram_id)
    return ActionResponse(ok=True, message="The bot is answering this customer again." if resumed
                          else "The bot was already answering this customer.")
