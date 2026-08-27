"""Minimal Proxmox API client: only what lxc-manager needs (guest
inventory and triggering an on-demand vzdump). The token's permissions
are scoped to VM.Audit + VM.Backup — see the LxcManagerAPI role in
Proxmox."""
import datetime as dt
import re
import time

import httpx

from . import config

# Proxmox qemu `ostype` values that mean Windows. Anything else linux-ish
# is treated as Linux until /etc/os-release says otherwise (via SSH probe).
_WINDOWS_OSTYPES = {
    "wxp", "w2k", "w2k3", "w2k8", "wvista", "win7", "win8", "win10", "win11",
}


def _headers():
    return {
        "Authorization": f"PVEAPIToken={config.PVE_TOKEN_ID}={config.PVE_TOKEN_SECRET}"
    }


def _guest_config(node: str, vmid: int, guest_type: str) -> dict:
    kind = "lxc" if guest_type == "lxc" else "qemu"
    r = httpx.get(
        f"{config.PVE_API_URL}/api2/json/nodes/{node}/{kind}/{vmid}/config",
        headers=_headers(),
        verify=config.PVE_VERIFY_SSL,
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["data"]


def _ip_from_config(cfg: dict, vmid: int) -> str:
    if vmid in config.VM_GUESTS:
        return config.VM_GUESTS[vmid]["host"]
    net0 = cfg.get("net0", "")
    m = re.search(r"ip=([0-9.]+)", net0)
    return m.group(1) if m else "DHCP"


def classify_os(guest_type: str, pve_ostype: str = None) -> dict:
    """Map Proxmox guest type + qemu ostype → os_family / provisional os_id
    / whether package updates are known-supported. LXC in this homelab
    are Debian; VMs need an SSH probe (see agent.probe_vm_os) to refine
    linux → debian/ubuntu vs something apt can't handle."""
    if guest_type == "lxc":
        return {
            "os_family": "linux",
            "os_id": "debian",
            "update_supported": 1,
        }
    ostype = (pve_ostype or "").lower()
    if ostype in _WINDOWS_OSTYPES or ostype.startswith("win"):
        return {
            "os_family": "windows",
            "os_id": "windows",
            "update_supported": 0,
        }
    if ostype in ("l24", "l26", ""):
        # Linux VM — apt support unknown until probed.
        return {
            "os_family": "linux",
            "os_id": "linux",
            "update_supported": 0,
        }
    return {
        "os_family": "other",
        "os_id": ostype or "unknown",
        "update_supported": 0,
    }


def discover_guests() -> list[dict]:
    """Cluster-wide guests tagged auto-update, with app_type and OS
    classification already resolved. For Linux VMs listed in VM_GUESTS,
    refines os_id via SSH (/etc/os-release) so apt only runs on
    Debian/Ubuntu."""
    # Imported lazily: agent imports config; keep proxmox usable alone.
    from . import agent

    r = httpx.get(
        f"{config.PVE_API_URL}/api2/json/cluster/resources",
        params={"type": "vm"},
        headers=_headers(),
        verify=config.PVE_VERIFY_SSL,
        timeout=15,
    )
    r.raise_for_status()
    out = []
    for item in r.json()["data"]:
        tags = item.get("tags", "")
        tag_list = [t for t in tags.split(";") if t]
        if config.REQUIRED_TAG not in tag_list:
            continue
        app_type = "unknown"
        for t in tag_list:
            if t in config.TAG_APP_TYPE:
                app_type = config.TAG_APP_TYPE[t]
                break

        vmid = item["vmid"]
        node = item["node"]
        guest_type = item["type"]  # lxc | qemu
        pve_ostype = None
        try:
            cfg = _guest_config(node, vmid, guest_type)
            ip = _ip_from_config(cfg, vmid)
            if guest_type == "qemu":
                pve_ostype = cfg.get("ostype")
        except httpx.HTTPError:
            ip = config.VM_GUESTS[vmid]["host"] if vmid in config.VM_GUESTS else "?"

        os_info = classify_os(guest_type, pve_ostype)
        if (
            guest_type == "qemu"
            and os_info["os_family"] == "linux"
            and vmid in config.VM_GUESTS
        ):
            probed = agent.probe_vm_os(vmid)
            if probed is not None:
                os_info = probed

        out.append(
            {
                "vmid": vmid,
                "node": node,
                "name": item.get("name", str(vmid)),
                "type": guest_type,
                "app_type": app_type,
                "tags": tags,
                "maxmem": item.get("maxmem", 0),
                "maxcpu": item.get("maxcpu", 0),
                "ip": ip,
                "os_family": os_info["os_family"],
                "os_id": os_info["os_id"],
                "update_supported": int(os_info["update_supported"]),
            }
        )
    return out


def list_pbs_storages() -> list[str]:
    """Storage ids with type=pbs, sorted for a stable UI order. Used when
    LXCMGR_PBS_STORAGES is unset so a new PBS backend appears without a
    config change. Falls back to the on-demand destination alone if the
    API call fails — better a partial panel than a hard crash."""
    if config.PBS_STORAGES is not None:
        return list(config.PBS_STORAGES)
    try:
        r = httpx.get(
            f"{config.PVE_API_URL}/api2/json/storage",
            headers=_headers(),
            verify=config.PVE_VERIFY_SSL,
            timeout=15,
        )
        r.raise_for_status()
        names = sorted(
            s["storage"] for s in r.json()["data"] if s.get("type") == "pbs"
        )
        return names or [config.PBS_STORAGE]
    except httpx.HTTPError:
        return [config.PBS_STORAGE]


def list_backups(node: str, storage: str = config.PBS_STORAGE) -> list[dict]:
    """Raw backup listing for one PBS storage — not scoped to `node` in
    practice (the storage is cluster-wide), `node` is only which host
    routes the request. One call sees every guest's backups on both
    hosts for that storage."""
    r = httpx.get(
        f"{config.PVE_API_URL}/api2/json/nodes/{node}/storage/{storage}/content",
        params={"content": "backup"},
        headers=_headers(),
        verify=config.PVE_VERIFY_SSL,
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["data"]


def trigger_snapshot(
    node: str,
    vmid: int,
    storage: str = config.PBS_STORAGE,
    notes: str = "lxc-manager pre-update snapshot",
) -> str:
    """On-demand vzdump to one storage. Returns the task's UPID."""
    r = httpx.post(
        f"{config.PVE_API_URL}/api2/json/nodes/{node}/vzdump",
        params={
            "vmid": vmid,
            "storage": storage,
            "mode": "snapshot",
            "notes-template": notes,
        },
        headers=_headers(),
        verify=config.PVE_VERIFY_SSL,
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["data"]


def wait_task(node: str, upid: str, timeout_s: int = 300) -> bool:
    deadline = dt.datetime.now() + dt.timedelta(seconds=timeout_s)
    while dt.datetime.now() < deadline:
        r = httpx.get(
            f"{config.PVE_API_URL}/api2/json/nodes/{node}/tasks/{upid}/status",
            headers=_headers(),
            verify=config.PVE_VERIFY_SSL,
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()["data"]
        if data["status"] == "stopped":
            return data.get("exitstatus") == "OK"
        time.sleep(3)
    return False


def vzdump_all_storages(
    node: str,
    vmid: int,
    timeout_s: int = 900,
    notes: str = "lxc-manager on-demand backup",
):
    """Run vzdump sequentially against every discovered PBS storage.
    Parallel dumps of the same guest would contend on the snapshot
    freeze. Returns [(storage, ok), ...]."""
    results = []
    for storage in list_pbs_storages():
        upid = trigger_snapshot(node, vmid, storage, notes=notes)
        ok = wait_task(node, upid, timeout_s=timeout_s)
        results.append((storage, ok))
    return results
