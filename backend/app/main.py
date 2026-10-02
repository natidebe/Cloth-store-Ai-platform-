import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from app.agents.catalog import Catalog
from app.agents.orchestrator import Orchestrator
from app.api.v1 import admin, catalog, health, miniapp, platform, platform_app, stores, webhook
from app.core.config import get_settings
from app.core.monitoring import init_sentry
from app.services.conversation_service import DatabaseConversationStore, RateLimiter
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
            ai_daily_limit=settings.ai_daily_calls_per_store,  # D21 (Phase 10)
            rate_limit=RateLimiter(settings.customer_messages_per_minute),
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


# Before the app exists, so Sentry hooks into FastAPI (off without SENTRY_DSN).
init_sentry(get_settings())

app = FastAPI(title="Cloth Store AI Platform", lifespan=lifespan)

app.include_router(health.router, prefix="/api/v1")
app.include_router(webhook.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1")
app.include_router(catalog.router, prefix="/api/v1")
app.include_router(stores.router, prefix="/api/v1")
app.include_router(platform.router, prefix="/api/v1")
app.include_router(miniapp.router, prefix="/api/v1")
app.include_router(platform_app.router, prefix="/api/v1")


# The Mini App (Phase 10b): the built React app from frontend/dist, or a
# placeholder page until it's built. Any path under /app/ that isn't a file
# gets index.html, so the app's own pages (e.g. /app/platform) work.
FRONTEND_BUILD = Path(__file__).resolve().parents[2] / "frontend" / "dist"
PLACEHOLDER = Path(__file__).resolve().parent / "static" / "miniapp"


@app.get("/app", include_in_schema=False)
@app.get("/app/{path:path}", include_in_schema=False)
async def mini_app(path: str = "") -> FileResponse:
    root = FRONTEND_BUILD if (FRONTEND_BUILD / "index.html").is_file() else PLACEHOLDER
    file = (root / path).resolve()
    if path and file.is_file() and file.is_relative_to(root.resolve()):
        # Built files have a hash in their name: they never change, cache them.
        cache = "public, max-age=31536000, immutable" if path.startswith("assets/") else "no-cache"
        return FileResponse(file, headers={"Cache-Control": cache})
    if path.startswith("assets/"):
        # A file from an older build: a real 404, so the app can reload itself.
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(root / "index.html", headers={"Cache-Control": "no-cache"})
