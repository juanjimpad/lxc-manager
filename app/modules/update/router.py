from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
import html

from ...core import agent, auth, config, db
from ...core.strings import t
from ...core.templates import templates
from ..backups import status as backup_status
from ..security import audit as security_audit
from . import runner, scheduler

router = APIRouter()


def _guests_with_status():
    with db.get_conn() as conn:
        guests = conn.execute(
            """SELECT g.*, s.cron, s.enabled,
                      (SELECT status FROM runs r WHERE r.vmid=g.vmid ORDER BY r.id DESC LIMIT 1) as last_status,
                      (SELECT started_at FROM runs r WHERE r.vmid=g.vmid ORDER BY r.id DESC LIMIT 1) as last_run
               FROM guests g LEFT JOIN schedules s ON s.vmid = g.vmid
               ORDER BY g.node, g.vmid"""
        ).fetchall()
    jobs = {j.id: j.next_run_time for j in scheduler.scheduler.get_jobs()}
    security = {g["vmid"]: security_audit.get_evaluated(g["vmid"]) for g in guests}
    backups = {g["vmid"]: backup_status.get_evaluated(g["vmid"]) for g in guests}
    return guests, jobs, security, backups


@router.get("/")
def index(request: Request, _=Depends(auth.require_login)):
    guests, jobs, security, backups = _guests_with_status()
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "guests": guests, "jobs": jobs, "security": security, "backups": backups},
    )


@router.get("/partials/guests")
def partial_guests(request: Request, _=Depends(auth.require_login)):
    guests, jobs, security, backups = _guests_with_status()
    return templates.TemplateResponse(
        "_guests_table.html",
        {"request": request, "guests": guests, "jobs": jobs, "security": security, "backups": backups},
    )


@router.post("/refresh")
async def refresh_guests(
    request: Request,
    _=Depends(auth.require_login),
    _csrf=Depends(auth.require_csrf),
):
    scheduler.sync_guests_and_schedules()
    guests, jobs, security, backups = _guests_with_status()
    return templates.TemplateResponse(
        "_guests_table.html",
        {"request": request, "guests": guests, "jobs": jobs, "security": security, "backups": backups},
    )


@router.get("/guest/{vmid}")
def guest_detail(request: Request, vmid: int, _=Depends(auth.require_login)):
    with db.get_conn() as conn:
        guest = conn.execute("SELECT * FROM guests WHERE vmid=?", (vmid,)).fetchone()
        schedule = conn.execute("SELECT * FROM schedules WHERE vmid=?", (vmid,)).fetchone()
        runs = conn.execute(
            "SELECT * FROM runs WHERE vmid=? ORDER BY id DESC LIMIT 20", (vmid,)
        ).fetchall()
    if guest is None:
        return RedirectResponse("/", status_code=303)
    # kernel is fetched lazily by the page itself (GET /guest/{vmid}/kernel)
    # so this route never blocks on a live SSH call — everything else here
    # is already local (SQLite).
    sec = security_audit.get_evaluated(vmid)  # cached, never fetched live here
    bk = backup_status.get_evaluated(vmid)    # cached, never fetched live here
    backup_runs = backup_status.recent_runs(vmid)
    next_run = scheduler.next_run_for(vmid)
    if guest["vmid"] in config.VM_GUESTS:
        vg = config.VM_GUESTS[guest["vmid"]]
        update_via = f"{vg['user']}@{vg['host']} (SSH)"
    else:
        update_via = t["update_via_agent"]
    return templates.TemplateResponse(
        "guest.html",
        {
            "request": request, "guest": guest, "schedule": schedule, "runs": runs,
            "sec": sec, "bk": bk, "pending": backup_status.is_pending(vmid),
            "backup_runs": backup_runs,
            "runner_pending": runner.is_pending(vmid),
            "vmid": vmid, "next_run": next_run,
            "update_via": update_via,
        },
    )


@router.get("/guest/{vmid}/kernel", response_class=HTMLResponse)
def guest_kernel(vmid: int, _=Depends(auth.require_login)):
    with db.get_conn() as conn:
        guest = conn.execute("SELECT node FROM guests WHERE vmid=?", (vmid,)).fetchone()
    if guest is None:
        return html.escape(t["unknown"])
    return html.escape(agent.get_kernel(guest["node"], vmid))


@router.get("/partials/guest/{vmid}/history")
def partial_history(request: Request, vmid: int, _=Depends(auth.require_login)):
    with db.get_conn() as conn:
        runs = conn.execute(
            "SELECT * FROM runs WHERE vmid=? ORDER BY id DESC LIMIT 20", (vmid,)
        ).fetchall()
    return templates.TemplateResponse(
        "_run_history.html", {"request": request, "runs": runs, "vmid": vmid}
    )


@router.get("/partials/guest/{vmid}/update-controls")
def partial_update_controls(request: Request, vmid: int, _=Depends(auth.require_login)):
    with db.get_conn() as conn:
        guest = conn.execute("SELECT * FROM guests WHERE vmid=?", (vmid,)).fetchone()
        runs = conn.execute(
            "SELECT * FROM runs WHERE vmid=? ORDER BY id DESC LIMIT 20", (vmid,)
        ).fetchall()
    running = runner.is_pending(vmid) or (bool(runs) and runs[0]["status"] == "running")
    return templates.TemplateResponse(
        "_update_controls.html",
        {
            "request": request,
            "vmid": vmid,
            "runs": runs,
            "running": running,
            "update_supported": bool(guest["update_supported"]) if guest else False,
            "os_family": guest["os_family"] if guest else "unknown",
            "os_id": guest["os_id"] if guest else "unknown",
        },
    )


@router.post("/guest/{vmid}/run", response_class=HTMLResponse)
async def guest_run_now(
    vmid: int,
    background_tasks: BackgroundTasks,
    _=Depends(auth.require_login),
    _csrf=Depends(auth.require_csrf),
):
    runner.mark_pending(vmid)
    background_tasks.add_task(runner.run_guest, vmid)
    # tells the history panel (idle otherwise — see _run_history.html) to
    # fetch once now instead of polling forever on the off chance a run
    # is happening. Same event re-fetches #update-controls so the Run
    # button disables for the whole background job.
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
    # Validate before touching the DB: reload_jobs() rebuilds every guest's
    # job in one pass, so one bad cron string saved here would raise partway
    # through and leave every guest after it unscheduled — not just this one.
    try:
        CronTrigger.from_crontab(cron, timezone="Europe/Madrid")
    except ValueError:
        return f'<span class="status-failed">{html.escape(t["invalid_cron"])}</span>'

    with db.get_conn() as conn:
        conn.execute(
            "UPDATE schedules SET cron=?, enabled=? WHERE vmid=?",
            (cron, 1 if enabled else 0, vmid),
        )
    scheduler.reload_jobs()
    # "Next scheduled" (in the stat row above, rendered once at page load)
    # doesn't belong to this form's own target — push the fresh value there
    # out-of-band instead of leaving it showing a stale time after e.g.
    # disabling the schedule.
    next_run = scheduler.next_run_for(vmid)
    next_label = next_run or t["status_never"]
    return (
        f'<span class="status-ok">{html.escape(t["saved"])}</span>'
        f'<span id="next-run-value" hx-swap-oob="true">{html.escape(str(next_label))}</span>'
    )
