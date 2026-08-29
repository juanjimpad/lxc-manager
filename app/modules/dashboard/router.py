"""Main dashboard: client cards with online/CPU/RAM/temp, plus the
legacy Proxmox guests table when that module is configured."""
from fastapi import APIRouter, Depends, Request

from ...core import auth, config
from ...core.templates import templates
from ..clients import store as client_store
from ..update import router as update_router

router = APIRouter()


def _dashboard_ctx():
    clients = client_store.list_clients()
    online = sum(1 for c in clients if c["online"])
    guests = jobs = security = backups = None
    if config.PVE_ENABLED:
        guests, jobs, security, backups = update_router._guests_with_status()
    return {
        "clients": clients,
        "online_count": online,
        "offline_count": len(clients) - online,
        "pve_enabled": config.PVE_ENABLED,
        "guests": guests or [],
        "jobs": jobs or {},
        "security": security or {},
        "backups": backups or {},
    }


@router.get("/")
def index(request: Request, _=Depends(auth.require_login)):
    return templates.TemplateResponse(
        "index.html",
        {"request": request, **_dashboard_ctx()},
    )


@router.get("/partials/dashboard")
def partial_dashboard(request: Request, _=Depends(auth.require_login)):
    return templates.TemplateResponse(
        "_dashboard.html",
        {"request": request, **_dashboard_ctx()},
    )
