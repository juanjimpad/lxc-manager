import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .api import api as api_app
from .core import auth, db
from .modules.update import scheduler as update_scheduler
from .web import router as web_router

app = FastAPI(title="lxc-manager", docs_url=None, redoc_url=None, openapi_url=None)

_here = Path(__file__).parent
app.mount("/static", StaticFiles(directory=str(_here / "static")), name="static")
app.mount("/api", api_app)

SESSION_SECRET = os.environ.get("LXCMGR_SESSION_SECRET")
if not SESSION_SECRET:
    raise RuntimeError("LXCMGR_SESSION_SECRET is not set — see .env.example")
# https_only=True: Secure cookie — browsers only send it on HTTPS (NPM).
# same_site=lax: blocks cross-site POST CSRF for the session cookie.
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    https_only=True,
    same_site="lax",
    max_age=60 * 60 * 12,
)

_SKIP_CSP_PREFIXES = ("/api/docs", "/api/redoc", "/api/openapi.json")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    path = request.url.path
    if not path.startswith(_SKIP_CSP_PREFIXES):
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; img-src 'self' data:; frame-ancestors 'none'; "
            "base-uri 'self'; form-action 'self'",
        )
    return response


@app.exception_handler(auth.LoginRequired)
def _login_required_handler(request: Request, exc: auth.LoginRequired):
    return auth.login_redirect(request)


@app.on_event("startup")
def _startup():
    db.init_db()
    auth.seed_admin_if_empty()
    if os.environ.get("LXCMGR_SKIP_SCHEDULER") != "1":
        update_scheduler.start()


app.include_router(web_router)
