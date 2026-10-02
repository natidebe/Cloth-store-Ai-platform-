"""The platform admin page (Phase 9b, D14–D16).

Only platform admins (the platform_admins table, D15) can use these; anyone
else gets 403, including store owners.

    GET  /api/v1/platform/stores                   all stores: status, plan, number of orders
    POST /api/v1/platform/stores/{store}/approve   pending/suspended -> active (the bot takes orders)
    POST /api/v1/platform/stores/{store}/suspend   active -> suspended (the bot stops taking orders)
    PUT  /api/v1/platform/stores/{store}/plan      change the plan (a name only for now, D16)
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.agents.onboarding import Onboarding
from app.api.v1.stores import current_user, get_onboarding
from app.api.v1.webhook import get_db
from app.models.schemas import AuthUser, Store, StorePlan, StoreSummary
from app.services.supabase_service import SupabaseService

router = APIRouter(prefix="/platform", tags=["platform"])


async def require_platform_admin(
    user: AuthUser = Depends(current_user),
    db: SupabaseService = Depends(get_db),
) -> AuthUser:
    if not await db.is_platform_admin(user.id):
        raise HTTPException(status_code=403, detail="platform admins only")
    return user


async def _store(store_id: UUID, db: SupabaseService) -> Store:
    store = await db.get_store_any_status(store_id)
    if store is None:
        raise HTTPException(status_code=404, detail="store not found")
    return store


class PlanRequest(BaseModel):
    plan: StorePlan


class StatusResponse(BaseModel):
    store_id: UUID
    status: str
    plan: str | None


@router.get("/stores", response_model=list[StoreSummary])
async def list_stores(
    _: AuthUser = Depends(require_platform_admin),
    db: SupabaseService = Depends(get_db),
) -> list[StoreSummary]:
    return await db.list_all_stores()


@router.post("/stores/{store_id}/approve", response_model=StatusResponse)
async def approve_store(
    store_id: UUID,
    _: AuthUser = Depends(require_platform_admin),
    db: SupabaseService = Depends(get_db),
    onboarding: Onboarding = Depends(get_onboarding),
) -> StatusResponse:
    store = await _store(store_id, db)
    await onboarding.set_status(store, "active")
    return StatusResponse(store_id=store.id, status="active", plan=store.plan)


@router.post("/stores/{store_id}/suspend", response_model=StatusResponse)
async def suspend_store(
    store_id: UUID,
    _: AuthUser = Depends(require_platform_admin),
    db: SupabaseService = Depends(get_db),
    onboarding: Onboarding = Depends(get_onboarding),
) -> StatusResponse:
    store = await _store(store_id, db)
    await onboarding.set_status(store, "suspended")
    return StatusResponse(store_id=store.id, status="suspended", plan=store.plan)


@router.put("/stores/{store_id}/plan", response_model=StatusResponse)
async def change_plan(
    store_id: UUID,
    body: PlanRequest,
    _: AuthUser = Depends(require_platform_admin),
    db: SupabaseService = Depends(get_db),
) -> StatusResponse:
    store = await _store(store_id, db)
    await db.set_store_plan(store.id, body.plan)
    return StatusResponse(store_id=store.id, status=store.status, plan=body.plan)
