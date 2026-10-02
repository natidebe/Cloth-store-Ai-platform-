"""Checks that requests come from who they claim to.

- Telegram webhooks: Telegram sends the `secret_token` given to `setWebhook`
  in the `X-Telegram-Bot-Api-Secret-Token` header.
- Dashboard (admin endpoints): the staff member's Supabase login token in
  `Authorization: Bearer <token>`. It is checked with Supabase, and the user
  must be staff of the store in the URL (see api/v1/admin.py).

The login is declared as a Bearer security scheme, so /docs has an
"Authorize" button. (Swagger UI never sends a header *parameter* named
Authorization, so a plain Header(...) can't be tried there.)
"""
import hmac

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer(
    auto_error=False,  # we answer 401 ourselves, with a clear message
    description="The dashboard user's Supabase login token (scripts/login_token.py prints one).",
)


def verify_telegram_secret(received: str | None, expected: str | None) -> bool:
    if not received or not expected:
        return False
    return hmac.compare_digest(received, expected)


async def login_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str | None:
    """The token from an "Authorization: Bearer <token>" header, if any."""
    token = credentials.credentials.strip() if credentials else ""
    return token or None
