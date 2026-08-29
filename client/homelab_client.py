#!/usr/bin/env python3
"""homelab-client — talks to homelab-manager with the cluster HMAC key.

Stdlib only so it installs on Debian, Raspberry Pi OS, Proxmox, Synology
DSM (python3) and Unraid without a venv. Capabilities are detected at
runtime: apt, docker, lxc/qemu (Proxmox), synology, unraid.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import platform
import random
import re
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

CLIENT_VERSION = "2.0.0"
HEARTBEAT_S = 20
JOB_TIMEOUT_S = 900


# --- HMAC (must match app/core/client_auth.py) -----------------------------

def sign(key: str, timestamp: str, nonce: str, method: str, path: str, body: bytes) -> str:
    msg = f"{timestamp}\n{nonce}\n{method.upper()}\n{path}\n".encode() + body
    return hmac.new(key.encode(), msg, hashlib.sha256).hexdigest()


def _request(url: str, key: str, client_id: str, method: str, path: str, payload=None, timeout: int = 20):
    body = b"" if payload is None else json.dumps(payload).encode()
    ts = str(int(time.time()))
    nonce = secrets.token_hex(16)
    sig = sign(key, ts, nonce, method, path, body)
    headers = {
        "Content-Type": "application/json",
        "X-Homelab-Timestamp": ts,
        "X-Homelab-Nonce": nonce,
        "X-Homelab-Signature": sig,
        "X-Homelab-Client": client_id,
        "User-Agent": f"homelab-client/{CLIENT_VERSION}",
    }
    req = urllib.request.Request(url.rstrip("/") + path, data=body or None, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return json.loads(raw.decode() or "{}")
    except urllib.error.HTTPError as exc:
        err = exc.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {err}") from exc


# --- collectors ------------------------------------------------------------

def _read(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _first_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("1.1.1.1", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return ""


def detect_platform() -> tuple[str, str, str]:
    """Returns (platform, os_id, os_version)."""
    if Path("/etc/pve").is_dir() or Path("/usr/bin/pveversion").exists():
        ver = _cmd(["pveversion", "-v"], timeout=5).splitlines()
        pve = ver[0].strip() if ver else ""
        return "proxmox", "proxmox", pve.split("/")[-1] if pve else ""
    if Path("/etc/synoinfo.conf").exists() or Path("/usr/syno").is_dir():
        return "synology", "dsm", _os_release("VERSION_ID") or ""
    if Path("/etc/unraid-version").exists() or Path("/usr/local/sbin/unraid-api").exists():
        ver = _read("/etc/unraid-version").strip() or _os_release("VERSION")
        return "unraid", "unraid", ver
    os_id = _os_release("ID") or "linux"
    version = _os_release("VERSION_ID") or ""
    if os_id in ("raspbian", "raspberrypi") or Path("/etc/rpi-issue").exists():
        return "raspbian", "raspbian", version
    if os_id == "ubuntu":
        return "ubuntu", "ubuntu", version
    if os_id == "debian":
        return "debian", "debian", version
    return "linux", os_id, version


def _os_release(key: str) -> str:
    for line in _read("/etc/os-release").splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip().strip('"')
    return ""


def _cmd(args: list[str], timeout: int = 15, input_text: str | None = None) -> str:
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            input=input_text,
            check=False,
        )
        return (proc.stdout or "") + (proc.stderr or "")
    except (OSError, subprocess.TimeoutExpired):
        return ""


def which(name: str) -> bool:
    from shutil import which as _which
    return _which(name) is not None


def detect_capabilities(plat: str) -> list[str]:
    caps = ["self-update"]
    if which("apt-get"):
        caps.append("apt")
    if which("docker"):
        caps.append("docker")
    if plat == "proxmox" or which("pct"):
        caps.append("lxc")
    if plat == "proxmox" or which("qm"):
        caps.append("qemu")
    if plat == "synology":
        caps.append("synology")
    if plat == "unraid":
        caps.append("unraid")
    return caps


def cpu_percent(sample_s: float = 0.15) -> float | None:
    def _snap():
        for line in _read("/proc/stat").splitlines():
            if line.startswith("cpu "):
                parts = [int(x) for x in line.split()[1:]]
                idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
                return idle, sum(parts)
        return None

    a = _snap()
    if not a:
        return None
    time.sleep(sample_s)
    b = _snap()
    if not b:
        return None
    idle = b[0] - a[0]
    total = b[1] - a[1]
    if total <= 0:
        return 0.0
    return round(max(0.0, min(100.0, 100.0 * (1.0 - idle / total))), 1)


def mem_info() -> tuple[int | None, int | None]:
    total = avail = None
    for line in _read("/proc/meminfo").splitlines():
        if line.startswith("MemTotal:"):
            total = int(line.split()[1]) * 1024
        elif line.startswith("MemAvailable:"):
            avail = int(line.split()[1]) * 1024
    if total is None:
        return None, None
    used = total - avail if avail is not None else None
    return used, total


def temp_c() -> float | None:
    temps = []
    thermal = Path("/sys/class/thermal")
    if thermal.is_dir():
        for zone in sorted(thermal.glob("thermal_zone*/temp")):
            raw = _read(str(zone)).strip()
            if raw.isdigit():
                val = int(raw)
                # millidegree C on Linux; some boards report whole degrees
                temps.append(val / 1000.0 if val > 200 else float(val))
    hwmon = Path("/sys/class/hwmon")
    if hwmon.is_dir():
        for f in hwmon.glob("hwmon*/temp*_input"):
            raw = _read(str(f)).strip()
            if raw.isdigit():
                val = int(raw)
                temps.append(val / 1000.0 if val > 200 else float(val))
    if not temps:
        out = _cmd(["vcgencmd", "measure_temp"], timeout=3)
        m = re.search(r"temp=([0-9.]+)", out)
        if m:
            temps.append(float(m.group(1)))
    if not temps:
        return None
    return round(sorted(temps)[len(temps) // 2], 1)


def uptime_seconds() -> int | None:
    raw = _read("/proc/uptime").split()
    if not raw:
        return None
    try:
        return int(float(raw[0]))
    except ValueError:
        return None


def loadavg() -> list[float]:
    try:
        return [round(x, 2) for x in os.getloadavg()]
    except OSError:
        return []


def collect_docker() -> list[dict]:
    if not which("docker"):
        return []
    out = _cmd(
        ["docker", "ps", "-a", "--format", "{{.Names}}\t{{.State}}\t{{.Image}}"],
        timeout=10,
    )
    rows = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        rows.append(
            {
                "kind": "docker",
                "id": parts[0],
                "name": parts[0],
                "status": parts[1],
                "extra": {"image": parts[2] if len(parts) > 2 else ""},
            }
        )
    return rows


def collect_proxmox_guests() -> list[dict]:
    rows = []
    if which("pct"):
        out = _cmd(["pct", "list"], timeout=10)
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 2:
                continue
            vmid, status = parts[0], parts[1]
            name = parts[-1] if len(parts) >= 3 else vmid
            rows.append({"kind": "lxc", "id": vmid, "name": name, "status": status})
    if which("qm"):
        out = _cmd(["qm", "list"], timeout=10)
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 3:
                continue
            # VMID NAME STATUS ...
            rows.append(
                {"kind": "qemu", "id": parts[0], "name": parts[1], "status": parts[2]}
            )
    return rows


def collect_heartbeat(name: str | None = None) -> dict:
    plat, os_id, os_version = detect_platform()
    used, total = mem_info()
    resources = []
    caps = detect_capabilities(plat)
    if "docker" in caps:
        resources.extend(collect_docker())
    if "lxc" in caps or "qemu" in caps:
        resources.extend(collect_proxmox_guests())
    host = socket.gethostname()
    return {
        "name": name or host,
        "hostname": host,
        "platform": plat,
        "os_id": os_id,
        "os_version": os_version,
        "ip": _first_ip(),
        "capabilities": caps,
        "cpu_percent": cpu_percent(),
        "mem_used": used,
        "mem_total": total,
        "temp_c": temp_c(),
        "uptime_seconds": uptime_seconds(),
        "loadavg": loadavg(),
        "version": CLIENT_VERSION,
        "kernel": platform.release(),
        "resources": resources,
    }


# --- jobs ------------------------------------------------------------------

def _run_ok(args: list[str], timeout: int = JOB_TIMEOUT_S) -> tuple[bool, str]:
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
        out = ((proc.stdout or "") + (proc.stderr or "")).strip()
        return proc.returncode == 0, out[-4000:]
    except subprocess.TimeoutExpired:
        return False, f"timeout after {timeout}s"
    except OSError as exc:
        return False, str(exc)


def run_sys_update() -> tuple[bool, str]:
    plat, _, _ = detect_platform()
    if plat == "synology" and which("synopkg"):
        return _run_ok(["synopkg", "upgradeall"], timeout=JOB_TIMEOUT_S)
    if plat == "unraid":
        # Community Applications / plugin check if present; otherwise nothing safe.
        if Path("/usr/local/emhttp/plugins/dynamix.plugin.manager/scripts/plugin").exists():
            return _run_ok(
                ["/usr/local/emhttp/plugins/dynamix.plugin.manager/scripts/plugin", "check"],
                timeout=120,
            )
        return False, "unraid: no plugin manager script found"
    if which("apt-get"):
        ok1, out1 = _run_ok(["apt-get", "update", "-qq"], timeout=180)
        env = os.environ.copy()
        env["DEBIAN_FRONTEND"] = "noninteractive"
        try:
            proc = subprocess.run(
                ["apt-get", "-y", "upgrade"],
                capture_output=True,
                text=True,
                timeout=JOB_TIMEOUT_S,
                check=False,
                env=env,
            )
            out2 = ((proc.stdout or "") + (proc.stderr or "")).strip()
            ok2 = proc.returncode == 0
        except subprocess.TimeoutExpired:
            return False, "apt-get upgrade timeout"
        return ok1 and ok2, (out1 + "\n" + out2).strip()[-4000:]
    return False, f"no sys-update handler for platform={plat}"


def run_docker_update(target: str = "") -> tuple[bool, str]:
    if not which("docker"):
        return False, "docker not installed"
    if target:
        inspect = _cmd(["docker", "inspect", "-f", "{{.Config.Image}}", target], timeout=15).strip()
        image = inspect.splitlines()[-1] if inspect.strip() else ""
        if not image:
            return False, f"could not resolve image for {target}"
        ok1, out1 = _run_ok(["docker", "pull", image], timeout=JOB_TIMEOUT_S)
        ok2, out2 = _run_ok(["docker", "update", "--restart-unless-stopped", target], timeout=30)
        # recreate is safer than update; compose users should prefer stack pull.
        ok3, out3 = _run_ok(["docker", "compose", "pull", target], timeout=JOB_TIMEOUT_S)
        return ok1 or ok3, "\n".join(x for x in (out1, out2, out3) if x)[-4000:]
    # Prefer compose projects; fall back to pulling every running image.
    ls = _cmd(["docker", "compose", "ls", "-q"], timeout=15)
    lines = []
    ok_all = True
    projects = [p for p in ls.splitlines() if p.strip()]
    if projects:
        for proj in projects:
            ok, out = _run_ok(
                ["docker", "compose", "-p", proj, "pull"],
                timeout=JOB_TIMEOUT_S,
            )
            lines.append(f"compose {proj} pull: {'ok' if ok else 'FAILED'}\n{out}")
            if ok:
                ok2, out2 = _run_ok(
                    ["docker", "compose", "-p", proj, "up", "-d"],
                    timeout=JOB_TIMEOUT_S,
                )
                lines.append(f"compose {proj} up: {'ok' if ok2 else 'FAILED'}\n{out2}")
                ok = ok and ok2
            ok_all = ok_all and ok
        return ok_all, "\n".join(lines)[-4000:]
    images = _cmd(
        ["docker", "ps", "--format", "{{.Image}}"],
        timeout=10,
    )
    seen = []
    for img in images.splitlines():
        img = img.strip()
        if not img or img in seen:
            continue
        seen.append(img)
        ok, out = _run_ok(["docker", "pull", img], timeout=JOB_TIMEOUT_S)
        lines.append(f"pull {img}: {'ok' if ok else 'FAILED'}\n{out}")
        ok_all = ok_all and ok
    if not seen:
        return True, "no running containers"
    return ok_all, "\n".join(lines)[-4000:]


def run_lxc_update(vmid: str) -> tuple[bool, str]:
    agent = Path("/usr/local/sbin/lxc-manager-agent.sh")
    if agent.exists():
        env = os.environ.copy()
        env["SSH_ORIGINAL_COMMAND"] = f"apt-upgrade {vmid}"
        try:
            proc = subprocess.run(
                [str(agent)],
                capture_output=True,
                text=True,
                timeout=JOB_TIMEOUT_S,
                env=env,
                check=False,
            )
            out = ((proc.stdout or "") + (proc.stderr or "")).strip()
            return proc.returncode == 0, out[-4000:]
        except subprocess.TimeoutExpired:
            return False, "timeout"
    if not which("pct"):
        return False, "pct not found"
    return _run_ok(
        [
            "pct",
            "exec",
            vmid,
            "--",
            "bash",
            "-c",
            "apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get -y upgrade",
        ],
        timeout=JOB_TIMEOUT_S,
    )


def run_self_update(install_dir: Path) -> tuple[bool, str]:
    if not (install_dir / ".git").is_dir():
        # Walk up to a clone that contains this file.
        here = Path(__file__).resolve().parent
        root = here.parent if (here.parent / ".git").is_dir() else here
        install_dir = root
    if not (install_dir / ".git").is_dir():
        return False, f"not a git checkout: {install_dir}"
    ok1, out1 = _run_ok(["git", "-C", str(install_dir), "fetch", "--quiet", "origin"], timeout=60)
    if not ok1:
        return False, out1
    ok2, out2 = _run_ok(
        ["git", "-C", str(install_dir), "merge", "--ff-only", "origin/main"],
        timeout=60,
    )
    return ok2, (out1 + "\n" + out2).strip()


def run_job(job: dict, install_dir: Path) -> tuple[bool, str]:
    kind = job.get("kind") or ""
    target = str(job.get("target") or "")
    if kind == "sys-update":
        return run_sys_update()
    if kind == "docker-update":
        return run_docker_update(target)
    if kind == "lxc-update":
        if not target:
            return False, "lxc-update requires a vmid target"
        return run_lxc_update(target)
    if kind == "self-update":
        return run_self_update(install_dir)
    return False, f"unknown job kind: {kind}"


# --- identity / loop -------------------------------------------------------

def load_or_create_id(path: Path) -> str:
    if path.is_file():
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
    path.parent.mkdir(parents=True, exist_ok=True)
    value = secrets.token_hex(8)
    path.write_text(value + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return value


def handle_job(url: str, key: str, client_id: str, job: dict, install_dir: Path) -> None:
    ok, detail = run_job(job, install_dir)
    _request(
        url,
        key,
        client_id,
        "POST",
        f"/api/v1/jobs/{job['id']}/result",
        {"ok": ok, "detail": detail, "summary": "ok" if ok else "failed"},
        timeout=30,
    )


def loop(url: str, key: str, client_id: str, name: str | None, install_dir: Path, once: bool = False) -> None:
    while True:
        try:
            payload = collect_heartbeat(name)
            resp = _request(url, key, client_id, "POST", "/api/v1/heartbeat", payload, timeout=30)
            job = (resp or {}).get("job")
            if job:
                handle_job(url, key, client_id, job, install_dir)
        except Exception as exc:  # noqa: BLE001 — stay alive
            print(f"[homelab-client] heartbeat failed: {exc}", file=sys.stderr)
        if once:
            return
        time.sleep(HEARTBEAT_S + random.random() * 3)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="homelab-client")
    parser.add_argument("--url", default=os.environ.get("HLMGR_URL", ""))
    parser.add_argument("--key", default=os.environ.get("HLMGR_KEY", ""))
    parser.add_argument("--name", default=os.environ.get("HLMGR_NAME", ""))
    parser.add_argument(
        "--id-file",
        default=os.environ.get("HLMGR_ID_FILE", "/var/lib/homelab-client/id"),
    )
    parser.add_argument("--once", action="store_true", help="one heartbeat then exit")
    parser.add_argument("--print-metrics", action="store_true")
    args = parser.parse_args(argv)

    if args.print_metrics:
        print(json.dumps(collect_heartbeat(args.name or None), indent=2, default=str))
        return 0
    if not args.url or not args.key:
        print("HLMGR_URL and HLMGR_KEY (or --url/--key) are required", file=sys.stderr)
        return 2
    client_id = load_or_create_id(Path(args.id_file))
    install_dir = Path(__file__).resolve().parent.parent
    loop(args.url, args.key, client_id, args.name or None, install_dir, once=args.once)
    return 0


if __name__ == "__main__":
    sys.exit(main())
