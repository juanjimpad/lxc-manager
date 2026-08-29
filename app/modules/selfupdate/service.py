"""Install a newer GitHub tag of this panel: replace `app/` wholesale
(no merge — deleted files stay gone), pip-install requirements, then
exit so systemd Restart=always brings uvicorn back.

Does not touch .env, the SQLite DB, or the host-side agent."""
from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import Path

import httpx

from ...core import config
from ...core.errors import (
    InvalidRelease,
    NotNewer,
    SelfUpdateBusy,
    SelfUpdateDisabled,
)
from ...core.version import APP_VERSION

_TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_CACHE_TTL_S = 24 * 60 * 60
_GITHUB = "https://api.github.com"
_UA = "lxc-manager-selfupdate"

_lock = threading.Lock()
_applying = False
_last_error: str | None = None
_cache: dict = {"at": 0.0, "latest": None, "tag": None}


def _semver(text: str) -> tuple[int, int, int] | None:
    raw = (text or "").strip()
    if not raw.startswith("v"):
        raw = f"v{raw}"
    m = _TAG_RE.fullmatch(raw)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def tag_for(version: str) -> str:
    """Canonical GitHub tag: vX.Y.Z."""
    parsed = _semver(version)
    if parsed is None:
        raise InvalidRelease(f"not a semver: {version}")
    return "v%d.%d.%d" % parsed


def _repo() -> str:
    repo = config.UPDATE_REPO
    if not _REPO_RE.fullmatch(repo):
        raise InvalidRelease("LXCMGR_UPDATE_REPO must be owner/name")
    return repo


def _headers() -> dict:
    return {"Accept": "application/vnd.github+json", "User-Agent": _UA}


def _add_semver(
    found: list[tuple[tuple[int, int, int], str]], name: str
) -> None:
    parsed = _semver(name)
    if parsed:
        found.append((parsed, "%d.%d.%d" % parsed))


def _latest_from_github() -> tuple[str | None, str | None]:
    """Return (version without v, tag with v) or (None, None).

    Highest semver among git tags, merged with GitHub's latest Release.
    ``/releases/latest`` is the newest *Release object*, not the newest
    tag — ``git tag vX.Y.Z && git push --tags`` does not create one.
    """
    repo = _repo()
    found: list[tuple[tuple[int, int, int], str]] = []
    with httpx.Client(timeout=15.0, follow_redirects=True, headers=_headers()) as client:
        tags = client.get(f"{_GITHUB}/repos/{repo}/tags", params={"per_page": 100})
        if tags.status_code == 200:
            for item in tags.json():
                _add_semver(found, item.get("name") or "")
        rel = client.get(f"{_GITHUB}/repos/{repo}/releases/latest")
        if rel.status_code == 200:
            _add_semver(found, (rel.json().get("tag_name") or "").strip())
        if not found:
            tags.raise_for_status()
            return None, None
    found.sort()
    ver = found[-1][1]
    return ver, tag_for(ver)


def refresh_cache() -> None:
    global _last_error
    if not config.SELF_UPDATE_ENABLED:
        return
    try:
        latest, tag = _latest_from_github()
        with _lock:
            _cache["at"] = time.time()
            _cache["latest"] = latest
            _cache["tag"] = tag
            _last_error = None
    except Exception:
        # Leave `at` alone so the next status() still retries GitHub.
        # Updating `at` here made a failed fetch look fresh and hid the
        # banner until the 6h TTL — Check for updates then showed a
        # version with no Apply button.
        return


def status(*, force: bool = False) -> dict:
    current = APP_VERSION
    if not config.SELF_UPDATE_ENABLED:
        return {
            "enabled": False,
            "current": current,
            "latest": None,
            "tag": None,
            "update_available": False,
            "applying": False,
            "error": None,
        }
    with _lock:
        applying = _applying
        err = _last_error
        stale = force or (time.time() - _cache["at"] > _CACHE_TTL_S)
        latest = _cache["latest"]
        tag = _cache["tag"]
    if stale:
        refresh_cache()
        with _lock:
            latest = _cache["latest"]
            tag = _cache["tag"]
    available = False
    cur = _semver(current)
    new = _semver(latest) if latest else None
    if cur and new and new > cur:
        available = True
    return {
        "enabled": True,
        "current": current,
        "latest": latest,
        "tag": tag if available else None,
        "update_available": available,
        "applying": applying,
        "error": err,
    }


