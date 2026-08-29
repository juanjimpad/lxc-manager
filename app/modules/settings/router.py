"""Settings: password, cluster key, Telegram, self-update."""
from __future__ import annotations

import html as html_mod

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import HTMLResponse

from ...core import auth, cluster, config, db, selfupdate, telegram
from ...core.strings import t
from ...core.templates import templates

router = APIRouter()


def _settings_ctx(request: Request, user: str) -> dict:
    try:
        update_info = selfupdate.remote_status()
        update_error = None
    except selfupdate.UpdateError as exc:
        update_info = None
        update_error = str(exc)
    except Exception as exc:  # noqa: BLE001 — git missing, no network, …
        update_info = None
        update_error = str(exc)
    return {
        "request": request,
        "user": user,
        "cluster_key": cluster.load_key() or "",
        "public_url": config.PUBLIC_URL,
        "telegram_token": telegram.get_token(),
        "telegram_chat_id": telegram.get_chat_id(),
        "notify_client_offline": telegram.event_enabled("notify_client_offline"),
        "notify_client_online": telegram.event_enabled("notify_client_online"),
        "notify_update_ok": telegram.event_enabled("notify_update_ok"),
        "notify_update_failed": telegram.event_enabled("notify_update_failed"),
        "notify_self_update": telegram.event_enabled("notify_self_update"),
        "update_info": update_info,
        "update_error": update_error,
    }


@router.get("/settings")
def settings_page(request: Request, _=Depends(auth.require_login)):
    ctx = _settings_ctx(request, auth.current_user(request))
    from ...core.version import APP_VERSION

    ctx["app_version"] = APP_VERSION
    ctx["revision"] = selfupdate.current_revision()
    return templates.TemplateResponse("settings.html", ctx)


@router.post("/settings/telegram", response_class=HTMLResponse)
async def settings_telegram(
    request: Request,
    telegram_token: str = Form(""),
    telegram_chat_id: str = Form(""),
    notify_client_offline: str = Form(""),
    notify_client_online: str = Form(""),
    notify_update_ok: str = Form(""),
    notify_update_failed: str = Form(""),
    notify_self_update: str = Form(""),
    _=Depends(auth.require_login),
    _csrf=Depends(auth.require_csrf),
):
    db.set_setting("telegram_token", telegram_token.strip())
    db.set_setting("telegram_chat_id", telegram_chat_id.strip())
    for key, raw in (
        ("notify_client_offline", notify_client_offline),
        ("notify_client_online", notify_client_online),
        ("notify_update_ok", notify_update_ok),
        ("notify_update_failed", notify_update_failed),
        ("notify_self_update", notify_self_update),
    ):
        db.set_setting(key, "1" if raw else "0")
    return f'<span class="status-ok">{html_mod.escape(t["saved"])}</span>'


@router.post("/settings/telegram/test", response_class=HTMLResponse)
async def settings_telegram_test(
    request: Request,
    _=Depends(auth.require_login),
    _csrf=Depends(auth.require_csrf),
):
    ok = telegram.notify("homelab-manager · test notification", event=None)
    if ok:
        return f'<span class="status-ok">{html_mod.escape(t["telegram_test_ok"])}</span>'
    return f'<span class="status-failed">{html_mod.escape(t["telegram_test_failed"])}</span>'


@router.post("/settings/update/check", response_class=HTMLResponse)
async def settings_update_check(
    request: Request,
    _=Depends(auth.require_login),
    _csrf=Depends(auth.require_csrf),
):
    try:
        info = selfupdate.remote_status()
    except selfupdate.UpdateError as exc:
        return f'<span class="status-failed">{html_mod.escape(str(exc))}</span>'
    if info["up_to_date"]:
        return f'<span class="status-ok">{html_mod.escape(t["update_up_to_date"])}</span>'
    msg = t["update_behind"].format(n=info["behind"])
    return f'<span class="status-warning">{html_mod.escape(msg)}</span>'


def _apply_and_restart() -> None:
    from ...core import telegram as tg

    try:
        result = selfupdate.apply_update()
        tg.notify(
            f"homelab-manager · self-update: {result.get('message')}",
            event="notify_self_update",
        )
        if result.get("applied"):
            selfupdate.request_restart()
    except Exception as exc:  # noqa: BLE001
        tg.notify(f"homelab-manager · self-update failed: {exc}", event="notify_self_update")


@router.post("/settings/update/apply", response_class=HTMLResponse)
async def settings_update_apply(
    background_tasks: BackgroundTasks,
    _=Depends(auth.require_login),
    _csrf=Depends(auth.require_csrf),
):
    background_tasks.add_task(_apply_and_restart)
    return f'<span class="status-ok">{html_mod.escape(t["update_started"])}</span>'
