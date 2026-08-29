from unittest.mock import patch

from app.core import auth, db
from app.core.version import APP_VERSION

ADMIN = {"username": "admin", "password": "test-password-ok"}


def test_unauthenticated_api_is_json_401(client):
    r = client.get("/api/v1/guests")
    assert r.status_code == 401
    assert r.json()["detail"] == "Not authenticated"


def test_wrong_bearer_is_401(client):
    r = client.get("/api/v1/guests", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_bearer_me(client, auth_header):
    r = client.get("/api/v1/me", headers=auth_header)
    assert r.status_code == 200
    assert r.json() == {"user": "api"}


def test_login_then_session_me(client):
    r = client.post("/api/v1/login", json=ADMIN)
    assert r.status_code == 200
    assert r.json() == {"user": "admin"}
    me = client.get("/api/v1/me")
    assert me.status_code == 200
    assert me.json() == {"user": "admin"}


def test_login_wrong_password(client):
    r = client.post(
        "/api/v1/login",
        json={"username": "admin", "password": "definitely-wrong"},
    )
    assert r.status_code == 401
    assert "detail" in r.json()


def test_bearer_cannot_change_password(client, auth_header):
    r = client.post(
        "/api/v1/settings/password",
        headers=auth_header,
        json={"current_password": "x", "new_password": "abcdefghijkl"},
    )
    assert r.status_code == 401


def test_list_guests(client, auth_header, guest):
    r = client.get("/api/v1/guests", headers=auth_header)
    assert r.status_code == 200
    guests = r.json()["guests"]
    assert len(guests) == 1
    assert guests[0]["vmid"] == 100
    assert guests[0]["name"] == "test-lxc"
    assert guests[0]["update_supported"] is True
    assert guests[0]["security"]["checked"] is False
    assert guests[0]["backups"]["checked"] is False


def test_guest_detail_404(client, auth_header):
    r = client.get("/api/v1/guests/999", headers=auth_header)
    assert r.status_code == 404


def test_guest_detail(client, auth_header, guest):
    r = client.get("/api/v1/guests/100", headers=auth_header)
    assert r.status_code == 200
    body = r.json()
    assert body["guest"]["vmid"] == 100
    assert body["schedule"]["cron"] == "0 4 * * 6"
    assert body["schedule"]["enabled"] is True
    assert body["update_via"]["kind"] == "agent"
    assert body["runs"] == []


def test_invalid_cron_not_saved(client, auth_header, guest):
    r = client.put(
        "/api/v1/guests/100/schedule",
        headers=auth_header,
        json={"cron": "not a cron", "enabled": True},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "invalid_cron"
    with db.get_conn() as conn:
        row = conn.execute("SELECT cron FROM schedules WHERE vmid=100").fetchone()
    assert row["cron"] == "0 4 * * 6"


def test_set_schedule(client, auth_header, guest):
    r = client.put(
        "/api/v1/guests/100/schedule",
        headers=auth_header,
        json={"cron": "0 5 * * 6", "enabled": False},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["cron"] == "0 5 * * 6"
    assert body["enabled"] is False
    assert body["next_run"] is None  # disabled → no job


def test_start_update_run(client, auth_header, guest):
    with patch("app.api.guests.runner.run_guest") as run:
        r = client.post("/api/v1/guests/100/runs", headers=auth_header)
    assert r.status_code == 202
    assert r.json()["status"] == "started"
    run.assert_called_once_with(100)


def test_start_update_missing_guest(client, auth_header):
    r = client.post("/api/v1/guests/999/runs", headers=auth_header)
    assert r.status_code == 404


def test_sync_guests_mocked(client, auth_header, guest):
    with patch(
        "app.modules.update.scheduler.proxmox.discover_guests", return_value=[]
    ), patch(
        "app.modules.backups.status.proxmox.list_pbs_storages", return_value=[]
    ):
        r = client.post("/api/v1/guests/sync", headers=auth_header)
    assert r.status_code == 200
    # discovery returned nothing → the seeded guest is pruned
    assert r.json()["guests"] == []


def test_html_login_page(client):
    r = client.get("/login")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert f"v{APP_VERSION}" in r.text
    assert "https://github.com/juanjimpad/lxc-manager" in r.text


def test_html_index_redirects_when_anonymous(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert "/login" in r.headers["location"]


def test_html_guest_page_renders_dicts(client, guest):
    assert client.post("/api/v1/login", json=ADMIN).status_code == 200
    r = client.get("/guest/100")
    assert r.status_code == 200
    assert "test-lxc" in r.text
    assert "dell-5060" in r.text


def test_html_index_table_after_login(client, guest):
    assert client.post("/api/v1/login", json=ADMIN).status_code == 200
    r = client.get("/")
    assert r.status_code == 200
    assert 'id="guest-table"' in r.text
    assert ">100<" in r.text
    assert f"v{APP_VERSION}" in r.text
    assert "https://github.com/juanjimpad/lxc-manager" in r.text


def test_html_settings_matches_other_page_container(client):
    assert client.post("/api/v1/login", json=ADMIN).status_code == 200
    index = client.get("/")
    settings = client.get("/settings")
    assert settings.status_code == 200
    assert 'class="container"' in settings.text
    assert "max-width: 24rem" not in settings.text
    assert 'class="settings-form"' in settings.text
    assert index.text.count('class="container"') >= 1


def test_change_password_via_session(client):
    assert client.post("/api/v1/login", json=ADMIN).status_code == 200
    r = client.post(
        "/api/v1/settings/password",
        json={
            "current_password": ADMIN["password"],
            "new_password": "new-password-ok",
        },
    )
    assert r.status_code == 200
    # restore so other tests still work
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE users SET password_hash=? WHERE username=?",
            (auth.hash_password(ADMIN["password"]), ADMIN["username"]),
        )
