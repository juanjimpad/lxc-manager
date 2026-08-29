import os
from pathlib import Path
from typing import Optional

# config.py lives in app/core/ — three .parent calls to reach the repo root.
BASE_DIR = Path(__file__).resolve().parent.parent.parent


def _env(*names: str, default: str = "") -> str:
    """First non-empty match among aliases (HLMGR_* then legacy LXCMGR_*)."""
    for name in names:
        value = os.environ.get(name)
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return default


DB_PATH = _env("HLMGR_DB_PATH", "LXCMGR_DB_PATH", default=str(BASE_DIR / "homelab-manager.db"))
SSH_KEY_PATH = _env("HLMGR_SSH_KEY", "LXCMGR_SSH_KEY", default="/home/lxcmgr/.ssh/id_ed25519")

PVE_API_URL = _env("HLMGR_PVE_API_URL", "LXCMGR_PVE_API_URL")
PVE_TOKEN_ID = _env("HLMGR_PVE_TOKEN_ID", "LXCMGR_PVE_TOKEN_ID")
PVE_TOKEN_SECRET = _env("HLMGR_PVE_TOKEN_SECRET", "LXCMGR_PVE_TOKEN_SECRET")
PVE_ENABLED = bool(PVE_API_URL and PVE_TOKEN_ID and PVE_TOKEN_SECRET)

_PVE_CA_FILE = _env("HLMGR_PVE_CA_FILE", "LXCMGR_PVE_CA_FILE")
_PVE_VERIFY_RAW = _env("HLMGR_PVE_VERIFY_SSL", "LXCMGR_PVE_VERIFY_SSL", default="1").lower()
if _PVE_CA_FILE:
    PVE_VERIFY_SSL: Optional[object] = _PVE_CA_FILE
elif _PVE_VERIFY_RAW in ("0", "false", "no"):
    PVE_VERIFY_SSL = False
else:
    PVE_VERIFY_SSL = True

# Env is the bootstrap default; Settings can override and persist in SQLite.
TELEGRAM_TOKEN = _env("HLMGR_TELEGRAM_TOKEN", "LXCMGR_TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = _env("HLMGR_TELEGRAM_CHAT_ID", "LXCMGR_TELEGRAM_CHAT_ID")

NODE_HOSTS = {
    "lenovo-m700": _env("HLMGR_HOST_M700", "LXCMGR_HOST_M700", default="192.168.1.8"),
    "dell-5060": _env("HLMGR_HOST_5060", "LXCMGR_HOST_5060", default="192.168.1.6"),
}

TAG_APP_TYPE = {
    "proxy": "npm",
    "adblock": "adguard",
    "dashboard": "glance",
    "network": "ddns",
    "git": "gitea",
    "docker": "docker-host",
}

APP_UPDATE_MODE = {
    "adguard": "auto",
    "glance": "check-only",
    "ddns": "check-only",
    "gitea": "check-only",
    "npm": "none",
    "docker-host": "none",
}

REQUIRED_TAG = "managed"
AUTO_UPDATE_TAG = "auto-update"

PBS_STORAGE = _env("HLMGR_PBS_STORAGE", "LXCMGR_PBS_STORAGE", default="unraid-pbs")

_PBS_STORAGES_ENV = _env("HLMGR_PBS_STORAGES", "LXCMGR_PBS_STORAGES")
PBS_STORAGES: Optional[list] = (
    [s.strip() for s in _PBS_STORAGES_ENV.split(",") if s.strip()]
    if _PBS_STORAGES_ENV
    else None
)

VM_GUESTS = {
    # empty — dell5060-docker (112) destroyed 2026-08-28
}

# Manager URL clients should poll (shown in Settings / install hint).
PUBLIC_URL = _env("HLMGR_PUBLIC_URL", "LXCMGR_PUBLIC_URL")

# Git remote used by self-update. Empty = origin of the working clone.
UPDATE_REMOTE = _env("HLMGR_UPDATE_REMOTE", "LXCMGR_UPDATE_REMOTE", default="origin")
UPDATE_BRANCH = _env("HLMGR_UPDATE_BRANCH", "LXCMGR_UPDATE_BRANCH", default="main")

# A client is offline if no heartbeat arrives within this many seconds.
CLIENT_OFFLINE_AFTER_S = int(_env("HLMGR_CLIENT_OFFLINE_AFTER", default="90"))
SESSION_SECRET = _env("HLMGR_SESSION_SECRET", "LXCMGR_SESSION_SECRET")

TIMEZONE = _env("HLMGR_TIMEZONE", "LXCMGR_TIMEZONE", default="Europe/Madrid")
