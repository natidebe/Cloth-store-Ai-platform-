import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1 import admin, health, webhook
from app.core.config import get_settings
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
    # TODO: initialize the Supabase client once here and store it on app.state
    yield
    logger.info("shutting down")


app = FastAPI(title="Cloth Store AI Platform", lifespan=lifespan)

app.include_router(health.router, prefix="/api/v1")
app.include_router(webhook.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1")
