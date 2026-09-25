"""Single entry point for every customer message, across every store."""
from uuid import UUID

from fastapi import APIRouter

router = APIRouter(tags=["webhook"])


@router.post("/webhook/{store_id}")
async def telegram_webhook(store_id: UUID) -> dict[str, bool]:
    # TODO: verify secret token, look up store, hand off to conversation_service
    # in the background, and return 200 quickly.
    return {"ok": True}
