"""Backup status + on-demand vzdump."""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from ..core import auth
from ..core.errors import GuestNotFound
from ..modules.backups import status
from ..modules.update import service as update_service
from . import schemas

router = APIRouter()


@router.get("/guests/{vmid}/backups", response_model=schemas.BackupsSectionOut)
def get_backups(vmid: int, _user: str = Depends(auth.require_api_auth)):
    if update_service.get_guest(vmid) is None:
        raise HTTPException(status_code=404, detail="Guest not found")
    section = status.get_section(vmid)
    return schemas.BackupsSectionOut(
        backups=section["bk"],
        pending=section["pending"],
        runs=[schemas.RunOut.model_validate(r) for r in section["backup_runs"]],
    )


@router.post("/guests/{vmid}/backups", response_model=schemas.StatusOut, status_code=202)
def backup_run_now(
    vmid: int,
    background_tasks: BackgroundTasks,
    _user: str = Depends(auth.require_api_auth),
):
    try:
        status.start_backup(vmid)
    except GuestNotFound:
        raise HTTPException(status_code=404, detail="Guest not found") from None
    background_tasks.add_task(status.run_backup_now, vmid)
    return schemas.StatusOut(status="started", vmid=vmid)
