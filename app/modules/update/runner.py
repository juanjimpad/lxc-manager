"""The real sequence of a run: optional backup-all-PBS -> packages (if
supported) -> app-update/version -> health -> record -> Telegram. One
guest at a time. Package updates only run on LXC or on Debian/Ubuntu
VMs; Windows and other OS families skip the OS layer.

`phase` on the runs row is updated as we go so the guest page can show
what is happening (backup can take a long time on large guests)."""
import datetime as dt

from ...core import agent, config, db, telegram
from ..backups import status as backup_status

# In-memory — single-process app. Set in the POST handler before the
# background task is queued so the Update button disables immediately.
_pending: set[int] = set()


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def is_pending(vmid: int) -> bool:
    return vmid in _pending


def mark_pending(vmid: int) -> None:
    _pending.add(vmid)


def _set_phase(run_id: int, phase: str, lines: list[str] | None = None) -> None:
    """Persist the current step so polling UIs can show live progress."""
    with db.get_conn() as conn:
        if lines is None:
            conn.execute("UPDATE runs SET phase=? WHERE id=?", (phase, run_id))
        else:
            conn.execute(
                "UPDATE runs SET phase=?, detail=? WHERE id=?",
                (phase, "\n".join(lines), run_id),
            )


def run_guest(vmid: int, with_backup: bool = True) -> None:
    """Run an update. Scheduled jobs keep with_backup=True; manual Run now
    can skip the pre-update vzdump (large guests like Immich take ages)."""
    _pending.add(vmid)
    try:
        _run_guest(vmid, with_backup=with_backup)
    finally:
        _pending.discard(vmid)


def _run_guest(vmid: int, with_backup: bool = True) -> None:
    with db.get_conn() as conn:
        guest = conn.execute("SELECT * FROM guests WHERE vmid = ?", (vmid,)).fetchone()
    if guest is None:
        return

    with db.get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO runs (vmid, started_at, status, phase)
               VALUES (?, ?, 'running', ?)""",
            (vmid, _now(), "starting"),
        )
        run_id = cur.lastrowid

    lines = [f"lxc-manager · {guest['name']} ({vmid})"]
    lines.append(
        f"guest: {guest['type']} · os={guest['os_id']} ({guest['os_family']}) · "
        f"updates={'yes' if guest['update_supported'] else 'no'} · "
        f"pre-backup={'yes' if with_backup else 'no'}"
    )
    ok_overall = True
    is_vm = guest["type"] == "qemu" or vmid in config.VM_GUESTS
    _set_phase(run_id, "starting", lines)

    try:
        # 1. optional safety backup — same path as Backups → "Back up now"
        # so backup_runs + backup_status (and PBS verify) stay in sync.
        if with_backup:
            _set_phase(run_id, "backup (backups module)", lines)
            backup_ok = backup_status.run_backup_now(
                vmid, notes="lxc-manager pre-update snapshot"
            )
            with db.get_conn() as conn:
                brow = conn.execute(
                    """SELECT status, detail FROM backup_runs
                       WHERE vmid=? ORDER BY id DESC LIMIT 1""",
                    (vmid,),
                ).fetchone()
            if brow and brow["detail"]:
                for line in brow["detail"].splitlines()[1:]:
                    lines.append(line)
            lines.append(
                "backup: ok (recorded in backups module)"
                if backup_ok
                else "backup: FAILED (see backups history)"
            )
            if not backup_ok:
                ok_overall = False
            _set_phase(run_id, "backup done", lines)
        else:
            lines.append("backup: skipped (not requested)")
            _set_phase(run_id, "backup skipped", lines)

        # 2. system packages — only when the OS is known to support it
        if not guest["update_supported"]:
            reason = guest["os_family"]
            if reason == "windows":
                lines.append("os-update: skipped (Windows — OS updates disabled)")
            else:
                lines.append(
                    f"os-update: skipped (unsupported os_id={guest['os_id']})"
                )
            _set_phase(run_id, "os-update skipped", lines)
        elif is_vm:
            if vmid not in config.VM_GUESTS:
                lines.append("os-update: skipped (VM not in VM_GUESTS — no SSH target)")
                ok_overall = False
                _set_phase(run_id, "os-update skipped", lines)
            else:
                _set_phase(run_id, "apt-upgrade", lines)
                res = agent.run_vm_apt_upgrade(vmid)
                lines.append(f"apt-upgrade: {'ok' if res.ok else 'FAILED'}")
                if not res.ok:
                    ok_overall = False
                lines.append(res.output[-800:])
        else:
            _set_phase(run_id, "apt-upgrade", lines)
            res = agent.run_lxc_action(guest["node"], vmid, "apt-upgrade")
            lines.append(f"apt-upgrade: {'ok' if res.ok else 'FAILED'}")
            if not res.ok:
                ok_overall = False
            lines.append(res.output[-800:])

        # 3. app layer (per type, never blindly) — LXC only
        app_type = guest["app_type"]
        mode = config.APP_UPDATE_MODE.get(app_type, "check-only")
        if not is_vm and mode == "auto":
            _set_phase(run_id, f"app-update ({app_type})", lines)
            res = agent.run_lxc_action(guest["node"], vmid, "app-update")
            lines.append(f"app-update ({app_type}): {'ok' if res.ok else 'FAILED'}")
            lines.append(res.output[-400:])
        elif not is_vm and mode == "check-only":
            _set_phase(run_id, f"app-version ({app_type})", lines)
            res = agent.run_lxc_action(guest["node"], vmid, "app-version")
            lines.append(f"app-version ({app_type}): {res.output.strip()}")

        # 4. health — LXC only
        if not is_vm and app_type != "unknown":
            _set_phase(run_id, "health-check", lines)
            res = agent.run_lxc_action(guest["node"], vmid, "health-check")
            healthy = res.ok and res.output.strip() not in ("", "000")
            lines.append(f"health-check: {res.output.strip()} ({'ok' if healthy else 'CHECK'})")
            if not healthy:
                ok_overall = False

        status = "ok" if ok_overall else "failed"
    except Exception as exc:  # noqa: BLE001 - a failing run must not take the scheduler down
        status = "failed"
        lines.append(f"exception: {exc}")

    summary = lines[0] + " → " + status
    detail = "\n".join(lines)

    with db.get_conn() as conn:
        conn.execute(
            """UPDATE runs SET finished_at=?, status=?, summary=?, detail=?, phase=?
               WHERE id=?""",
            (_now(), status, summary, detail, status, run_id),
        )

    telegram.notify(detail)


def run_all_due(vmids: list[int]) -> None:
    for vmid in vmids:
        run_guest(vmid, with_backup=True)
