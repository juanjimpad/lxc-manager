"""Update-module service: guests, schedules, runs, kernel. No HTTP, no Jinja."""
from __future__ import annotations

from apscheduler.triggers.cron import CronTrigger

from ...core import agent, config, db
from ...core.errors import GuestNotFound, InvalidCron
from ..backups import status as backup_status
from ..security import audit as security_audit
from . import runner, scheduler


def _row(row) -> dict | None:
    return dict(row) if row is not None else None


def get_guest(vmid: int) -> dict | None:
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM guests WHERE vmid=?", (vmid,)).fetchone()
    return _row(row)


def get_schedule(vmid: int) -> dict | None:
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM schedules WHERE vmid=?", (vmid,)).fetchone()
    return _row(row)


def list_runs(vmid: int, limit: int = 20) -> list[dict]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM runs WHERE vmid=? ORDER BY id DESC LIMIT ?",
            (vmid, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def update_via(guest: dict) -> dict:
    """How this guest is reached for package work — not a UI string."""
    vmid = guest["vmid"]
    if vmid in config.VM_GUESTS:
        vg = config.VM_GUESTS[vmid]
        return {"kind": "ssh", "target": f"{vg['user']}@{vg['host']}"}
    return {"kind": "agent"}


def list_guests_with_status() -> tuple[list[dict], dict, dict]:
    with db.get_conn() as conn:
        guests = conn.execute(
            """SELECT g.*, s.cron, s.enabled,
                      (SELECT status FROM runs r WHERE r.vmid=g.vmid ORDER BY r.id DESC LIMIT 1) as last_status,
                      (SELECT started_at FROM runs r WHERE r.vmid=g.vmid ORDER BY r.id DESC LIMIT 1) as last_run
               FROM guests g LEFT JOIN schedules s ON s.vmid = g.vmid
               ORDER BY g.vmid"""
        ).fetchall()
    guests = [dict(g) for g in guests]
    security = {g["vmid"]: security_audit.get_evaluated(g["vmid"]) for g in guests}
    backups = {g["vmid"]: backup_status.get_evaluated(g["vmid"]) for g in guests}
    return guests, security, backups


def sync_guests() -> tuple[list[dict], dict, dict]:
    scheduler.sync_guests_and_schedules()
    return list_guests_with_status()


def get_guest_detail(vmid: int) -> dict | None:
    guest = get_guest(vmid)
    if guest is None:
        return None
    runs = list_runs(vmid)
    return {
        "guest": guest,
        "schedule": get_schedule(vmid),
        "runs": runs,
        "sec": security_audit.get_evaluated(vmid),
        "bk": backup_status.get_evaluated(vmid),
        "pending": backup_status.is_pending(vmid),
        "backup_runs": backup_status.recent_runs(vmid),
        "runner_pending": runner.is_pending(vmid),
        "next_run": scheduler.next_run_for(vmid),
        "update_via": update_via(guest),
    }


def get_kernel(vmid: int) -> str | None:
    guest = get_guest(vmid)
    if guest is None:
        return None
    return agent.get_kernel(guest["node"], vmid)


def get_update_controls(vmid: int) -> dict | None:
    guest = get_guest(vmid)
    if guest is None:
        return None
    runs = list_runs(vmid)
    running = runner.is_pending(vmid) or (bool(runs) and runs[0]["status"] == "running")
    return {
        "vmid": vmid,
        "runs": runs,
        "running": running,
        "update_supported": bool(guest["update_supported"]),
        "os_family": guest["os_family"],
        "os_id": guest["os_id"],
    }


def start_update(vmid: int) -> None:
    if get_guest(vmid) is None:
        raise GuestNotFound()
    runner.mark_pending(vmid)


def set_schedule(vmid: int, cron: str, enabled: bool):
    if get_guest(vmid) is None:
        raise GuestNotFound()
    try:
        CronTrigger.from_crontab(cron, timezone="Europe/Madrid")
    except ValueError as exc:
        raise InvalidCron() from exc
    with db.get_conn() as conn:
        conn.execute(
            """INSERT INTO schedules (vmid, cron, enabled) VALUES (?, ?, ?)
               ON CONFLICT(vmid) DO UPDATE SET
                 cron=excluded.cron, enabled=excluded.enabled""",
            (vmid, cron, 1 if enabled else 0),
        )
    scheduler.reload_jobs()
    return scheduler.next_run_for(vmid)
