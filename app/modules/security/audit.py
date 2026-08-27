"""Security module: SSH/fail2ban/sudo per guest, via the same restricted
agent action (`security-audit`) the rest of the app uses — never a free
command. Result always cached (`security_checks`); never requested on
every visit to the guest page, only on "Check now" or the weekly
scheduler sweep."""
import datetime as dt

from ...core import agent, db

FIELDS = [
    "ssh_active",
    "password_auth",
    "permit_root_login",
    "authorized_keys_files",
    "fail2ban",
    "sudo_nopasswd_lines",
    "listening_ports",
]


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _parse(raw: str) -> dict:
    out = {}
    for line in raw.splitlines():
        key, sep, value = line.partition("=")
        if sep and key in FIELDS:
            out[key] = value
    return out


def run_audit(vmid: int) -> dict:
    with db.get_conn() as conn:
        guest = conn.execute("SELECT node FROM guests WHERE vmid=?", (vmid,)).fetchone()
    if guest is None:
        return {}

    res = agent.get_security_audit(guest["node"], vmid)
    parsed = _parse(res.output) if res.ok else {}
    checked_at = _now()

    with db.get_conn() as conn:
        conn.execute(
            """INSERT INTO security_checks
               (vmid, checked_at, ssh_active, password_auth, permit_root_login,
                authorized_keys_files, fail2ban, sudo_nopasswd_lines, listening_ports, raw)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(vmid) DO UPDATE SET
                 checked_at=excluded.checked_at,
                 ssh_active=excluded.ssh_active,
                 password_auth=excluded.password_auth,
                 permit_root_login=excluded.permit_root_login,
                 authorized_keys_files=excluded.authorized_keys_files,
                 fail2ban=excluded.fail2ban,
                 sudo_nopasswd_lines=excluded.sudo_nopasswd_lines,
                 listening_ports=excluded.listening_ports,
                 raw=excluded.raw""",
            (
                vmid, checked_at,
                parsed.get("ssh_active"), parsed.get("password_auth"),
                parsed.get("permit_root_login"), parsed.get("authorized_keys_files"),
                parsed.get("fail2ban"), parsed.get("sudo_nopasswd_lines"),
                parsed.get("listening_ports"), res.output,
            ),
        )
    return parsed


def get_cached_check(vmid: int):
    with db.get_conn() as conn:
        return conn.execute("SELECT * FROM security_checks WHERE vmid=?", (vmid,)).fetchone()


def audit_all_guests() -> None:
    """Weekly sweep — one guest at a time, same reason as run_all_due in
    the update module: don't hammer a host with several pct exec/SSH
    calls at once."""
    with db.get_conn() as conn:
        vmids = [r["vmid"] for r in conn.execute("SELECT vmid FROM guests").fetchall()]
    for vmid in vmids:
        run_audit(vmid)


def evaluate(check, *, require_sudo_locked: bool = True) -> dict:
    """Turn a raw security_checks row into display-ready fields: values
    plus a pass/fail flag per field and one overall_ok. `ssh_active`,
    `authorized_keys_files` and `listening_ports` are informational only
    (their presence/absence isn't itself good or bad) and don't count
    toward overall_ok — only password_auth, permit_root_login, fail2ban
    and sudo_nopasswd_lines do.

    For VMs updated via direct SSH (`VM_GUESTS`), passwordless sudo for
    apt/sshd is required by lxc-manager — `require_sudo_locked=False`
    keeps the sudo line visible but excludes it from overall_ok."""
    if check is None:
        return {"checked": False}

    password_auth_ok = check["password_auth"] == "no"
    # "without-password" is sshd's older name for "prohibit-password" —
    # same meaning (root only via key, never via password), both count.
    permit_root_ok = check["permit_root_login"] in ("no", "prohibit-password", "without-password")
    fail2ban_ok = check["fail2ban"] == "active"
    sudo_ok = (check["sudo_nopasswd_lines"] or 0) == 0
    authorized_keys_ok = (check["authorized_keys_files"] or 0) > 0

    overall_bits = [password_auth_ok, permit_root_ok, fail2ban_ok, authorized_keys_ok]
    if require_sudo_locked:
        overall_bits.append(sudo_ok)

    return {
        "checked": True,
        "checked_at": check["checked_at"],
        "ssh_active": check["ssh_active"],
        "password_auth": check["password_auth"],
        "password_auth_ok": password_auth_ok,
        "permit_root_login": check["permit_root_login"],
        "permit_root_ok": permit_root_ok,
        "authorized_keys_files": check["authorized_keys_files"],
        "authorized_keys_ok": authorized_keys_ok,
        "fail2ban": check["fail2ban"],
        "fail2ban_ok": fail2ban_ok,
        "sudo_nopasswd_lines": check["sudo_nopasswd_lines"],
        "sudo_ok": sudo_ok,
        "listening_ports": check["listening_ports"],
        "overall_ok": all(overall_bits),
    }


def get_evaluated(vmid: int) -> dict:
    from ...core import config
    return evaluate(
        get_cached_check(vmid),
        require_sudo_locked=vmid not in config.VM_GUESTS,
    )
