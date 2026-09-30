"""Get a dashboard login token, to try the staff and onboarding endpoints in /docs.

Run from the backend folder, with the virtual environment active:

    python -m scripts.login_token you@example.com

It asks for the password (not shown while typing) and prints a Supabase
access token. In http://localhost:8000/docs, open an endpoint, click
"Try it out", and fill the `authorization` field with:  Bearer <token>
It expires after about an hour.

No account yet? Create one in Supabase: Authentication -> Users -> Add user
(tick "Auto Confirm User").
"""
import argparse
import getpass
import sys

import httpx

from app.core.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Print a Supabase login token for /docs.")
    parser.add_argument("email")
    args = parser.parse_args()

    settings = get_settings()
    key = settings.supabase_service_role_key.get_secret_value()
    if not settings.supabase_url or not key:
        sys.exit("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in backend/.env first.")
    base = settings.supabase_url.rstrip("/").removesuffix("/rest/v1")

    password = getpass.getpass("Password: ")
    response = httpx.post(f"{base}/auth/v1/token", params={"grant_type": "password"},
                          headers={"apikey": key}, json={"email": args.email, "password": password},
                          timeout=15)
    if response.status_code != 200:
        sys.exit(f"Login failed: {response.json().get('error_description') or response.text}")
    print(response.json()["access_token"])


if __name__ == "__main__":
    main()
