# Test env must be set before app.core.config / app.main import.
import os
import tempfile

_tmpdir = tempfile.mkdtemp(prefix="lxcmgr-test-")
os.environ.setdefault("LXCMGR_PVE_API_URL", "https://127.0.0.1:8006")
os.environ.setdefault("LXCMGR_PVE_TOKEN_ID", "test@pve!api")
os.environ.setdefault("LXCMGR_PVE_TOKEN_SECRET", "test-secret")
os.environ.setdefault("LXCMGR_SESSION_SECRET", "0" * 64)
os.environ.setdefault("LXCMGR_SKIP_SCHEDULER", "1")
os.environ.setdefault("LXCMGR_API_TOKEN", "test-api-token")
os.environ.setdefault("LXCMGR_ADMIN_USER", "admin")
os.environ.setdefault("LXCMGR_ADMIN_PASSWORD", "test-password-ok")
os.environ.setdefault("LXCMGR_DB_PATH", os.path.join(_tmpdir, "test.db"))
os.environ.setdefault("LXCMGR_PVE_VERIFY_SSL", "0")

import pytest
from fastapi.testclient import TestClient

from app.core import db
from app.main import app


ADMIN = {
    "username": os.environ["LXCMGR_ADMIN_USER"],
    "password": os.environ["LXCMGR_ADMIN_PASSWORD"],
}


@pytest.fixture
def client():
    with TestClient(app, base_url="https://testserver") as c:
        yield c


@pytest.fixture
def auth_header():
    return {"Authorization": "Bearer test-api-token"}


@pytest.fixture
def guest(client):
    with db.get_conn() as conn:
        conn.execute("DELETE FROM backup_runs")
        conn.execute("DELETE FROM runs")
        conn.execute("DELETE FROM backup_status")
        conn.execute("DELETE FROM security_checks")
        conn.execute("DELETE FROM schedules")
        conn.execute("DELETE FROM guests")
        conn.execute(
            """INSERT INTO guests
                 (vmid, node, name, type, app_type, tags, maxmem, maxcpu, ip,
                  os_family, os_id, update_supported, last_seen)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))""",
            (100, "dell-5060", "test-lxc", "lxc", "unknown", "managed",
             0, 1, "10.0.0.1", "linux", "debian", 1),
        )
        conn.execute(
            "INSERT INTO schedules (vmid, cron, enabled) VALUES (?,?,?)",
            (100, "0 4 * * 6", 1),
        )
    return 100
