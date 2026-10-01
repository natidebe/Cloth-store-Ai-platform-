"""Platform-level settings loaded from environment variables / .env.

Per-store Telegram bot tokens live in `stores.telegram_bot_token`, not here.
"""
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
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

    # Supabase database webhooks (Phase 8d, D39) send this in the
    # X-Webhook-Secret header, so nobody else can trigger channel updates.
    catalog_webhook_secret: SecretStr = SecretStr("")

    # Phase 10. D21: AI calls per store per day (Addis Ababa date); over it,
    # typed messages go to staff. Buttons and the order flow don't use AI.
    ai_daily_calls_per_store: int = Field(default=300, ge=0)
    # Messages one customer may send per minute; the rest are ignored.
    customer_messages_per_minute: int = Field(default=20, ge=1)

    @field_validator("supabase_url")
    @classmethod
    def _project_url_only(cls, value: str) -> str:
        # The client adds /rest/v1 itself, so accept the REST URL from the
        # dashboard too: https://x.supabase.co/rest/v1/ -> https://x.supabase.co
        value = value.strip().rstrip("/")
        if value.endswith("/rest/v1"):
            value = value[: -len("/rest/v1")]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
