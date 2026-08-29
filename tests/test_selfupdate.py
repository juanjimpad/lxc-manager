"""Panel self-update: GitHub tags, full app/ replace, no leftover files."""
import io
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest

from app.core import config
from app.core.errors import InvalidRelease, NotNewer, SelfUpdateDisabled
from app.core.version import APP_VERSION
from app.modules.selfupdate import service


def _tarball(tmp_path: Path, version: str, extra_app_file: str | None = "fresh.txt") -> bytes:
    root = tmp_path / f"lxc-manager-v{version}"
    app = root / "app" / "core"
    app.mkdir(parents=True)
    (app / "version.py").write_text(f'APP_VERSION = "{version}"\n', encoding="utf-8")
    if extra_app_file:
        (root / "app" / extra_app_file).write_text("new\n", encoding="utf-8")
    (root / "requirements.txt").write_text("fastapi==0.115.0\n", encoding="utf-8")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        tf.add(root, arcname=root.name)
    return buf.getvalue()


def _install_tree(tmp_path: Path) -> Path:
    root = tmp_path / "install"
    app = root / "app" / "core"
    app.mkdir(parents=True)
    (app / "version.py").write_text('APP_VERSION = "1.0.3"\n', encoding="utf-8")
    (root / "app" / "obsolete.py").write_text("old junk\n", encoding="utf-8")
    (root / "requirements.txt").write_text("old\n", encoding="utf-8")
    (root / ".env").write_text("KEEP=1\n", encoding="utf-8")
    return root


def test_version_endpoint_disabled(client, auth_header):
    r = client.get("/api/v1/version", headers=auth_header)
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False
    assert body["current"] == APP_VERSION
    assert body["update_available"] is False


def test_self_update_disabled_rejects_apply(auth_header, client):
    r = client.post("/api/v1/self-update", headers=auth_header)
    assert r.status_code == 503


def test_apply_replaces_app_tree_and_drops_obsolete(tmp_path, monkeypatch):
    root = _install_tree(tmp_path)
    blob = _tarball(tmp_path / "pkg", "1.2.0")
    monkeypatch.setattr(config, "SELF_UPDATE_ENABLED", True)
    monkeypatch.setattr(config, "INSTALL_ROOT", root)
    monkeypatch.setattr(service, "APP_VERSION", "1.0.3")
    monkeypatch.setattr(service, "_download_archive", lambda tag: blob)
    monkeypatch.setattr(service, "_run_pip", lambda req: None)
    monkeypatch.setenv("LXCMGR_SELF_UPDATE_EXIT", "0")

    service.apply("v1.2.0")

    assert not (root / "app" / "obsolete.py").exists()
    assert (root / "app" / "fresh.txt").read_text(encoding="utf-8") == "new\n"
    assert (root / ".env").read_text(encoding="utf-8") == "KEEP=1\n"
    assert "fastapi" in (root / "requirements.txt").read_text(encoding="utf-8")
    assert not (root / "app.bak").exists()


def test_apply_reverts_on_pip_failure(tmp_path, monkeypatch):
    root = _install_tree(tmp_path)
    blob = _tarball(tmp_path / "pkg", "1.2.0")
    monkeypatch.setattr(config, "SELF_UPDATE_ENABLED", True)
    monkeypatch.setattr(config, "INSTALL_ROOT", root)
    monkeypatch.setattr(service, "APP_VERSION", "1.0.3")
    monkeypatch.setattr(service, "_download_archive", lambda tag: blob)

    def boom(_req):
        raise RuntimeError("pip died")

    monkeypatch.setattr(service, "_run_pip", boom)
    monkeypatch.setenv("LXCMGR_SELF_UPDATE_EXIT", "0")
    with pytest.raises(RuntimeError, match="pip died"):
        service.apply("v1.2.0")
    assert (root / "app" / "obsolete.py").exists()
    assert not (root / "app" / "fresh.txt").exists()
    assert (root / ".env").read_text(encoding="utf-8") == "KEEP=1\n"


def test_apply_rejects_downgrade(tmp_path, monkeypatch):
    root = _install_tree(tmp_path)
    monkeypatch.setattr(config, "SELF_UPDATE_ENABLED", True)
    monkeypatch.setattr(config, "INSTALL_ROOT", root)
    monkeypatch.setattr(service, "APP_VERSION", "1.2.0")
    with pytest.raises(NotNewer):
        service.apply("v1.0.3")


def test_apply_rejects_bad_tag(monkeypatch):
    monkeypatch.setattr(config, "SELF_UPDATE_ENABLED", True)
    with pytest.raises(InvalidRelease):
        service.apply("../evil")


def test_apply_disabled(monkeypatch):
    monkeypatch.setattr(config, "SELF_UPDATE_ENABLED", False)
    with pytest.raises(SelfUpdateDisabled):
        service.apply("v1.2.0")


def test_status_newer_tag(monkeypatch):
    monkeypatch.setattr(config, "SELF_UPDATE_ENABLED", True)
    monkeypatch.setattr(service, "APP_VERSION", "1.0.3")
    monkeypatch.setattr(
        service, "_latest_from_github", lambda: ("1.2.0", "v1.2.0")
    )
    with patch.object(service, "_cache", {"at": 0.0, "latest": None, "tag": None}):
        st = service.status(force=True)
    assert st["update_available"] is True
    assert st["latest"] == "1.2.0"
    assert st["tag"] == "v1.2.0"


def test_api_self_update_starts(client, auth_header, monkeypatch):
    monkeypatch.setattr(config, "SELF_UPDATE_ENABLED", True)
    monkeypatch.setattr(service, "APP_VERSION", "1.0.3")
    monkeypatch.setattr(
        service, "_latest_from_github", lambda: ("1.2.0", "v1.2.0")
    )
    with patch.object(service, "_cache", {"at": 0.0, "latest": None, "tag": None}):
        with patch.object(service, "apply") as apply:
            r = client.post("/api/v1/self-update", headers=auth_header)
    assert r.status_code == 202
    assert r.json()["status"] == "started"
    apply.assert_called_once_with("v1.2.0")


def test_banner_empty_when_disabled(client):
    assert client.post("/api/v1/login", json={
        "username": "admin", "password": "test-password-ok",
    }).status_code == 200
    r = client.get("/partials/self-update")
    assert r.status_code == 200
    assert "self-update-banner-empty" in r.text or "New version" not in r.text
