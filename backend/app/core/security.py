"""Verifies incoming webhook requests genuinely came from Telegram.

Telegram sends the `secret_token` given to `setWebhook` in the
`X-Telegram-Bot-Api-Secret-Token` header.
"""
import hmac


def verify_telegram_secret(received: str | None, expected: str | None) -> bool:
    if not received or not expected:
        return False
    return hmac.compare_digest(received, expected)
