"""Backups module: is each guest's PBS backup chain healthy, per
storage? One Proxmox API call per PBS storage (no SSH, no agent)
lists that storage's contents at once — refreshed hourly by the
scheduler, or on demand after "Back up now" actually triggers a real
vzdump. Storages are discovered from the cluster (type=pbs) unless
LXCMGR_PBS_STORAGES pins an explicit list."""
import datetime as dt

from ...core import config, db, proxmox
from ...core.errors import GuestNotFound

STALE_HOURS = 36

# vmids with a backup currently in flight (triggered via "Back up now").
# In-memory only — this is a single-process app and the state is purely
# ephemeral, not worth a DB column for.
_pending: set[int] = set()


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def is_pending(vmid: int) -> bool:
    return vmid in _pending


def mark_pending(vmid: int) -> None:
    """Set before the background task is queued so the next htmx poll
    (triggered by HX-Trigger on the POST response) already sees the
    button as disabled — otherwise there's a window where the response
    has landed but run_backup_now hasn't started yet."""
    _pending.add(vmid)


def begin_run(vmid: int) -> None:
    """Insert the 'running' history row up front (with mark_pending) so
    the History panel can show it on the first backupStarted refresh."""
    mark_pending(vmid)
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO backup_runs (vmid, started_at, status) VALUES (?, ?, 'running')",
            (vmid, _now()),
        )


def _latest_by_vmid(backups: list[dict]) -> dict[int, dict]:
    latest: dict[int, dict] = {}
    for b in backups:
        vmid = b.get("vmid")
        if vmid is None:
            continue
        current = latest.get(vmid)
        if current is None or b["ctime"] > current["ctime"]:
            latest[vmid] = b
    return latest


def sync_all() -> None:
    """Refresh backup_status for every known guest × every PBS storage.
    A guest with no matching backup on a given storage gets a row with
    last_backup_at=NULL, which is itself the finding worth surfacing."""
    node = next(iter(config.NODE_HOSTS))
    storages = proxmox.list_pbs_storages()
    checked_at = _now()

    per_storage: dict[str, dict[int, dict]] = {}
    for storage in storages:
        try:
            per_storage[storage] = _latest_by_vmid(proxmox.list_backups(node, storage))
        except Exception:
            # One storage failing (offline NFS, auth, …) must not wipe
            # the others — leave its previous rows alone this cycle.
            continue

    with db.get_conn() as conn:
        vmids = [r["vmid"] for r in conn.execute("SELECT vmid FROM guests").fetchall()]
        for storage, latest_by_vmid in per_storage.items():
            for vmid in vmids:
                b = latest_by_vmid.get(vmid)
                if b is None:
                    last_backup_at = None
                    size = None
                    verification = None
                else:
                    last_backup_at = dt.datetime.fromtimestamp(b["ctime"]).isoformat(
                        timespec="seconds"
                    )
                    size = b.get("size")
                    verification = b.get("verification", {}).get("state")
                conn.execute(
                    """INSERT INTO backup_status
                         (vmid, storage, checked_at, last_backup_at, last_backup_size, verification)
                       VALUES (?,?,?,?,?,?)
                       ON CONFLICT(vmid, storage) DO UPDATE SET
                         checked_at=excluded.checked_at,
                         last_backup_at=excluded.last_backup_at,
                         last_backup_size=excluded.last_backup_size,
                         verification=excluded.verification""",
                    (vmid, storage, checked_at, last_backup_at, size, verification),
                )


def get_cached_rows(vmid: int) -> list:
    """All storage rows for a guest, in storage-name order (stable UI)."""
    with db.get_conn() as conn:
        return conn.execute(
            "SELECT * FROM backup_status WHERE vmid=? ORDER BY storage",
            (vmid,),
        ).fetchall()


def evaluate_row(row) -> dict:
    storage = row["storage"]
    if row["last_backup_at"] is None:
        return {
            "storage": storage,
            "has_backup": False,
            "checked_at": row["checked_at"],
            "overall_ok": False,
        }

    last = dt.datetime.fromisoformat(row["last_backup_at"])
    age_ok = (dt.datetime.now() - last) <= dt.timedelta(hours=STALE_HOURS)
    verification_failed = row["verification"] == "failed"

    return {
        "storage": storage,
        "has_backup": True,
        "checked_at": row["checked_at"],
        "last_backup_at": row["last_backup_at"],
        "age_ok": age_ok,
        "last_backup_size": row["last_backup_size"],
        "verification": row["verification"],
        "verification_failed": verification_failed,
        "overall_ok": age_ok and not verification_failed,
    }


