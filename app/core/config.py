import os
from pathlib import Path
from typing import Optional

# config.py lives in app/core/ (one level deeper than before the module
# split) — three .parent calls to reach the repo root.
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = os.environ.get("LXCMGR_DB_PATH", str(BASE_DIR / "lxc-manager.db"))
SSH_KEY_PATH = os.environ.get("LXCMGR_SSH_KEY", "/home/lxcmgr/.ssh/id_ed25519")

PVE_API_URL = os.environ["LXCMGR_PVE_API_URL"]          # e.g. https://192.168.1.8:8006
PVE_TOKEN_ID = os.environ["LXCMGR_PVE_TOKEN_ID"]        # e.g. lxc-manager@pve!api
PVE_TOKEN_SECRET = os.environ["LXCMGR_PVE_TOKEN_SECRET"]
# TLS to the Proxmox API: path to a CA/PEM file, or "1"/"true" (default)
# for system CAs, or "0"/"false" to disable (not recommended). Prefer
# copying /etc/pve/pve-root-ca.pem into the panel LXC and pointing
# LXCMGR_PVE_CA_FILE at it.
_PVE_CA_FILE = os.environ.get("LXCMGR_PVE_CA_FILE", "").strip()
_PVE_VERIFY_RAW = os.environ.get("LXCMGR_PVE_VERIFY_SSL", "1").strip().lower()
if _PVE_CA_FILE:
    PVE_VERIFY_SSL: Optional[object] = _PVE_CA_FILE
elif _PVE_VERIFY_RAW in ("0", "false", "no"):
    PVE_VERIFY_SSL = False
else:
    PVE_VERIFY_SSL = True

TELEGRAM_TOKEN = os.environ.get("LXCMGR_TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("LXCMGR_TELEGRAM_CHAT_ID", "")

# Optional machine-to-machine token for /api/v1 (Authorization: Bearer).
# Empty = Bearer auth disabled; cookie sessions still work for the API.
API_TOKEN = os.environ.get("LXCMGR_API_TOKEN", "").strip()

# node name (as Proxmox reports it) -> host IP the agent SSH connects to
NODE_HOSTS = {
    "lenovo-m700": os.environ.get("LXCMGR_HOST_M700", "192.168.1.8"),
    "dell-5060": os.environ.get("LXCMGR_HOST_5060", "192.168.1.6"),
}

# secondary Proxmox tag -> app type. Mirrors lxc-manager-agent.sh exactly —
# keep both in sync by hand, there are only 5 entries.
TAG_APP_TYPE = {
    "proxy": "npm",
    "adblock": "adguard",
    "dashboard": "glance",
    "network": "ddns",
    "git": "gitea",
    "docker": "docker-host",
}

# whether app-update is safe to auto-apply, per app type. "docker-host" has
# no app-update at all (Watchtower already covers its containers) — only
# apt-upgrade applies there, and it goes through direct SSH, not the agent.
APP_UPDATE_MODE = {
    "adguard": "auto",
    "glance": "check-only",
    "ddns": "check-only",
    "gitea": "check-only",
    "npm": "none",
    "docker-host": "none",
}

# Discovery / panel / backups / security. Weekly apt schedules still
# require the separate `auto-update` tag (see scheduler sync).
REQUIRED_TAG = "managed"
AUTO_UPDATE_TAG = "auto-update"

# Fallback / Update-module notes only. "Back up now" and the Update
# run's safety dump both write to every discovered PBS storage via
# list_pbs_storages() — see PBS_STORAGES.
PBS_STORAGE = os.environ.get("LXCMGR_PBS_STORAGE", "unraid-pbs")

# Storages whose latest backup+verification we surface per guest, and
# that "Back up now" writes to. Empty (the default) = discover every
# `type=pbs` storage from the Proxmox API at sync/run time, so a newly
# added PBS backend shows up on its own. Set
# LXCMGR_PBS_STORAGES=unraid-pbs,synology-pbs to pin an explicit list
# (and order) instead.
_PBS_STORAGES_ENV = os.environ.get("LXCMGR_PBS_STORAGES", "").strip()
PBS_STORAGES: Optional[list] = (
    [s.strip() for s in _PBS_STORAGES_ENV.split(",") if s.strip()]
    if _PBS_STORAGES_ENV
    else None
)

# VMID(s) that are VMs, not LXC — reach them by direct SSH instead of the
# host-side pct-exec agent. Value is the guest's own IP + SSH user.
VM_GUESTS = {
    # empty — dell5060-docker (112) destroyed 2026-08-28
}
