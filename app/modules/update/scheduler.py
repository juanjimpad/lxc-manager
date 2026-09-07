from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from ...core import config, db, proxmox
from ..backups import status as backup_status
from ..security import audit as security_audit
from ..selfupdate import service as selfupdate
from . import runner

scheduler = BackgroundScheduler(timezone="Europe/Madrid")

# defaults on deploy: 5060 (less critical) before m700, early Saturday
# mornings — see docs/design/machines/lxc-manager.md in the homelab
# repo. Editable per guest from the UI once deployed.
DEFAULT_CRON_BY_NODE = {
    "dell-5060": "0 4 * * 6",
    "lenovo-m700": "0 6 * * 6",
}

# Same work as the UI Refresh button (discover + PBS status), plus the
# security audits each guest page can run with "Check now".
PANEL_REFRESH_HOURS = 6


def _job_id(vmid: int) -> str:
    return f"guest-{vmid}"


def next_run_for(vmid: int):
    job = scheduler.get_job(_job_id(vmid))
    return job.next_run_time if job else None


def _prune_stale_guest(conn, vmid: int) -> None:
    """Remove a guest that no longer has the discovery tag (destroyed or
    untagged). Child rows first — FKs have no ON DELETE CASCADE."""
    job_id = _job_id(vmid)
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    for table in (
        "schedules",
        "security_checks",
        "backup_status",
        "runs",
        "backup_runs",
    ):
        conn.execute(f"DELETE FROM {table} WHERE vmid = ?", (vmid,))
    conn.execute("DELETE FROM guests WHERE vmid = ?", (vmid,))


def sync_guests_and_schedules() -> None:
    """Discovers guests by tag and creates/updates their schedules row
    and their scheduler job, then refreshes each guest's backup status
    (guests must exist in the DB first — backup_status.sync_all() reads
    the vmid list from there). Guests that vanished from Proxmox (or lost
    the required tag) are pruned from SQLite — refresh used to only
    upsert, so destroyed guests lingered forever. Called on startup, from
    the UI Refresh button, and by panel_refresh every PANEL_REFRESH_HOURS."""
    guests = proxmox.discover_guests()
    seen = {g["vmid"] for g in guests}
    with db.get_conn() as conn:
        for g in guests:
            conn.execute(
                """INSERT INTO guests
                     (vmid, node, name, type, app_type, tags, maxmem, maxcpu, ip,
                      os_family, os_id, update_supported, last_seen)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
                   ON CONFLICT(vmid) DO UPDATE SET
                     node=excluded.node, name=excluded.name, type=excluded.type,
                     app_type=excluded.app_type, tags=excluded.tags,
                     maxmem=excluded.maxmem, maxcpu=excluded.maxcpu, ip=excluded.ip,
                     os_family=excluded.os_family, os_id=excluded.os_id,
                     update_supported=excluded.update_supported,
                     last_seen=excluded.last_seen""",
                (g["vmid"], g["node"], g["name"], g["type"], g["app_type"], g["tags"],
                 g["maxmem"], g["maxcpu"], g["ip"],
                 g["os_family"], g["os_id"], g["update_supported"]),
            )
            existing = conn.execute(
                "SELECT 1 FROM schedules WHERE vmid = ?", (g["vmid"],)
            ).fetchone()
            if existing is None:
                default_cron = DEFAULT_CRON_BY_NODE.get(g["node"], "0 5 * * 6")
                # Weekly apt only if Proxmox also has auto-update; managed
                # alone is enough for panel / backups / security.
                tag_list = proxmox.parse_tags(g["tags"])
                enabled = 1 if config.AUTO_UPDATE_TAG in tag_list else 0
                conn.execute(
                    "INSERT INTO schedules (vmid, cron, enabled) VALUES (?, ?, ?)",
                    (g["vmid"], default_cron, enabled),
                )
        stale = [
            row["vmid"]
            for row in conn.execute("SELECT vmid FROM guests").fetchall()
            if row["vmid"] not in seen
        ]
        for vmid in stale:
            _prune_stale_guest(conn, vmid)
    reload_jobs()
    backup_status.sync_all()


def panel_refresh() -> None:
    """Periodic counterpart of the UI Refresh button, plus security audits.

    Discovery alone used to be scheduled with next_run_time=None, which
    APScheduler treats as a paused job — so after startup the panel never
    rediscovered guests or refreshed PBS/security until someone clicked
    Refresh. This job must keep a real next_run_time."""
    sync_guests_and_schedules()
    security_audit.audit_all_guests()


def reload_jobs() -> None:
    with db.get_conn() as conn:
        rows = conn.execute("SELECT vmid, cron, enabled FROM schedules").fetchall()
    for row in rows:
        job_id = _job_id(row["vmid"])
        scheduler.remove_job(job_id) if scheduler.get_job(job_id) else None
        if row["enabled"]:
            scheduler.add_job(
                runner.run_guest,
                CronTrigger.from_crontab(row["cron"], timezone="Europe/Madrid"),
                args=[row["vmid"]],
                id=job_id,
                replace_existing=True,
            )


def start() -> None:
    # Do NOT pass next_run_time=None — that pauses the job forever in
    # APScheduler 3.x (only startup + manual Refresh would ever sync).
    scheduler.add_job(
        panel_refresh,
        "interval",
        hours=PANEL_REFRESH_HOURS,
        id="panel-refresh",
        next_run_time=datetime.now(scheduler.timezone) + timedelta(minutes=2),
        replace_existing=True,
    )
    scheduler.add_job(
        selfupdate.refresh_cache,
        "interval",
        hours=24,
        id="selfupdate-check",
        # First pass ~1 min after boot so a just-restarted panel fills
        # the cache; later ticks are daily. refresh_cache never applies.
        next_run_time=datetime.now(scheduler.timezone) + timedelta(minutes=1),
        replace_existing=True,
    )
    sync_guests_and_schedules()  # first pass, synchronous, before starting
    scheduler.start()
