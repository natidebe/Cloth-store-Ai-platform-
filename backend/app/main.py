from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1 import admin, health, webhook
from app.core.config import get_settings
from app.utils.logging import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(get_settings().log_level)
    # TODO: initialize the Supabase client once here and store it on app.state
    yield


app = FastAPI(title="Cloth Store AI Platform", lifespan=lifespan)

app.include_router(health.router, prefix="/api/v1")
app.include_router(webhook.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1")
