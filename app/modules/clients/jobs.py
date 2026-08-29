"""Job queue: the manager enqueues, clients poll, run, and POST a result."""
from __future__ import annotations

import datetime as dt

from ...core import db, telegram

ALLOWED_KINDS = {
    "sys-update",
    "docker-update",
    "lxc-update",
    "qemu-update",
    "self-update",
}


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def enqueue(client_id: str, kind: str, target: str = "") -> int:
    if kind not in ALLOWED_KINDS:
        raise ValueError(f"unknown job kind: {kind}")
    with db.get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO client_jobs (client_id, kind, target, status, created_at)
               VALUES (?, ?, ?, 'queued', ?)""",
            (client_id, kind, target or "", _now()),
        )
        return int(cur.lastrowid)


def claim_next(client_id: str) -> dict | None:
    """Atomically take the oldest queued job for this client."""
    with db.get_conn() as conn:
        row = conn.execute(
            """SELECT * FROM client_jobs
               WHERE client_id=? AND status='queued'
               ORDER BY id ASC LIMIT 1""",
            (client_id,),
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            "UPDATE client_jobs SET status='running', started_at=? WHERE id=? AND status='queued'",
            (_now(), row["id"]),
        )
        claimed = conn.execute("SELECT * FROM client_jobs WHERE id=?", (row["id"],)).fetchone()
    return dict(claimed) if claimed else None


def complete(job_id: int, client_id: str, ok: bool, detail: str = "", summary: str = "") -> dict | None:
    status = "ok" if ok else "failed"
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM client_jobs WHERE id=? AND client_id=?",
            (job_id, client_id),
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            """UPDATE client_jobs
               SET status=?, finished_at=?, summary=?, detail=?
               WHERE id=?""",
            (status, _now(), summary or status, (detail or "")[:8000], job_id),
        )
        updated = conn.execute("SELECT * FROM client_jobs WHERE id=?", (job_id,)).fetchone()
    event = "notify_update_ok" if ok else "notify_update_failed"
    if row["kind"] == "self-update":
        event = "notify_self_update"
    name = row["kind"] + (f" {row['target']}" if row["target"] else "")
    telegram.notify(
        f"homelab-manager · {name} on {client_id} → {status}\n{(detail or '')[:500]}",
        event=event,
    )
    return dict(updated)


def list_jobs(client_id: str, limit: int = 20) -> list:
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM client_jobs
               WHERE client_id=? ORDER BY id DESC LIMIT ?""",
            (client_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def has_pending(client_id: str) -> bool:
    with db.get_conn() as conn:
        row = conn.execute(
            """SELECT 1 FROM client_jobs
               WHERE client_id=? AND status IN ('queued', 'running')
               LIMIT 1""",
            (client_id,),
        ).fetchone()
    return row is not None