def _run_pip(requirements: Path) -> None:
    pip = config.INSTALL_ROOT / ".venv" / "bin" / "pip"
    if pip.is_file():
        cmd = [str(pip), "install", "-r", str(requirements)]
    else:
        cmd = [sys.executable, "-m", "pip", "install", "-r", str(requirements)]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def _should_exit() -> bool:
    raw = os.environ.get("LXCMGR_SELF_UPDATE_EXIT", "1").strip().lower()
    return raw not in ("0", "false", "no")


def _download_archive(tag: str) -> bytes:
    repo = _repo()
    url = f"https://github.com/{repo}/archive/refs/tags/{tag}.tar.gz"
    with httpx.Client(timeout=60.0, follow_redirects=True, headers=_headers()) as client:
        r = client.get(url)
        r.raise_for_status()
        return r.content


def _payload_from_tar(blob: bytes, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    dest = dest.resolve()
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
        for member in tf.getmembers():
            name = Path(member.name)
            if name.is_absolute() or ".." in name.parts:
                raise InvalidRelease("unsafe archive path")
        try:
            tf.extractall(dest, filter="data")
        except TypeError:
            tf.extractall(dest)
    for p in dest.iterdir():
        if (p / "app" / "core" / "version.py").is_file() and (p / "requirements.txt").is_file():
            return p
    raise InvalidRelease("archive missing app/ or requirements.txt")


def apply(tag: str) -> None:
    """Replace INSTALL_ROOT/app with the tree from `tag` (vX.Y.Z)."""
    global _applying, _last_error
    if not config.SELF_UPDATE_ENABLED:
        raise SelfUpdateDisabled()
    parsed = _semver(tag)
    if parsed is None:
        raise InvalidRelease(f"not a semver tag: {tag}")
    tag = tag_for(tag)
    incoming = parsed
    current = _semver(APP_VERSION)
    if current is None or incoming <= current:
        raise NotNewer()

    with _lock:
        if _applying:
            raise SelfUpdateBusy()
        _applying = True
        _last_error = None

    root = config.INSTALL_ROOT
    app_dir = root / "app"
    bak = root / "app.bak"
    staging = root / "app.new"
    req_dst = root / "requirements.txt"
    req_bak = root / "requirements.txt.bak"
    extract_dir = None
    swapped = False
    try:
        blob = _download_archive(tag)
        extract_dir = root / ".selfupdate-extract"
        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        payload = _payload_from_tar(blob, extract_dir)
        new_app = payload / "app"
        new_req = payload / "requirements.txt"

        if staging.exists():
            shutil.rmtree(staging)
        shutil.copytree(new_app, staging, symlinks=False)

        if bak.exists():
            shutil.rmtree(bak)
        if app_dir.exists():
            app_dir.rename(bak)
        staging.rename(app_dir)
        swapped = True

        if req_dst.exists():
            shutil.copy2(req_dst, req_bak)
        shutil.copy2(new_req, req_dst)
        _run_pip(req_dst)

        if bak.exists():
            shutil.rmtree(bak)
        if req_bak.exists():
            req_bak.unlink()
        _last_error = None
    except Exception as exc:
        _last_error = str(exc)
        if swapped and bak.exists():
            if app_dir.exists():
                shutil.rmtree(app_dir)
            bak.rename(app_dir)
        if req_bak.exists():
            shutil.copy2(req_bak, req_dst)
            req_bak.unlink(missing_ok=True)
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise
    finally:
        if extract_dir is not None and extract_dir.exists():
            shutil.rmtree(extract_dir, ignore_errors=True)
        with _lock:
            _applying = False

    if _should_exit():
        os._exit(0)
