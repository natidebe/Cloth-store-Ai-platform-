"""Single entry point for every customer message, across every store.

Telegram calls POST /api/v1/webhook/{store_id} for each new message sent to
that store's bot. We check the request, answer 200 right away, and handle
the message in the background so Telegram isn't kept waiting.
"""
import logging
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from pydantic import ValidationError

from app.agents.orchestrator import handle_update
from app.core.security import verify_telegram_secret
from app.models.schemas import TelegramUpdate
from app.services.supabase_service import DatabaseError, SupabaseService
from app.services.telegram_service import TelegramService

logger = logging.getLogger(__name__)
router = APIRouter(tags=["webhook"])

SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"


# Dependencies: FastAPI calls these and passes the result to the endpoint.
# Tests replace them with fakes.

def get_db(request: Request) -> SupabaseService:
    db = request.app.state.db
    if db is None:
        raise HTTPException(status_code=503, detail="database not configured")
    return db


def get_telegram(request: Request) -> TelegramService:
    return request.app.state.telegram


@router.post("/webhook/{store_id}")
async def telegram_webhook(
    store_id: UUID,
    request: Request,
    background: BackgroundTasks,
    secret: str | None = Header(default=None, alias=SECRET_HEADER),
    db: SupabaseService = Depends(get_db),
    telegram: TelegramService = Depends(get_telegram),
) -> dict[str, bool]:
    # 1. Find the store (switched-off stores count as not found).
    try:
        store = await db.get_store(store_id)
    except DatabaseError:
        logger.exception("store lookup failed", extra={"store_id": str(store_id)})
        # Telegram will retry later, so the message isn't lost.
        raise HTTPException(status_code=503, detail="try again later")
    if store is None:
        raise HTTPException(status_code=404, detail="store not found")

    # 2. Check the request really came from Telegram.
    expected = store.webhook_secret.get_secret_value() if store.webhook_secret else None
    if not verify_telegram_secret(secret, expected):
        logger.warning("webhook secret mismatch", extra={"store_id": str(store_id)})
        raise HTTPException(status_code=401, detail="invalid secret")

    # 3. Read the update. If it's malformed, say OK anyway: retrying won't fix it.
    try:
        update = TelegramUpdate.model_validate(await request.json())
    except (ValueError, ValidationError):
        logger.warning("unreadable update ignored", extra={"store_id": str(store_id)})
        return {"ok": True}

    # 4. Handle it after responding.
    background.add_task(handle_update, store, update, telegram)
    return {"ok": True}
