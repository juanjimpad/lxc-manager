"""Update module HTML routes — thin adapter over modules.update.service."""
import html

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..core import auth
from ..core.errors import GuestNotFound, InvalidCron
from ..core.strings import t
from ..core.templates import templates
from ..modules.update import runner, service

router = APIRouter()


def _update_via_label(via: dict) -> str:
    if via.get("kind") == "ssh":
        return f"{via['target']} (SSH)"
    return t["update_via_agent"]


@router.get("/")
def index(request: Request, _=Depends(auth.require_login)):
    guests, security, backups = service.list_guests_with_status()
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "guests": guests, "security": security, "backups": backups},
    )


@router.get("/partials/guests")
def partial_guests(request: Request, _=Depends(auth.require_login)):
    guests, security, backups = service.list_guests_with_status()
    return templates.TemplateResponse(
        "_guests_table.html",
        {"request": request, "guests": guests, "security": security, "backups": backups},
    )


@router.post("/refresh")
async def refresh_guests(
    request: Request,
    _=Depends(auth.require_login),
    _csrf=Depends(auth.require_csrf),
):
    guests, security, backups = service.sync_guests()
    return templates.TemplateResponse(
        "_guests_table.html",
        {"request": request, "guests": guests, "security": security, "backups": backups},
    )


@router.get("/guest/{vmid}")
def guest_detail(request: Request, vmid: int, _=Depends(auth.require_login)):
    detail = service.get_guest_detail(vmid)
    if detail is None:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        "guest.html",
        {
            "request": request,
            "guest": detail["guest"],
            "schedule": detail["schedule"],
            "runs": detail["runs"],
            "sec": detail["sec"],
            "bk": detail["bk"],
            "pending": detail["pending"],
            "backup_runs": detail["backup_runs"],
            "runner_pending": detail["runner_pending"],
            "vmid": vmid,
            "next_run": detail["next_run"],
            "update_via": _update_via_label(detail["update_via"]),
        },
    )


@router.get("/guest/{vmid}/kernel", response_class=HTMLResponse)
def guest_kernel(vmid: int, _=Depends(auth.require_login)):
    kernel = service.get_kernel(vmid)
    if kernel is None:
        return html.escape(t["unknown"])
    return html.escape(kernel)


@router.get("/partials/guest/{vmid}/history")
def partial_history(request: Request, vmid: int, _=Depends(auth.require_login)):
    runs = service.list_runs(vmid)
    return templates.TemplateResponse(
        "_run_history.html", {"request": request, "runs": runs, "vmid": vmid}
    )


@router.get("/partials/guest/{vmid}/update-controls")
def partial_update_controls(request: Request, vmid: int, _=Depends(auth.require_login)):
    controls = service.get_update_controls(vmid)
    if controls is None:
        controls = {
            "vmid": vmid,
            "runs": [],
            "running": False,
            "update_supported": False,
            "os_family": "unknown",
            "os_id": "unknown",
        }
    return templates.TemplateResponse(
        "_update_controls.html",
        {"request": request, **controls},
    )


@router.post("/guest/{vmid}/run", response_class=HTMLResponse)
async def guest_run_now(
    vmid: int,
    background_tasks: BackgroundTasks,
    _=Depends(auth.require_login),
    _csrf=Depends(auth.require_csrf),
):
    try:
        service.start_update(vmid)
    except GuestNotFound:
        return HTMLResponse(
            content=f'<span class="status-failed">{html.escape(t["unknown"])}</span>',
            status_code=404,
        )
    background_tasks.add_task(runner.run_guest, vmid)
    return HTMLResponse(
        content=f'<span class="status-ok">{html.escape(t["run_launched"])}</span>',
        headers={"HX-Trigger": "runStarted"},
    )


@router.post("/guest/{vmid}/schedule", response_class=HTMLResponse)
async def guest_schedule(
    vmid: int,
    cron: str = Form(...),
    enabled: bool = Form(False),
    _=Depends(auth.require_login),
    _csrf=Depends(auth.require_csrf),
):
    try:
        next_run = service.set_schedule(vmid, cron, enabled)
    except InvalidCron:
        return f'<span class="status-failed">{html.escape(t["invalid_cron"])}</span>'
    except GuestNotFound:
        return f'<span class="status-failed">{html.escape(t["unknown"])}</span>'
    next_label = next_run or t["status_never"]
    return (
        f'<span class="status-ok">{html.escape(t["saved"])}</span>'
        f'<span id="next-run-value" hx-swap-oob="true">{html.escape(str(next_label))}</span>'
    )
