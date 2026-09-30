import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.agents.catalog import Catalog
from app.agents.orchestrator import Orchestrator
from app.api.v1 import admin, catalog, health, platform, stores, webhook
from app.core.config import get_settings
from app.services.conversation_service import DatabaseConversationStore
from app.services.llm_service import create_provider
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

    # The AI provider, chosen by LLM_PROVIDER / LLM_MODEL.
    app.state.llm = None
    try:
        app.state.llm = create_provider(
            settings.llm_provider, settings.llm_model, settings.llm_api_key.get_secret_value()
        )
    except ValueError as error:
        logger.warning("AI provider disabled", extra={"reason": str(error)})

    # Handles customer messages in the background (needs the database).
    app.state.orchestrator = None
    app.state.catalog = None
    recovery_task = None
    if app.state.db is not None:
        # The store's channel as its catalog (Phase 8d).
        app.state.catalog = Catalog(app.state.db, app.state.telegram)
        app.state.orchestrator = Orchestrator(
            app.state.db, DatabaseConversationStore(app.state.db), app.state.telegram,
            app.state.llm, catalog=app.state.catalog,
        )
        # Messages left unfinished by the last run (crash or restart).
        try:
            await app.state.orchestrator.recover(startup=True)
        except Exception:
            logger.exception("startup recovery failed; the sweep will try again")
        recovery_task = asyncio.create_task(app.state.orchestrator.run_recovery_loop())
    yield
    if recovery_task is not None:
        recovery_task.cancel()
    if app.state.orchestrator is not None:
        await app.state.orchestrator.close()
    if app.state.catalog is not None:
        await app.state.catalog.close()
    if app.state.llm is not None:
        await app.state.llm.close()
    await app.state.telegram.close()
    if app.state.db is not None:
        await app.state.db.close()
    logger.info("shutting down")


app = FastAPI(title="Cloth Store AI Platform", lifespan=lifespan)

app.include_router(health.router, prefix="/api/v1")
app.include_router(webhook.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1")
app.include_router(catalog.router, prefix="/api/v1")
app.include_router(stores.router, prefix="/api/v1")
app.include_router(platform.router, prefix="/api/v1")
