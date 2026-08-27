"""The real sequence of a run: backup-all-PBS -> packages (if supported)
-> app-update/version -> health -> record -> Telegram. One guest at a
time. Package updates only run on LXC or on Debian/Ubuntu VMs;
Windows and other OS families get the backups and skip the rest of the
OS layer."""
import datetime as dt

from ...core import agent, config, db, proxmox, telegram

# In-memory — single-process app. Set in the POST handler before the
# background task is queued so the Update button disables immediately.
_pending: set[int] = set()


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def is_pending(vmid: int) -> bool:
    return vmid in _pending


def mark_pending(vmid: int) -> None:
    _pending.add(vmid)


def run_guest(vmid: int) -> None:
    _pending.add(vmid)
    try:
        _run_guest(vmid)
    finally:
        _pending.discard(vmid)


def _run_guest(vmid: int) -> None:
    with db.get_conn() as conn:
        guest = conn.execute("SELECT * FROM guests WHERE vmid = ?", (vmid,)).fetchone()
    if guest is None:
        return

    with db.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO runs (vmid, started_at, status) VALUES (?, ?, 'running')",
            (vmid, _now()),
        )
        run_id = cur.lastrowid

    lines = [f"lxc-manager · {guest['name']} ({vmid})"]
    lines.append(
        f"guest: {guest['type']} · os={guest['os_id']} ({guest['os_family']}) · "
        f"updates={'yes' if guest['update_supported'] else 'no'}"
    )
    ok_overall = True
    is_vm = guest["type"] == "qemu" or vmid in config.VM_GUESTS

    try:
        # 1. safety backup on every PBS storage (same as Backups → Back up now)
        dump_results = proxmox.vzdump_all_storages(
            guest["node"],
            vmid,
            timeout_s=900,
            notes="lxc-manager pre-update snapshot",
        )
        if not dump_results:
            lines.append("backup: no PBS storages discovered")
            ok_overall = False
        for storage, snap_ok in dump_results:
            lines.append(f"backup → {storage}: {'ok' if snap_ok else 'FAILED'}")
            if not snap_ok:
                ok_overall = False

        # 2. system packages — only when the OS is known to support it
        if not guest["update_supported"]:
            reason = guest["os_family"]
            if reason == "windows":
                lines.append("os-update: skipped (Windows — OS updates disabled)")
            else:
                lines.append(
                    f"os-update: skipped (unsupported os_id={guest['os_id']})"
                )
        elif is_vm:
            if vmid not in config.VM_GUESTS:
                lines.append("os-update: skipped (VM not in VM_GUESTS — no SSH target)")
                ok_overall = False
            else:
                res = agent.run_vm_apt_upgrade(vmid)
                lines.append(f"apt-upgrade: {'ok' if res.ok else 'FAILED'}")
                if not res.ok:
                    ok_overall = False
                lines.append(res.output[-800:])
        else:
            res = agent.run_lxc_action(guest["node"], vmid, "apt-upgrade")
            lines.append(f"apt-upgrade: {'ok' if res.ok else 'FAILED'}")
            if not res.ok:
                ok_overall = False
            lines.append(res.output[-800:])

        # 3. app layer (per type, never blindly) — LXC only
        app_type = guest["app_type"]
        mode = config.APP_UPDATE_MODE.get(app_type, "check-only")
        if not is_vm and mode == "auto":
            res = agent.run_lxc_action(guest["node"], vmid, "app-update")
            lines.append(f"app-update ({app_type}): {'ok' if res.ok else 'FAILED'}")
            lines.append(res.output[-400:])
        elif not is_vm and mode == "check-only":
            res = agent.run_lxc_action(guest["node"], vmid, "app-version")
            lines.append(f"app-version ({app_type}): {res.output.strip()}")

        # 4. health — LXC only
        if not is_vm and app_type != "unknown":
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
            "UPDATE runs SET finished_at=?, status=?, summary=?, detail=? WHERE id=?",
            (_now(), status, summary, detail, run_id),
        )

    telegram.notify(detail)


def run_all_due(vmids: list[int]) -> None:
    for vmid in vmids:
        run_guest(vmid)
