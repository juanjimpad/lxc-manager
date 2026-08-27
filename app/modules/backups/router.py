from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import HTMLResponse

from ...core import auth
from ...core.strings import t
from ...core.templates import templates
from . import status

router = APIRouter()


@router.get("/partials/backups/{vmid}")
def partial_backups(request: Request, vmid: int, _=Depends(auth.require_login)):
    bk = status.get_evaluated(vmid)
    pending = status.is_pending(vmid)
    backup_runs = status.recent_runs(vmid)
    return templates.TemplateResponse(
        "_backups_section.html",
        {
            "request": request,
            "vmid": vmid,
            "bk": bk,
            "pending": pending,
            "backup_runs": backup_runs,
        },
    )


@router.get("/partials/backups/{vmid}/history")
def partial_backup_history(request: Request, vmid: int, _=Depends(auth.require_login)):
    backup_runs = status.recent_runs(vmid)
    return templates.TemplateResponse(
        "_backup_history.html",
        {
            "request": request,
            "vmid": vmid,
            "backup_runs": backup_runs,
        },
    )


@router.post("/backups/{vmid}/run", response_class=HTMLResponse)
def backup_run_now(vmid: int, background_tasks: BackgroundTasks, _=Depends(auth.require_login)):
    status.begin_run(vmid)
    background_tasks.add_task(status.run_backup_now, vmid)
    return HTMLResponse(
        content=f'<span class="status-ok">{t["backup_started"]}</span>',
        headers={"HX-Trigger": "backupStarted"},
    )
