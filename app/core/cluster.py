"""Cluster key: created once on first manager boot, shared with every
homelab-client. Clients never get a shell; they HMAC-sign HTTP calls
with this key. Stored next to the DB (mode 0600), not in the journal."""
from __future__ import annotations

import secrets
from pathlib import Path

from . import config

KEY_BYTES = 32


def key_path() -> Path:
    return Path(config.DB_PATH).resolve().parent / "CLUSTER_KEY"


def generate_key() -> str:
    return secrets.token_urlsafe(KEY_BYTES)


def load_key() -> str | None:
    path = key_path()
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return text or None


def save_key(key: str) -> Path:
    path = key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(key + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def ensure_cluster_key() -> str:
    existing = load_key()
    if existing:
        return existing
    key = generate_key()
    path = save_key(key)
    print(
        f"[homelab-manager] cluster key created at {path} (mode 0600) — "
        "copy it into each homelab-client's config"
    )
    return key
