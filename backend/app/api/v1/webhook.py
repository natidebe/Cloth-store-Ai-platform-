"""Single entry point for every customer message, across every store.

Telegram calls POST /api/v1/webhook/{store_id} for each new message sent to
that store's bot. We check the request, save the message to the inbox, and
only then answer 200. So "OK" to Telegram means "safely stored": if the
server crashes afterwards, the message is still in the inbox and the
recovery sweep handles it. The real work happens in the background.
"""
import logging
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from pydantic import ValidationError

from app.agents.messages import both
from app.agents.onboarding import Onboarding, link_command
from app.agents.orchestrator import Orchestrator
from app.core.config import get_settings
from app.core.security import verify_telegram_secret
from app.models.schemas import Store, TelegramUpdate
from app.services.supabase_service import DatabaseError, SupabaseService
from app.services.telegram_service import TelegramError, TelegramService, parse_update
from app.utils.logging import log_context

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


def get_orchestrator(request: Request) -> Orchestrator:
    orchestrator = request.app.state.orchestrator
    if orchestrator is None:
        raise HTTPException(status_code=503, detail="database not configured")
    return orchestrator


@router.post("/webhook/{store_id}")
async def telegram_webhook(
    store_id: UUID,
    request: Request,
    background: BackgroundTasks,
    secret: str | None = Header(default=None, alias=SECRET_HEADER),
    db: SupabaseService = Depends(get_db),
    orchestrator: Orchestrator = Depends(get_orchestrator),
) -> dict[str, bool]:
    # 1. Find the store (also pending or suspended ones: see step 4).
    try:
        store = await db.get_store_any_status(store_id)
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
        payload = await request.json()
        update = TelegramUpdate.model_validate(payload)
    except (ValueError, ValidationError):
        logger.warning("unreadable update ignored", extra={"store_id": str(store_id)})
        return {"ok": True}

    # 4. Setup commands work for every store, even one waiting for approval:
    #    /link <code> (Phase 9b) and /chatid. Otherwise a store that isn't
    #    active (D14) only tells customers it isn't taking orders yet.
    if link_command(update) is not None:
        onboarding = Onboarding(db, orchestrator.telegram, get_settings().public_base_url)
        background.add_task(onboarding.link, store, update)
        return {"ok": True}
    if orchestrator.staff.is_chat_id_request(update):
        background.add_task(orchestrator.staff.send_chat_id, store, update)
        return {"ok": True}
    if store.status != "active":
        message = parse_update(store.id, update)
        if message is not None and message.kind != "button":
            background.add_task(_say_not_open, orchestrator.telegram, store, message.telegram_id)
        return {"ok": True}

    #    The staff group (button presses, staff replying to a customer) is
    #    handled separately, in the background (Phase 9).
    if orchestrator.staff.is_staff_update(store, update):
        background.add_task(orchestrator.staff.handle, store, update)
        return {"ok": True}

    # 5. Only private messages from people are handled (not groups, edits, bots).
    message = parse_update(store.id, update)
    if message is None:
        logger.info("update ignored", extra={"store_id": str(store_id), "update_id": update.update_id})
        return {"ok": True}

    with log_context(store_id=str(store_id), telegram_id=message.telegram_id,
                     update_id=message.update_id):
        # 6. Save it BEFORE answering. If saving fails, answer 503 so
        #    Telegram sends it again later.
        try:
            is_new = await orchestrator.conversations.save_to_inbox(
                store.id, update.update_id, message.telegram_id, payload
            )
        except DatabaseError:
            logger.exception("could not save to inbox")
            raise HTTPException(status_code=503, detail="try again later")
        if not is_new:
            logger.info("duplicate update ignored")
            return {"ok": True}

        logger.info("message received", extra={"kind": message.kind})
        # 7. Handle it after responding.
        background.add_task(orchestrator.process_customer, store, message.telegram_id)
        return {"ok": True}


async def _say_not_open(telegram: TelegramService, store: Store, chat_id: int) -> None:
    try:
        await telegram.send_message(store.telegram_bot_token.get_secret_value(), chat_id,
                                    both("store_not_open"))
    except TelegramError as error:
        logger.warning("not-open reply not sent", extra={"error": error.description})
