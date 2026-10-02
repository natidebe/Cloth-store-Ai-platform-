"""Checking who opened the Mini App (Phase 10b, D42).

Telegram gives the Mini App page an `initData` string (Telegram.WebApp.initData)
with the user's account, signed with the token of the bot the app was opened
from. The page sends it to us in the X-Telegram-Init-Data header, and we
check the signature with that bot's token, as Telegram describes:
https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app

    secret = HMAC_SHA256(key="WebAppData", message=bot_token)
    hash   = HMAC_SHA256(key=secret, message=<every other field, sorted, "key=value" per line>)

A valid signature proves Telegram vouches for the user; no passwords.
"""
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl, urlencode

from pydantic import BaseModel

INIT_DATA_HEADER = "X-Telegram-Init-Data"
MAX_AGE_SECONDS = 24 * 60 * 60  # an opened Mini App stays usable for a day


class MiniAppUser(BaseModel):
    """The Telegram account that opened the Mini App."""
    id: int
    first_name: str = ""
    last_name: str | None = None
    username: str | None = None
    language_code: str | None = None

    @property
    def full_name(self) -> str:
        return " ".join(part for part in (self.first_name, self.last_name) if part) or str(self.id)


def check_init_data(init_data: str | None, bot_token: str | None, *,
                    max_age: int = MAX_AGE_SECONDS, now: float | None = None) -> MiniAppUser | None:
    """The user, if `init_data` was signed with `bot_token` and isn't too old.
    None for anything forged, altered, expired or malformed."""
    if not init_data or not bot_token:
        return None
    try:
        fields = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None
    received = fields.pop("hash", None)
    if not received:
        return None
    check_string = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        return None
    try:
        auth_date = int(fields.get("auth_date", "0"))
        user = MiniAppUser.model_validate(json.loads(fields["user"]))
    except (ValueError, KeyError, TypeError):
        return None
    if (now if now is not None else time.time()) - auth_date > max_age:
        return None
    return user


def sign_init_data(fields: dict[str, str], bot_token: str) -> str:
    """Build a signed initData string, the way Telegram does (for tests and
    local tools only: real ones come from Telegram)."""
    check_string = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**fields, "hash": digest})
