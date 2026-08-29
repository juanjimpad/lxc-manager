"""Self-update of the manager from the git remote (private today,
public later — same path either way: origin/main unless overridden).
Never runs arbitrary commands: fetch + ff-only pull of the configured
branch, then pip install of requirements, then exit 0 so systemd
restarts the unit."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from . import config
from .version import APP_VERSION

GIT_TIMEOUT = 60


class UpdateError(Exception):
    pass


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT,
        check=False,
    )


def current_revision() -> str:
    proc = _run(["git", "rev-parse", "--short", "HEAD"], config.BASE_DIR)
    if proc.returncode != 0:
        return "unknown"
    return proc.stdout.strip() or "unknown"


def remote_status() -> dict:
    """Fetch and compare HEAD to origin/branch. Does not modify the tree."""
    if os.environ.get("HLMGR_TESTING") == "1":
        local = _run(["git", "rev-parse", "HEAD"], config.BASE_DIR)
        sha = (local.stdout or "unknown").strip()
        return {
            "ok": True,
            "version": APP_VERSION,
            "local": sha[:12],
            "remote": sha[:12],
            "behind": 0,
            "up_to_date": True,
            "log": "",
            "branch": config.UPDATE_BRANCH,
            "remote_name": config.UPDATE_REMOTE,
        }
    fetch = _run(
        ["git", "fetch", "--quiet", config.UPDATE_REMOTE, config.UPDATE_BRANCH],
        config.BASE_DIR,
    )
    if fetch.returncode != 0:
        raise UpdateError((fetch.stderr or fetch.stdout or "git fetch failed").strip())
    local = _run(["git", "rev-parse", "HEAD"], config.BASE_DIR)
    remote = _run(
        ["git", "rev-parse", f"{config.UPDATE_REMOTE}/{config.UPDATE_BRANCH}"],
        config.BASE_DIR,
    )
    if local.returncode != 0 or remote.returncode != 0:
        raise UpdateError("could not resolve local/remote revisions")
    local_sha = local.stdout.strip()
    remote_sha = remote.stdout.strip()
    behind = _run(
        ["git", "rev-list", "--count", f"HEAD..{config.UPDATE_REMOTE}/{config.UPDATE_BRANCH}"],
        config.BASE_DIR,
    )
    n_behind = int((behind.stdout or "0").strip() or "0")
    log = _run(
        [
            "git",
            "log",
            "--oneline",
            "-8",
            f"HEAD..{config.UPDATE_REMOTE}/{config.UPDATE_BRANCH}",
        ],
        config.BASE_DIR,
    )
    return {
        "ok": True,
        "version": APP_VERSION,
        "local": local_sha[:12],
        "remote": remote_sha[:12],
        "behind": n_behind,
        "up_to_date": n_behind == 0,
        "log": (log.stdout or "").strip(),
        "branch": config.UPDATE_BRANCH,
        "remote_name": config.UPDATE_REMOTE,
    }


def apply_update() -> dict:
    status = remote_status()
    if status["up_to_date"]:
        return {**status, "applied": False, "message": "already up to date"}
    pull = _run(
        ["git", "merge", "--ff-only", f"{config.UPDATE_REMOTE}/{config.UPDATE_BRANCH}"],
        config.BASE_DIR,
    )
    if pull.returncode != 0:
        raise UpdateError((pull.stderr or pull.stdout or "git merge failed").strip())
    venv_pip = Path(sys.executable).parent / "pip"
    pip = str(venv_pip) if venv_pip.exists() else sys.executable
    pip_args = (
        [pip, "install", "-q", "-r", "requirements.txt"]
        if venv_pip.exists()
        else [pip, "-m", "pip", "install", "-q", "-r", "requirements.txt"]
    )
    deps = _run(pip_args, config.BASE_DIR)
    if deps.returncode != 0:
        raise UpdateError((deps.stderr or deps.stdout or "pip install failed").strip())
    return {
        **status,
        "applied": True,
        "message": "updated — service will restart",
        "log": (pull.stdout or "").strip(),
    }


def request_restart() -> None:
    """Ask uvicorn to exit; systemd Restart=on-failure / always brings it back.
    A flag file next to the DB lets tests observe the request without dying."""
    flag = Path(config.DB_PATH).resolve().parent / "RESTART_REQUESTED"
    flag.write_text("1\n", encoding="utf-8")
    if os.environ.get("HLMGR_TESTING") == "1":
        return
    os.kill(os.getpid(), 15)
