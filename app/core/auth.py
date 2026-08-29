"""The app's own login (independent of NPM's Basic Auth, still in front
as a second layer if enabled). PBKDF2-HMAC-SHA256 via the standard
library's hashlib — no new dependency for a single password hash."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlparse

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse

from . import config, db

ITERATIONS = 260_000
MAX_PASSWORD_LEN = 256

# Login brute-force throttle (in-process; fail2ban remains the durable layer).
_LOGIN_WINDOW_S = 600
_LOGIN_MAX_FAILURES = 10
_login_failures: dict[str, list[float]] = {}


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITERATIONS)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    if len(password) > MAX_PASSWORD_LEN:
        return False
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
            # Never print the password to the journal — write once to a
            # 0600 file next to the DB and only log the path.
            out = Path(config.DB_PATH).resolve().parent / "INITIAL_ADMIN_PASSWORD"
            out.write_text(f"{username}\n{password}\n", encoding="utf-8")
            out.chmod(0o600)
            print(
                f"[lxc-manager] admin user created ({username}); "
                f"initial password in {out} (mode 0600) — change it in /settings "
                f"and delete that file"
            )
        conn.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (username, hash_password(password)),
        )


def authenticate(username: str, password: str) -> bool:
    """True if username/password match a row in `users`."""
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if row is None:
        return False
    return verify_password(password, row["password_hash"])


def begin_session(request: Request, username: str) -> None:
    """Rotate the session on login (new CSRF + user)."""
    request.session.clear()
    request.session["user"] = username
    ensure_csrf(request)


def change_password(username: str, current_password: str, new_password: str) -> str | None:
    """Update `username`'s password. Returns a strings.py key on failure, else None."""
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if row is None or not verify_password(current_password, row["password_hash"]):
            return "wrong_current_password"
        if len(new_password) < 12:
            return "password_too_short"
        if len(new_password) > MAX_PASSWORD_LEN:
            return "password_too_long"
        conn.execute(
            "UPDATE users SET password_hash=? WHERE username=?",
            (hash_password(new_password), username),
        )
    return None


def current_user(request: Request) -> str | None:
    return request.session.get("user")


def bearer_ok(request: Request) -> bool:
    """True when Authorization: Bearer matches LXCMGR_API_TOKEN."""
    expected = config.API_TOKEN
    if not expected:
        return False
    header = request.headers.get("Authorization") or ""
    if not header.startswith("Bearer "):
        return False
    offered = header[7:].strip()
    if not offered:
        return False
    return hmac.compare_digest(offered, expected)


def api_identity(request: Request) -> str | None:
    """Session username, or 'api' for a valid Bearer token, or None."""
    user = current_user(request)
    if user:
        return user
    if bearer_ok(request):
        return "api"
    return None


def require_login(request: Request):
    if current_user(request) is None:
        raise LoginRequired()


def require_api_auth(request: Request) -> str:
    """Cookie session or Bearer token. Raises HTTP 401 JSON, never a redirect."""
    identity = api_identity(request)
    if identity is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return identity


def require_session_user(request: Request) -> str:
    """A real logged-in username (not the Bearer 'api' identity)."""
    user = current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


class LoginRequired(Exception):
    pass


def login_redirect(request: Request) -> RedirectResponse:
    return RedirectResponse(f"/login?next={safe_next(request.url.path)}", status_code=303)


def safe_next(value: str | None, default: str = "/") -> str:
    """Only allow same-origin relative paths. Blocks open redirects
    (https://…, //evil, \\evil, javascript:, etc.)."""
    if not value:
        return default
    value = value.strip()
    if not value.startswith("/") or value.startswith("//") or value.startswith("/\\"):
        return default
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc:
        return default
    return value


def ensure_csrf(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def check_csrf(request: Request, token: str | None) -> bool:
    expected = request.session.get("csrf_token")
    if not expected or not token:
        return False
    return hmac.compare_digest(expected, str(token))


async def require_csrf(request: Request) -> None:
    """Accept X-CSRF-Token (htmx via body hx-headers) or form field csrf_token."""
    token = request.headers.get("X-CSRF-Token")
    if not token:
        form = await request.form()
        raw = form.get("csrf_token")
        token = raw if isinstance(raw, str) else (raw[0] if raw else None)
    if not check_csrf(request, token):
        raise HTTPException(status_code=403, detail="CSRF token missing or invalid")


def client_ip(request: Request) -> str:
    if request.client:
        return request.client.host or "unknown"
    return "unknown"


def login_rate_limited(ip: str) -> bool:
    now = time.time()
    stamps = [t for t in _login_failures.get(ip, []) if now - t < _LOGIN_WINDOW_S]
    _login_failures[ip] = stamps
    return len(stamps) >= _LOGIN_MAX_FAILURES


def record_login_failure(ip: str) -> None:
    _login_failures.setdefault(ip, []).append(time.time())


def clear_login_failures(ip: str) -> None:
    _login_failures.pop(ip, None)
