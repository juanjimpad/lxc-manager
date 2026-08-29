"""Login, logout, settings — HTML. Cross-cutting, not owned by a module."""
import html

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..core import auth
from ..core.strings import t
from ..core.templates import templates
from ..modules.selfupdate import service

router = APIRouter()


@router.get("/login")
def login_form(request: Request, next: str = "/"):
    next_url = auth.safe_next(next)
    if auth.current_user(request):
        return RedirectResponse(next_url, status_code=303)
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "next": next_url, "error": None},
    )


@router.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
    _csrf=Depends(auth.require_csrf),
):
    next_url = auth.safe_next(next)
    ip = auth.client_ip(request)
    if auth.login_rate_limited(ip):
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "next": next_url,
                "error": t["login_rate_limited"],
            },
            status_code=429,
        )
    if not auth.authenticate(username, password):
        auth.record_login_failure(ip)
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "next": next_url, "error": t["login_error"]},
            status_code=401,
        )
    auth.clear_login_failures(ip)
    auth.begin_session(request, username)
    return RedirectResponse(next_url, status_code=303)


@router.post("/logout")
async def logout(request: Request, _csrf=Depends(auth.require_csrf)):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/settings")
def settings_page(request: Request, _=Depends(auth.require_login)):
    st = service.status()
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "user": auth.current_user(request),
            **st,
        },
    )


@router.post("/settings/password", response_class=HTMLResponse)
async def settings_change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    _=Depends(auth.require_login),
    _csrf=Depends(auth.require_csrf),
):
    username = auth.current_user(request)
    error = auth.change_password(username, current_password, new_password)
    if error:
        return f'<span class="status-failed">{html.escape(t[error])}</span>'
    return f'<span class="status-ok">{html.escape(t["password_updated"])}</span>'
