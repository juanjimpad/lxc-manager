from __future__ import annotations

import json
import time

from app.core import client_auth, cluster
from app.modules.clients import jobs, store
from tests.conftest import csrf_from, login


def _headers(method: str, path: str, body: bytes, client_id: str) -> dict:
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


def _heartbeat(client, client_id: str = "node-a"):
    payload = {
        "name": client_id,
        "hostname": client_id,
        "platform": "ubuntu",
        "capabilities": ["apt", "docker"],
        "cpu_percent": 3,
        "mem_used": 1024,
        "mem_total": 2048,
    }
    body = json.dumps(payload).encode()
    headers = _headers("POST", "/api/v1/heartbeat", body, client_id)
    r = client.post("/api/v1/heartbeat", content=body, headers=headers)
    assert r.status_code == 200
    return r


def test_enqueue_claim_complete(client):
    _heartbeat(client, "node-a")
    job_id = jobs.enqueue("node-a", "sys-update")
    claimed = jobs.claim_next("node-a")
    assert claimed is not None
    assert claimed["id"] == job_id
    assert claimed["status"] == "running"
    assert jobs.claim_next("node-a") is None
    done = jobs.complete(job_id, "node-a", ok=True, detail="apt ok")
    assert done["status"] == "ok"


def test_heartbeat_delivers_queued_job(client):
    _heartbeat(client, "node-b")
    jobs.enqueue("node-b", "docker-update", "vaultwarden")
    payload = {"name": "node-b", "hostname": "node-b", "platform": "ubuntu"}
    body = json.dumps(payload).encode()
    headers = _headers("POST", "/api/v1/heartbeat", body, "node-b")
    r = client.post("/api/v1/heartbeat", content=body, headers=headers)
    job = r.json()["job"]
    assert job["kind"] == "docker-update"
    assert job["target"] == "vaultwarden"

    result_body = json.dumps({"ok": True, "detail": "pulled"}).encode()
    path = f"/api/v1/jobs/{job['id']}/result"
    rh = _headers("POST", path, result_body, "node-b")
    rr = client.post(path, content=result_body, headers=rh)
    assert rr.status_code == 200
    assert rr.json()["job"]["status"] == "ok"


def test_ui_queues_job(client):
    _heartbeat(client, "node-c")
    login(client)
    page = client.get("/client/node-c")
    token = csrf_from(page.text)
    r = client.post(
        "/client/node-c/run",
        data={"csrf_token": token, "kind": "sys-update"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert "Queued" in r.text
    assert jobs.has_pending("node-c")


def test_unknown_kind_rejected():
    try:
        jobs.enqueue("x", "rm-rf")
        assert False, "should have raised"
    except ValueError:
        pass


def test_offline_watch_flips_status(client):
    _heartbeat(client, "node-d")
    row = store.get_client("node-d")
    assert row["online"] is True
    from app.core import db

    with db.get_conn() as conn:
        conn.execute(
            "UPDATE clients SET last_seen='2000-01-01T00:00:00', last_status='online' WHERE id='node-d'"
        )
    store.refresh_offline_status()
    row = store.get_client("node-d")
    assert row["online"] is False
    assert row["last_status"] == "offline"
