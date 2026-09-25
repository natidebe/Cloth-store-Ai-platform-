"""Platform-level settings loaded from environment variables / .env.

Per-store Telegram bot tokens live in `stores.telegram_bot_token`, not here.
"""
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/.env, regardless of which directory the server is started from.
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore"
    )

    # Supabase
    supabase_url: str = ""
    supabase_service_role_key: SecretStr = SecretStr("")

    # LLM
    llm_provider: str = "openai"
    llm_model: str = "gpt-5-mini"
    llm_api_key: SecretStr = SecretStr("")

    # Public HTTPS address of this server, used when registering Telegram
    # webhooks, e.g. https://abc123.ngrok-free.app
    public_base_url: str = ""

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
