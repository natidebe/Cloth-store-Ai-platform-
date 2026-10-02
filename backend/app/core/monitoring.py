"""Error tracking with Sentry: an alert with the exact error when something
fails (an unhandled exception, a 5xx, or a logger.error / logger.exception).

Off unless SENTRY_DSN is set. Errors only, no performance tracing (the free
plan). Nothing secret or personal leaves the server: no request bodies, no
local variables, no default PII, and every event is scrubbed of bot tokens
(they are in Telegram API URLs), our own keys and secrets, the Mini App's
login data and webhook secret headers, and customers' phone numbers.
"""
import os
import re
from typing import Any

from app.core.config import Settings

# No \b in front: in Telegram API URLs the token follows "bot" (/bot123:ABC…).
BOT_TOKEN = re.compile(r"\d{5,15}:[A-Za-z0-9_-]{30,50}(?![A-Za-z0-9_-])")
# Ethiopian mobile numbers: 09xxxxxxxx / 07xxxxxxxx, +2519xxxxxxxx, 2517xxxxxxxx.
PHONE = re.compile(r"(?<!\d)(?:\+?251|0)[79]\d{8}(?!\d)")
SECRET_HEADERS = {
    "authorization",
    "cookie",
    "x-telegram-bot-api-secret-token",
    "x-telegram-init-data",
    "x-webhook-secret",
}
FILTERED = "[Filtered]"


def _secrets(settings: Settings) -> list[str]:
    values = [
        settings.supabase_service_role_key.get_secret_value(),
        settings.llm_api_key.get_secret_value(),
        settings.catalog_webhook_secret.get_secret_value(),
        settings.platform_bot_token.get_secret_value(),
    ]
    return [v for v in values if len(v) >= 8]


def scrub_text(text: str, secrets: list[str] = ()) -> str:
    for secret in secrets:
        text = text.replace(secret, FILTERED)
    text = BOT_TOKEN.sub("[bot-token]", text)
    return PHONE.sub("[phone]", text)


def scrub(value: Any, secrets: list[str] = ()) -> Any:
    """The same structure, with every string scrubbed and secret headers removed."""
    if isinstance(value, str):
        return scrub_text(value, secrets)
    if isinstance(value, dict):
        return {
            key: FILTERED if isinstance(key, str) and key.lower() in SECRET_HEADERS
            else scrub(item, secrets)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [scrub(item, secrets) for item in value]
    if isinstance(value, tuple):
        return tuple(scrub(item, secrets) for item in value)
    return value


def init_sentry(settings: Settings) -> bool:
    """Start Sentry if SENTRY_DSN is set; True if it was started."""
    dsn = settings.sentry_dsn.get_secret_value().strip()
    if not dsn:
        return False
    import sentry_sdk

    secrets = _secrets(settings)
    sentry_sdk.init(
        dsn=dsn,
        environment=settings.sentry_environment,
        release=os.environ.get("RENDER_GIT_COMMIT") or None,  # set by Render
        send_default_pii=False,
        include_local_variables=False,  # locals can hold tokens and customer data
        max_request_body_size="never",
        traces_sample_rate=0.0,  # errors only
        before_send=lambda event, hint: scrub(event, secrets),
        before_breadcrumb=lambda crumb, hint: scrub(crumb, secrets),
    )
    return True
