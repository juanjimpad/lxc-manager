"""Client-facing API (HMAC) and HTML routes for a single machine."""
from __future__ import annotations

import html as html_mod

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ...core import auth, client_auth
from ...core.strings import t
from ...core.templates import templates
from . import jobs, store

api_router = APIRouter(prefix="/api/v1")
router = APIRouter()


@api_router.post("/heartbeat")
async def heartbeat(request: Request, client_id: str = Depends(client_auth.require_client)):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    stored = store.apply_heartbeat(client_id, payload)
    pending = jobs.claim_next(client_id)
    return {
        "ok": True,
        "client_id": client_id,
        "job": pending,
        "server_time": stored["last_seen"] if stored else None,
    }


@api_router.get("/jobs")
async def poll_jobs(client_id: str = Depends(client_auth.require_client)):
    job = jobs.claim_next(client_id)
    return {"job": job}


@api_router.post("/jobs/{job_id}/result")
async def job_result(
    request: Request,
    job_id: int,
    client_id: str = Depends(client_auth.require_client),
):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    ok = bool(payload.get("ok"))
    updated = jobs.complete(
        job_id,
        client_id,
        ok=ok,
        detail=str(payload.get("detail") or ""),
        summary=str(payload.get("summary") or ""),
    )
    if updated is None:
        return JSONResponse({"ok": False, "error": "unknown job"}, status_code=404)
    return {"ok": True, "job": updated}


@router.get("/client/{client_id}")
def client_detail(request: Request, client_id: str, _=Depends(auth.require_login)):
    client = store.get_client(client_id)
    if client is None:
        return RedirectResponse("/", status_code=303)
    resources = store.list_resources(client_id)
    history = jobs.list_jobs(client_id)
    pending = jobs.has_pending(client_id)
    docker = [r for r in resources if r["kind"] == "docker"]
    lxc = [r for r in resources if r["kind"] == "lxc"]
    qemu = [r for r in resources if r["kind"] == "qemu"]
    return templates.TemplateResponse(
        "client.html",
        {
            "request": request,
            "client": client,
            "docker": docker,
            "lxc": lxc,
            "qemu": qemu,
            "jobs": history,
            "pending": pending,
        },
    )


@router.get("/partials/client/{client_id}")
def partial_client(request: Request, client_id: str, _=Depends(auth.require_login)):
    client = store.get_client(client_id)
    if client is None:
        return HTMLResponse(html_mod.escape(t["unknown"]))
    resources = store.list_resources(client_id)
    history = jobs.list_jobs(client_id)
    pending = jobs.has_pending(client_id)
    return templates.TemplateResponse(
        "_client_body.html",
        {
            "request": request,
            "client": client,
            "docker": [r for r in resources if r["kind"] == "docker"],
            "lxc": [r for r in resources if r["kind"] == "lxc"],
            "qemu": [r for r in resources if r["kind"] == "qemu"],
            "jobs": history,
            "pending": pending,
        },
    )


@router.post("/client/{client_id}/run", response_class=HTMLResponse)
async def client_run(
    request: Request,
    client_id: str,
    _=Depends(auth.require_login),
    _csrf=Depends(auth.require_csrf),
):
    client = store.get_client(client_id)
    if client is None:
        return HTMLResponse(
            f'<span class="status-failed">{html_mod.escape(t["unknown"])}</span>',
            status_code=404,
        )
    form = await request.form()
    kind = str(form.get("kind") or "sys-update")
    target = str(form.get("target") or "")
    try:
        jobs.enqueue(client_id, kind, target)
    except ValueError:
        return HTMLResponse(
            f'<span class="status-failed">{html_mod.escape(t["unknown_job"])}</span>',
            status_code=400,
        )
    return HTMLResponse(
        content=f'<span class="status-ok">{html_mod.escape(t["job_queued"])}</span>',
        headers={"HX-Trigger": "jobQueued"},
    )
