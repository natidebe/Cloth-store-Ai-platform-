"""Checks that requests come from who they claim to.

- Telegram webhooks: Telegram sends the `secret_token` given to `setWebhook`
  in the `X-Telegram-Bot-Api-Secret-Token` header.
- Dashboard (admin endpoints): the staff member's Supabase login token in
  `Authorization: Bearer <token>`. It is checked with Supabase, and the user
  must be staff of the store in the URL (see api/v1/admin.py).
"""
import hmac


def verify_telegram_secret(received: str | None, expected: str | None) -> bool:
    if not received or not expected:
        return False
    return hmac.compare_digest(received, expected)


def bearer_token(authorization: str | None) -> str | None:
    """The token from an "Authorization: Bearer <token>" header, if any."""
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()
