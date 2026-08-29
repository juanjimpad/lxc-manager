"""Test env must be set before app.core.config is imported."""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="hlmgr-test-"))
os.environ["HLMGR_TESTING"] = "1"
os.environ["HLMGR_SESSION_SECRET"] = "test-secret-not-for-prod-use-32b"
os.environ["HLMGR_DB_PATH"] = str(_TMP / "test.db")
os.environ["HLMGR_ADMIN_USER"] = "admin"
os.environ["HLMGR_ADMIN_PASSWORD"] = "test-password-12"
os.environ.pop("LXCMGR_PVE_API_URL", None)
os.environ.pop("HLMGR_PVE_API_URL", None)
os.environ.pop("LXCMGR_PVE_TOKEN_ID", None)
os.environ.pop("HLMGR_PVE_TOKEN_ID", None)
os.environ.pop("LXCMGR_PVE_TOKEN_SECRET", None)
os.environ.pop("HLMGR_PVE_TOKEN_SECRET", None)

import pytest
from fastapi.testclient import TestClient

from app.core import auth, client_auth, cluster, db
from app.main import app


@pytest.fixture(autouse=True)
def _reset_db():
    path = Path(os.environ["HLMGR_DB_PATH"])
    if path.exists():
        path.unlink()
    wal = Path(str(path) + "-wal")
    shm = Path(str(path) + "-shm")
    if wal.exists():
        wal.unlink()
    if shm.exists():
        shm.unlink()
    db.init_db()
    cluster.ensure_cluster_key()
    auth.seed_admin_if_empty()
    with db.get_conn() as conn:
        conn.execute("DELETE FROM client_jobs")
        conn.execute("DELETE FROM client_resources")
        conn.execute("DELETE FROM clients")
        conn.execute("DELETE FROM settings")
    client_auth._seen_nonces.clear()
    yield


@pytest.fixture
def client(_reset_db):
    with TestClient(app) as c:
        yield c


def csrf_from(html: str) -> str:
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert m, "csrf_token not found in HTML"
    return m.group(1)


def login(client: TestClient) -> None:
    r = client.get("/login")
    assert r.status_code == 200
    token = csrf_from(r.text)
    r = client.post(
        "/login",
        data={
            "username": "admin",
            "password": "test-password-12",
            "csrf_token": token,
            "next": "/",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
