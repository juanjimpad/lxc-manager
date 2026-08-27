import os
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .core import auth, db
from .core.strings import t
from .core.templates import templates
from .modules.backups import router as backups_router
from .modules.security import router as security_router
from .modules.update import router as update_router
from .modules.update import scheduler as update_scheduler

app = FastAPI(title="lxc-manager")

_here = Path(__file__).parent
app.mount("/static", StaticFiles(directory=str(_here / "static")), name="static")

SESSION_SECRET = os.environ.get("LXCMGR_SESSION_SECRET")
if not SESSION_SECRET:
    raise RuntimeError("LXCMGR_SESSION_SECRET is not set — see .env.example")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, https_only=False, same_site="lax")


@app.exception_handler(auth.LoginRequired)
def _login_required_handler(request: Request, exc: auth.LoginRequired):
    return auth.login_redirect(request)


@app.on_event("startup")
def _startup():
    db.init_db()
    auth.seed_admin_if_empty()
    update_scheduler.start()


# --- auth (cross-cutting, doesn't belong to any module) --------------------

@app.get("/login")
def login_form(request: Request, next: str = "/"):
    if auth.current_user(request):
        return RedirectResponse(next, status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "next": next, "error": None})


@app.post("/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...), next: str = Form("/")):
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if row is None or not auth.verify_password(password, row["password_hash"]):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "next": next, "error": t["login_error"]},
            status_code=401,
        )
    request.session["user"] = username
    return RedirectResponse(next or "/", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/settings")
def settings_page(request: Request, _=Depends(auth.require_login)):
    return templates.TemplateResponse(
        "settings.html", {"request": request, "user": auth.current_user(request)}
    )


@app.post("/settings/password", response_class=HTMLResponse)
def settings_change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    _=Depends(auth.require_login),
):
    username = auth.current_user(request)
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if row is None or not auth.verify_password(current_password, row["password_hash"]):
            return f'<span class="status-failed">{t["wrong_current_password"]}</span>'
        if len(new_password) < 12:
            return f'<span class="status-failed">{t["password_too_short"]}</span>'
        conn.execute(
            "UPDATE users SET password_hash=? WHERE username=?",
            (auth.hash_password(new_password), username),
        )
    return f'<span class="status-ok">{t["password_updated"]}</span>'


# --- modules -----------------------------------------------------------

app.include_router(update_router.router)
app.include_router(security_router.router)
app.include_router(backups_router.router)
