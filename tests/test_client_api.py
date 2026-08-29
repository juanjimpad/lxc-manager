from __future__ import annotations

import json
import time

from app.core import client_auth, cluster
from tests.conftest import login


def _headers(method: str, path: str, body: bytes, client_id: str = "testhost01") -> dict:
    key = cluster.load_key()
    ts = str(int(time.time()))
    nonce = f"nonce-{time.time_ns()}"
    sig = client_auth.sign(key, ts, nonce, method, path, body)
    return {
        "X-Homelab-Timestamp": ts,
        "X-Homelab-Nonce": nonce,
        "X-Homelab-Signature": sig,
        "X-Homelab-Client": client_id,
        "Content-Type": "application/json",
    }


def test_heartbeat_requires_signature(client):
    r = client.post("/api/v1/heartbeat", json={"hostname": "x"})
    assert r.status_code == 422  # missing HMAC headers


def test_heartbeat_rejects_bad_signature(client):
    body = json.dumps({"hostname": "x"}).encode()
    headers = _headers("POST", "/api/v1/heartbeat", body)
    headers["X-Homelab-Signature"] = "00" * 32
    r = client.post("/api/v1/heartbeat", content=body, headers=headers)
    assert r.status_code == 401


def test_heartbeat_rejects_replayed_nonce(client):
    body = json.dumps({"hostname": "pi-dns", "platform": "raspbian"}).encode()
    headers = _headers("POST", "/api/v1/heartbeat", body, "pi-dns")
    r1 = client.post("/api/v1/heartbeat", content=body, headers=headers)
    assert r1.status_code == 200
    r2 = client.post("/api/v1/heartbeat", content=body, headers=headers)
    assert r2.status_code == 401


def test_heartbeat_registers_client_and_dashboard_shows_it(client):
    payload = {
        "name": "dell-5060",
        "hostname": "dell-5060",
        "platform": "proxmox",
        "os_id": "proxmox",
        "ip": "192.168.1.6",
        "capabilities": ["apt", "lxc", "docker"],
        "cpu_percent": 12.5,
        "mem_used": 4 * 1024**3,
        "mem_total": 16 * 1024**3,
        "temp_c": 48.0,
        "resources": [
            {"kind": "lxc", "id": "101", "name": "npm", "status": "running"},
            {"kind": "docker", "id": "vaultwarden", "name": "vaultwarden", "status": "running"},
        ],
    }
    body = json.dumps(payload).encode()
    headers = _headers("POST", "/api/v1/heartbeat", body, "dell-5060")
    r = client.post("/api/v1/heartbeat", content=body, headers=headers)
    assert r.status_code == 200
    assert r.json()["ok"] is True

    login(client)
    dash = client.get("/")
    assert dash.status_code == 200
    assert "dell-5060" in dash.text
    assert "online" in dash.text
    assert "48" in dash.text

    detail = client.get("/client/dell-5060")
    assert detail.status_code == 200
    assert "vaultwarden" in detail.text
    assert "npm" in detail.text
