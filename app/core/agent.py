"""Runs actions against a guest: LXC via SSH to the Proxmox host it
lives on (which forwards to the restricted command agent, pct exec
underneath), VM via direct SSH to the guest itself. Never runs an
arbitrary command: only the actions already whitelisted by
lxc-manager-agent.sh on the host side, or the fixed `apt` commands for
VMs."""
import subprocess

from . import config


class ActionResult:
    def __init__(self, ok: bool, output: str):
        self.ok = ok
        self.output = output


def _ssh(host: str, user: str, command: str, timeout: int = 180) -> ActionResult:
    cmd = [
        "ssh",
        "-i", config.SSH_KEY_PATH,
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", f"ConnectTimeout=10",
        f"{user}@{host}",
        command,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        ok = proc.returncode == 0
        output = proc.stdout + proc.stderr
        return ActionResult(ok, output.strip())
    except subprocess.TimeoutExpired:
        return ActionResult(False, f"timeout after {timeout}s")


def run_lxc_action(node: str, vmid: int, action: str) -> ActionResult:
    host = config.NODE_HOSTS[node]
    return _ssh(host, "root", f"{action} {vmid}")


def run_vm_apt_upgrade(vmid: int) -> ActionResult:
    g = config.VM_GUESTS[vmid]
    return _ssh(
        g["host"], g["user"],
        "sudo -n apt-get update -qq && sudo -n DEBIAN_FRONTEND=noninteractive apt-get -y upgrade",
        timeout=600,
    )


def run_vm_apt_list(vmid: int) -> ActionResult:
    g = config.VM_GUESTS[vmid]
    return _ssh(g["host"], g["user"], "apt list --upgradable 2>/dev/null")


def probe_vm_os(vmid: int):
    """Read /etc/os-release over SSH. Returns the same shape as
    proxmox.classify_os(), or None if the guest isn't reachable / isn't
    in VM_GUESTS. Only Debian and Ubuntu get update_supported=1."""
    if vmid not in config.VM_GUESTS:
        return None
    g = config.VM_GUESTS[vmid]
    res = _ssh(
        g["host"],
        g["user"],
        # Portable enough for Debian/Ubuntu; empty on Windows (no bash).
        ". /etc/os-release 2>/dev/null; printf 'ID=%s\\nID_LIKE=%s\\n' \"$ID\" \"$ID_LIKE\"",
        timeout=15,
    )
    if not res.ok:
        return None
    fields = {}
    for line in res.output.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            fields[k.strip()] = v.strip().strip('"')
    os_id = (fields.get("ID") or "").lower()
    id_like = (fields.get("ID_LIKE") or "").lower()
    tokens = {os_id, *id_like.split()}
    debianish = bool(tokens & {"debian", "ubuntu"})
    if not os_id:
        return None
    return {
        "os_family": "linux",
        "os_id": os_id,
        "update_supported": 1 if debianish else 0,
    }


def get_kernel(node: str, vmid: int) -> str:
    """Kernel version. For an LXC it's the host's (containers share the
    kernel, they don't have their own) — informational, not specific to
    the guest. For the VM it's that guest's real kernel."""
    if vmid in config.VM_GUESTS:
        g = config.VM_GUESTS[vmid]
        res = _ssh(g["host"], g["user"], "uname -r", timeout=10)
    else:
        res = run_lxc_action(node, vmid, "sys-info")
    return res.output if res.ok else "—"


# Same fields, same order, as the "security-audit" case in
# agent/lxc-manager-agent.sh (LXC side) — keep the two in sync by hand
# if a field is added. `sudo -n` here because this path runs as a
# regular user (the VM doesn't go through the host's restricted agent,
# which already runs as root via pct exec); if the VM's sudoers doesn't
# allow it, the field comes back empty instead of breaking the rest.
SECURITY_AUDIT_SCRIPT = """
ssh_active=$(systemctl is-active ssh 2>/dev/null || systemctl is-active sshd 2>/dev/null || echo unknown)
pw_auth=$(sudo -n sshd -T 2>/dev/null | awk '/^passwordauthentication /{print $2}')
permit_root=$(sudo -n sshd -T 2>/dev/null | awk '/^permitrootlogin /{print $2}')
keys=0
for f in "$HOME/.ssh/authorized_keys" /home/*/.ssh/authorized_keys; do [ -s "$f" ] && keys=$((keys+1)); done
f2b=$(systemctl is-active fail2ban 2>/dev/null || echo not-installed)
sudo_nopasswd=$(sudo -n grep -rhE "NOPASSWD" /etc/sudoers /etc/sudoers.d/ 2>/dev/null | grep -vc "^#")
ports=$(ss -tlnH 2>/dev/null | awk '{print $4}' | grep -oE '[0-9]+$' | sort -un | paste -sd, -)
echo "ssh_active=$ssh_active"
echo "password_auth=$pw_auth"
echo "permit_root_login=$permit_root"
echo "authorized_keys_files=$keys"
echo "fail2ban=$f2b"
echo "sudo_nopasswd_lines=$sudo_nopasswd"
echo "listening_ports=$ports"
""".strip()


def get_security_audit(node: str, vmid: int) -> ActionResult:
    """Read-only audit (SSH/fail2ban/sudo/ports). LXC via the host
    agent's restricted `security-audit` action; VM via direct SSH with
    the same checks."""
    if vmid in config.VM_GUESTS:
        g = config.VM_GUESTS[vmid]
        return _ssh(g["host"], g["user"], SECURITY_AUDIT_SCRIPT, timeout=30)
    return run_lxc_action(node, vmid, "security-audit")
