import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1 import admin, health, webhook
from app.core.config import get_settings
from app.services.supabase_service import SupabaseService
from app.services.telegram_service import TelegramService
from app.utils.logging import setup_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level)
    logger.info(
        "starting up",
        extra={"llm_provider": settings.llm_provider, "llm_model": settings.llm_model},
    )

    # One Supabase client for the whole app, created once here.
    app.state.db = None
    if settings.supabase_url and settings.supabase_service_role_key.get_secret_value():
        app.state.db = await SupabaseService.connect(
            settings.supabase_url, settings.supabase_service_role_key.get_secret_value()
        )
    else:
        logger.warning("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set; database disabled")

    # One Telegram client shared by all stores' bots.
    app.state.telegram = TelegramService.create()
    yield
    await app.state.telegram.close()
    if app.state.db is not None:
        await app.state.db.close()
    logger.info("shutting down")


app = FastAPI(title="Cloth Store AI Platform", lifespan=lifespan)

app.include_router(health.router, prefix="/api/v1")
app.include_router(webhook.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1")
