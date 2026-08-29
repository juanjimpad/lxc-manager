"""Telegram notifications. Token/chat and event toggles live in Settings
(SQLite), with env vars as bootstrap defaults so an existing .env still
works before anyone opens the UI."""
from __future__ import annotations

import httpx

from . import config, db

EVENT_KEYS = (
    "notify_client_offline",
    "notify_client_online",
    "notify_update_ok",
    "notify_update_failed",
    "notify_self_update",
)


def _truthy(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


def get_token() -> str:
    return db.get_setting("telegram_token", config.TELEGRAM_TOKEN)


def get_chat_id() -> str:
    return db.get_setting("telegram_chat_id", config.TELEGRAM_CHAT_ID)


def event_enabled(event: str) -> bool:
    return _truthy(db.get_setting(event, "1"))


def notify(text: str, event: str | None = None) -> bool:
    if event and not event_enabled(event):
        return False
    token = get_token()
    chat_id = get_chat_id()
    if not token or not chat_id:
        return False
    try:
        r = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": text},
            timeout=10,
        )
        return r.status_code == 200
    except httpx.HTTPError:
        return False  # a failed notification must not take the run down with it
