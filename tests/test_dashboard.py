from tests.conftest import login


def test_login_required(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert "/login" in r.headers["location"]


def test_dashboard_empty_state(client):
    login(client)
    r = client.get("/")
    assert r.status_code == 200
    assert "homelab-manager" in r.text
    assert "No clients yet" in r.text
    assert "Guests" not in r.text  # Proxmox module off


def test_settings_shows_cluster_key(client):
    login(client)
    r = client.get("/settings")
    assert r.status_code == 200
    assert "Cluster key" in r.text
    from app.core import cluster

    assert cluster.load_key() in r.text
    assert "Telegram" in r.text
    assert "Self-update" in r.text


def test_guests_page_without_pve(client):
    login(client)
    r = client.get("/guests")
    assert r.status_code == 200
