"""Platform-level settings loaded from environment variables / backend/.env.

Per-store Telegram bot tokens live in `stores.telegram_bot_token`, not here.
"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/.env, regardless of which folder the server is started from
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    supabase_url: str = ""
    supabase_service_role_key: str = ""

    llm_provider: str = ""
    llm_model: str = ""
    llm_api_key: str = ""

    # Public HTTPS address Telegram uses to reach us (e.g. an ngrok URL)
    public_base_url: str = ""

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