def get_evaluated(vmid: int) -> dict:
    """Aggregate view: one entry per PBS storage, plus an overall
    status for the main table — ok (every storage healthy), failed
    (none healthy), or warning (mixed)."""
    rows = get_cached_rows(vmid)
    if not rows:
        return {"checked": False, "overall_ok": False, "overall_status": None, "storages": []}

    storages = [evaluate_row(r) for r in rows]
    oks = sum(1 for s in storages if s["overall_ok"])
    if oks == len(storages):
        overall_status = "ok"
    elif oks == 0:
        overall_status = "failed"
    else:
        overall_status = "warning"
    return {
        "checked": True,
        "overall_ok": overall_status == "ok",
        "overall_status": overall_status,
        "storages": storages,
    }


def recent_runs(vmid: int, limit: int = 20) -> list:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM backup_runs WHERE vmid=? ORDER BY id DESC LIMIT ?",
            (vmid, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_section(vmid: int) -> dict:
    """Cached status + in-flight flag + recent on-demand runs."""
    return {
        "bk": get_evaluated(vmid),
        "pending": is_pending(vmid),
        "backup_runs": recent_runs(vmid),
    }


def start_backup(vmid: int) -> None:
    with db.get_conn() as conn:
        row = conn.execute("SELECT 1 FROM guests WHERE vmid=?", (vmid,)).fetchone()
    if row is None:
        raise GuestNotFound()
    begin_run(vmid)


def run_backup_now(vmid: int) -> None:
    """A real on-demand backup — not a status refresh. Runs vzdump
    sequentially against every discovered PBS storage (same list the
    status card shows), then PBS integrity verify of the latest
    snapshot on each storage, then sync_all() refreshes timestamps.
    Each invocation is recorded in backup_runs (like update `runs`)."""
    from ...core import agent as agent_mod

    _pending.add(vmid)
    with db.get_conn() as conn:
        guest = conn.execute(
            "SELECT node, name FROM guests WHERE vmid=?", (vmid,)
        ).fetchone()
        row = conn.execute(
            """SELECT id FROM backup_runs
               WHERE vmid=? AND status='running' ORDER BY id DESC LIMIT 1""",
            (vmid,),
        ).fetchone()
        if row is None:
            cur = conn.execute(
                "INSERT INTO backup_runs (vmid, started_at, status) VALUES (?, ?, 'running')",
                (vmid, _now()),
            )
            run_id = cur.lastrowid
        else:
            run_id = row["id"]

    name = guest["name"] if guest is not None else str(vmid)
    storages = proxmox.list_pbs_storages()
    lines = [
        f"lxc-manager backup · {name} ({vmid})",
        f"storages: {', '.join(storages) if storages else '(none)'}",
    ]
    status = "failed"

    try:
        if guest is None:
            lines.append("guest not found in DB")
        elif not storages:
            lines.append("no PBS storages discovered")
        else:
            all_ok = True
            for storage, ok in proxmox.vzdump_all_storages(
                guest["node"],
                vmid,
                timeout_s=900,
                notes="lxc-manager on-demand backup",
            ):
                lines.append(f"vzdump → {storage}: {'ok' if ok else 'FAILED'}")
                if not ok:
                    all_ok = False
            # Integrity check on the snapshots just written (agent on the
            # Proxmox node talks to PBS with the storage credentials).
            if all_ok:
                ver = agent_mod.run_lxc_action(
                    guest["node"], vmid, "pbs-verify", timeout=900
                )
                for line in (ver.output or "").splitlines():
                    if line.strip():
                        lines.append(line.strip())
                if not ver.ok:
                    all_ok = False
                    if not (ver.output or "").strip():
                        lines.append("pbs-verify: FAILED")
            else:
                lines.append("pbs-verify: skipped (vzdump failed)")
            status = "ok" if all_ok else "failed"
        sync_all()
        with db.get_conn() as conn:
            brows = conn.execute(
                """SELECT storage, last_backup_at, last_backup_size, verification
                   FROM backup_status WHERE vmid=? ORDER BY storage""",
                (vmid,),
            ).fetchall()
        for brow in brows:
            if not brow["last_backup_at"]:
                lines.append(f"last on {brow['storage']}: (none)")
                continue
            extra = f"last on {brow['storage']}: {brow['last_backup_at']}"
            if brow["last_backup_size"] is not None:
                extra += f", {brow['last_backup_size']} bytes"
            if brow["verification"]:
                extra += f", verification={brow['verification']}"
            lines.append(extra)
    except Exception as exc:  # noqa: BLE001 — record anything, never leave 'running'
        status = "failed"
        lines.append(f"exception: {exc}")
    finally:
        summary = lines[0] + " → " + status
        detail = "\n".join(lines)
        with db.get_conn() as conn:
            conn.execute(
                """UPDATE backup_runs
                   SET finished_at=?, status=?, summary=?, detail=? WHERE id=?""",
                (_now(), status, summary, detail, run_id),
            )
        _pending.discard(vmid)
