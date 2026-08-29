"""Persist heartbeats and nested resources (docker / LXC / QEMU)."""
from __future__ import annotations

import datetime as dt
import json

from ...core import config, db, telegram

ALLOWED_PLATFORMS = {
    "ubuntu",
    "debian",
    "raspbian",
    "proxmox",
    "synology",
    "unraid",
    "linux",
    "other",
}
ALLOWED_RESOURCE_KINDS = {"docker", "lxc", "qemu"}
ALLOWED_CAPS = {
    "apt",
    "docker",
    "lxc",
    "qemu",
    "synology",
    "unraid",
    "self-update",
}


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _parse_iso(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return None


def is_online(last_seen: str | None, now: dt.datetime | None = None) -> bool:
    seen = _parse_iso(last_seen)
    if seen is None:
        return False
    now = now or dt.datetime.now()
    return (now - seen).total_seconds() <= config.CLIENT_OFFLINE_AFTER_S


def _as_int(value, default=None):
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value, default=None):
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def apply_heartbeat(client_id: str, payload: dict) -> dict:
    """Upsert the client row and replace its resource snapshot. Returns
    the stored client (with computed online flag) after the write."""
    name = str(payload.get("name") or payload.get("hostname") or client_id)[:80]
    hostname = str(payload.get("hostname") or name)[:80]
    platform = str(payload.get("platform") or "linux").lower()[:32]
    if platform not in ALLOWED_PLATFORMS:
        platform = "other"
    os_id = str(payload.get("os_id") or "")[:32]
    os_version = str(payload.get("os_version") or "")[:32]
    ip = str(payload.get("ip") or "")[:64]
    caps = payload.get("capabilities") or []
    if not isinstance(caps, list):
        caps = []
    caps_clean = sorted({str(c) for c in caps if str(c) in ALLOWED_CAPS})
    cpu = _as_float(payload.get("cpu_percent"))
    if cpu is not None:
        cpu = max(0.0, min(100.0, cpu))
    mem_used = _as_int(payload.get("mem_used"))
    mem_total = _as_int(payload.get("mem_total"))
    temp_c = _as_float(payload.get("temp_c"))
    uptime = _as_int(payload.get("uptime_seconds"))
    loadavg = payload.get("loadavg") or ""
    if isinstance(loadavg, (list, tuple)):
        loadavg = " ".join(str(x) for x in loadavg[:3])
    loadavg = str(loadavg)[:64]
    version = str(payload.get("version") or "")[:32]
    now = _now()

    with db.get_conn() as conn:
        prev = conn.execute(
            "SELECT last_status, name FROM clients WHERE id=?", (client_id,)
        ).fetchone()
        conn.execute(
            """INSERT INTO clients
                 (id, name, hostname, platform, os_id, os_version, ip, capabilities,
                  cpu_percent, mem_used, mem_total, temp_c, uptime_seconds, loadavg,
                  version, last_seen, last_status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'online')
               ON CONFLICT(id) DO UPDATE SET
                 name=excluded.name,
                 hostname=excluded.hostname,
                 platform=excluded.platform,
                 os_id=excluded.os_id,
                 os_version=excluded.os_version,
                 ip=excluded.ip,
                 capabilities=excluded.capabilities,
                 cpu_percent=excluded.cpu_percent,
                 mem_used=excluded.mem_used,
                 mem_total=excluded.mem_total,
                 temp_c=excluded.temp_c,
                 uptime_seconds=excluded.uptime_seconds,
                 loadavg=excluded.loadavg,
                 version=excluded.version,
                 last_seen=excluded.last_seen,
                 last_status='online'""",
            (
                client_id,
                name,
                hostname,
                platform,
                os_id,
                os_version,
                ip,
                json.dumps(caps_clean),
                cpu,
                mem_used,
                mem_total,
                temp_c,
                uptime,
                loadavg,
                version,
                now,
            ),
        )
        conn.execute("DELETE FROM client_resources WHERE client_id=?", (client_id,))
        resources = payload.get("resources") or []
        if isinstance(resources, list):
            for raw in resources[:200]:
                if not isinstance(raw, dict):
                    continue
                kind = str(raw.get("kind") or "")
                if kind not in ALLOWED_RESOURCE_KINDS:
                    continue
                rid = str(raw.get("id") or raw.get("resource_id") or "")[:80]
                if not rid:
                    continue
                extra = raw.get("extra") if isinstance(raw.get("extra"), dict) else {}
                conn.execute(
                    """INSERT OR REPLACE INTO client_resources
                         (client_id, kind, resource_id, name, status, extra)
                       VALUES (?,?,?,?,?,?)""",
                    (
                        client_id,
                        kind,
                        rid,
                        str(raw.get("name") or rid)[:80],
                        str(raw.get("status") or "")[:32],
                        json.dumps(extra)[:2000],
                    ),
                )

    if prev is None:
        telegram.notify(
            f"homelab-manager · new client {name} ({platform} {os_id}) at {ip or '—'}",
            event="notify_client_online",
        )
    elif prev["last_status"] == "offline":
        telegram.notify(
            f"homelab-manager · {name} is back online",
            event="notify_client_online",
        )
    return get_client(client_id)


def get_client(client_id: str):
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
    return decorate(row) if row else None


def list_resources(client_id: str) -> list:
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM client_resources
               WHERE client_id=? ORDER BY kind, name""",
            (client_id,),
        ).fetchall()
    out = []
    for r in rows:
        extra = {}
        try:
            extra = json.loads(r["extra"] or "{}")
        except json.JSONDecodeError:
            extra = {}
        out.append({**dict(r), "extra": extra})
    return out


def decorate(row) -> dict:
    d = dict(row)
    try:
        d["capabilities"] = json.loads(d.get("capabilities") or "[]")
    except json.JSONDecodeError:
        d["capabilities"] = []
    d["online"] = is_online(d.get("last_seen"))
    d["status"] = "online" if d["online"] else "offline"
    mem_total = d.get("mem_total") or 0
    mem_used = d.get("mem_used") or 0
    d["mem_percent"] = round(100.0 * mem_used / mem_total, 1) if mem_total else None
    return d


def list_clients() -> list[dict]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM clients ORDER BY name COLLATE NOCASE"
        ).fetchall()
    return [decorate(r) for r in rows]


def refresh_offline_status() -> None:
    """Flip last_status to offline and notify once when a client goes quiet."""
    with db.get_conn() as conn:
        rows = conn.execute("SELECT id, name, last_seen, last_status FROM clients").fetchall()
    for row in rows:
        online = is_online(row["last_seen"])
        if online and row["last_status"] != "online":
            with db.get_conn() as conn:
                conn.execute(
                    "UPDATE clients SET last_status='online' WHERE id=?", (row["id"],)
                )
        elif not online and row["last_status"] != "offline":
            with db.get_conn() as conn:
                conn.execute(
                    "UPDATE clients SET last_status='offline' WHERE id=?", (row["id"],)
                )
            telegram.notify(
                f"homelab-manager · {row['name']} is offline (no heartbeat)",
                event="notify_client_offline",
            )
