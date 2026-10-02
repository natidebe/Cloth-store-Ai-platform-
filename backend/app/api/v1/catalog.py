"""Supabase database webhooks for the channel catalog (Phase 8d, D39).

Supabase calls POST /api/v1/catalog/webhook whenever a row of `products` or
`product_variants` is inserted, updated or deleted (set up once in the
Supabase dashboard: Database -> Webhooks). The request must carry the
X-Webhook-Secret header matching CATALOG_WEBHOOK_SECRET in .env, so nobody
else can trigger channel posts. We answer at once and update the posts in
the background.
"""
import hmac
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request

from app.agents.catalog import Catalog
from app.core.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/catalog", tags=["catalog"])


def get_catalog(request: Request) -> Catalog:
    catalog = request.app.state.catalog
    if catalog is None:
        raise HTTPException(status_code=503, detail="database not configured")
    return catalog


@router.post("/webhook")
async def catalog_webhook(
    request: Request,
    background: BackgroundTasks,
    secret: str | None = Header(default=None, alias="X-Webhook-Secret"),
    catalog: Catalog = Depends(get_catalog),
) -> dict[str, bool]:
    expected = get_settings().catalog_webhook_secret.get_secret_value()
    if not expected or not secret or not hmac.compare_digest(secret, expected):
        raise HTTPException(status_code=401, detail="invalid secret")
    try:
        change = await request.json()
    except ValueError:
        return {"ok": True}  # malformed: retrying won't fix it
    if not isinstance(change, dict):
        return {"ok": True}
    background.add_task(catalog.on_change, change)
    return {"ok": True}
