"""Backups module HTML routes — thin adapter over modules.backups.status."""
import html

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import HTMLResponse

from ..core import auth
from ..core.errors import GuestNotFound
from ..core.strings import t
from ..core.templates import templates
from ..modules.backups import status

router = APIRouter()


@router.get("/partials/backups/{vmid}")
def partial_backups(request: Request, vmid: int, _=Depends(auth.require_login)):
    section = status.get_section(vmid)
    return templates.TemplateResponse(
        "_backups_section.html",
        {
            "request": request,
            "vmid": vmid,
            **section,
        },
    )


@router.get("/partials/backups/{vmid}/history")
def partial_backup_history(request: Request, vmid: int, _=Depends(auth.require_login)):
    return templates.TemplateResponse(
        "_backup_history.html",
        {
            "request": request,
            "vmid": vmid,
            "backup_runs": status.recent_runs(vmid),
        },
    )


@router.post("/backups/{vmid}/run", response_class=HTMLResponse)
async def backup_run_now(
    vmid: int,
    background_tasks: BackgroundTasks,
    _=Depends(auth.require_login),
    _csrf=Depends(auth.require_csrf),
):
    try:
        status.start_backup(vmid)
    except GuestNotFound:
        return HTMLResponse(
            content=f'<span class="status-failed">{html.escape(t["unknown"])}</span>',
            status_code=404,
        )
    background_tasks.add_task(status.run_backup_now, vmid)
    return HTMLResponse(
        content=f'<span class="status-ok">{html.escape(t["backup_started"])}</span>',
        headers={"HX-Trigger": "backupStarted"},
    )
