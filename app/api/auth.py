"""JSON auth: login, logout, me, password. Session cookie or used to mint one."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ..core import auth
from ..core.strings import t
from . import schemas

router = APIRouter()


@router.post("/login", response_model=schemas.UserOut)
def api_login(request: Request, body: schemas.LoginRequest):
    ip = auth.client_ip(request)
    if auth.login_rate_limited(ip):
        return JSONResponse({"detail": t["login_rate_limited"]}, status_code=429)
    if not auth.authenticate(body.username, body.password):
        auth.record_login_failure(ip)
        return JSONResponse({"detail": t["login_error"]}, status_code=401)
    auth.clear_login_failures(ip)
    auth.begin_session(request, body.username)
    return schemas.UserOut(user=body.username)


@router.post("/logout", response_model=schemas.StatusOut)
def api_logout(request: Request, _user: str = Depends(auth.require_api_auth)):
    request.session.clear()
    return schemas.StatusOut(status="ok")


@router.get("/me", response_model=schemas.UserOut)
def api_me(user: str = Depends(auth.require_api_auth)):
    return schemas.UserOut(user=user)


@router.post("/settings/password", response_model=schemas.StatusOut)
def api_change_password(
    body: schemas.PasswordChangeRequest,
    user: str = Depends(auth.require_session_user),
):
    error = auth.change_password(user, body.current_password, body.new_password)
    if error:
        return JSONResponse({"detail": t[error]}, status_code=400)
    return schemas.StatusOut(status="ok", detail=t["password_updated"])
