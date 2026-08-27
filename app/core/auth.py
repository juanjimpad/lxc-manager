"""The app's own login (independent of NPM's Basic Auth, still in front
as a second layer if enabled). PBKDF2-HMAC-SHA256 via the standard
library's hashlib — no new dependency for a single password hash."""
import hashlib
import hmac
import os
import secrets

from fastapi import Request
from fastapi.responses import RedirectResponse

from . import db

ITERATIONS = 260_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITERATIONS)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest_hex = stored.split("$", 1)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITERATIONS)
    return hmac.compare_digest(digest.hex(), digest_hex)


def seed_admin_if_empty() -> None:
    with db.get_conn() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
        if row["n"] > 0:
            return
        username = os.environ.get("LXCMGR_ADMIN_USER", "admin")
        password = os.environ.get("LXCMGR_ADMIN_PASSWORD")
        if not password:
            password = secrets.token_urlsafe(18)
            print(f"[lxc-manager] admin user created: {username} / {password}"
                  f" (save it, it won't be shown again; change it in /settings)")
        conn.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (username, hash_password(password)),
        )


def current_user(request: Request) -> str | None:
    return request.session.get("user")


def require_login(request: Request):
    if current_user(request) is None:
        raise LoginRequired()


class LoginRequired(Exception):
    pass


def login_redirect(request: Request) -> RedirectResponse:
    return RedirectResponse(f"/login?next={request.url.path}", status_code=303)
