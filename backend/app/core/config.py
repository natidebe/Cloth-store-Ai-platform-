"""Platform-level settings loaded from environment variables / .env.

Per-store Telegram bot tokens live in `stores.telegram_bot_token`, not here.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    supabase_url: str = ""
    supabase_service_role_key: str = ""
    llm_provider: str = ""
    llm_api_key: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
