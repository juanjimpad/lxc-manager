"""Guests, sync, detail, kernel, update runs, schedule."""
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from ..core import auth
from ..core.errors import GuestNotFound, InvalidCron
from ..modules.update import runner, scheduler, service
from . import schemas

router = APIRouter()


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _guest_summary(row: dict, security: dict | None = None, backups: dict | None = None) -> schemas.GuestOut:
    payload = dict(row)
    payload["next_run"] = _iso(payload.get("next_run"))
    if security is not None:
        payload["security"] = security
    if backups is not None:
        payload["backups"] = backups
    return schemas.GuestOut.model_validate(payload)


def _run_out(row: dict) -> schemas.RunOut:
    return schemas.RunOut.model_validate(row)


@router.get("/guests", response_model=schemas.GuestListOut)
def list_guests(_user: str = Depends(auth.require_api_auth)):
    guests, security, backups = service.list_guests_with_status()
    out = []
    for g in guests:
        g = dict(g)
        g["next_run"] = _iso(scheduler.next_run_for(g["vmid"]))
        out.append(_guest_summary(g, security.get(g["vmid"]), backups.get(g["vmid"])))
    return schemas.GuestListOut(guests=out)


@router.post("/guests/sync", response_model=schemas.GuestListOut)
def sync_guests(_user: str = Depends(auth.require_api_auth)):
    guests, security, backups = service.sync_guests()
    out = []
    for g in guests:
        g = dict(g)
        g["next_run"] = _iso(scheduler.next_run_for(g["vmid"]))
        out.append(_guest_summary(g, security.get(g["vmid"]), backups.get(g["vmid"])))
    return schemas.GuestListOut(guests=out)


@router.get("/guests/{vmid}", response_model=schemas.GuestDetailOut)
def guest_detail(vmid: int, _user: str = Depends(auth.require_api_auth)):
    detail = service.get_guest_detail(vmid)
    if detail is None:
        raise HTTPException(status_code=404, detail="Guest not found")
    schedule = detail["schedule"]
    schedule_out = None
    if schedule is not None:
        schedule_out = schemas.ScheduleOut(
            cron=schedule["cron"],
            enabled=bool(schedule["enabled"]),
            next_run=_iso(detail["next_run"]),
        )
    via = detail["update_via"]
    return schemas.GuestDetailOut(
        guest=_guest_summary(detail["guest"]),
        schedule=schedule_out,
        update_via=schemas.UpdateViaOut(kind=via["kind"], target=via.get("target")),
        next_run=_iso(detail["next_run"]),
        runner_pending=detail["runner_pending"],
        runs=[_run_out(r) for r in detail["runs"]],
        security=detail["sec"],
        backups=detail["bk"],
        backup_pending=detail["pending"],
        backup_runs=[_run_out(r) for r in detail["backup_runs"]],
    )


@router.get("/guests/{vmid}/kernel", response_model=schemas.KernelOut)
def guest_kernel(vmid: int, _user: str = Depends(auth.require_api_auth)):
    kernel = service.get_kernel(vmid)
    if kernel is None:
        raise HTTPException(status_code=404, detail="Guest not found")
    return schemas.KernelOut(kernel=kernel)


@router.get("/guests/{vmid}/runs", response_model=list[schemas.RunOut])
def guest_runs(vmid: int, _user: str = Depends(auth.require_api_auth)):
    if service.get_guest(vmid) is None:
        raise HTTPException(status_code=404, detail="Guest not found")
    return [_run_out(r) for r in service.list_runs(vmid)]


@router.post("/guests/{vmid}/runs", response_model=schemas.StatusOut, status_code=202)
def guest_run_now(
    vmid: int,
    background_tasks: BackgroundTasks,
    _user: str = Depends(auth.require_api_auth),
):
    try:
        service.start_update(vmid)
    except GuestNotFound:
        raise HTTPException(status_code=404, detail="Guest not found") from None
    background_tasks.add_task(runner.run_guest, vmid)
    return schemas.StatusOut(status="started", vmid=vmid)


@router.put("/guests/{vmid}/schedule", response_model=schemas.ScheduleOut)
def guest_schedule(
    vmid: int,
    body: schemas.ScheduleIn,
    _user: str = Depends(auth.require_api_auth),
):
    try:
        next_run = service.set_schedule(vmid, body.cron, body.enabled)
    except InvalidCron:
        raise HTTPException(status_code=400, detail="invalid_cron") from None
    except GuestNotFound:
        raise HTTPException(status_code=404, detail="Guest not found") from None
    return schemas.ScheduleOut(cron=body.cron, enabled=body.enabled, next_run=_iso(next_run))
