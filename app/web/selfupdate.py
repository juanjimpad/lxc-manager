"""HTML adapter for panel self-update (banner + confirm)."""
import html

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import HTMLResponse

from ..core import auth
from ..core.errors import (
    InvalidRelease,
    NotNewer,
    SelfUpdateBusy,
    SelfUpdateDisabled,
)
from ..core.strings import t
from ..core.templates import templates
from ..modules.selfupdate import service

router = APIRouter()


@router.post("/settings/check-update")
async def settings_check_update(
    request: Request,
    _=Depends(auth.require_login),
    _csrf=Depends(auth.require_csrf),
):
    st = service.status(force=True)
    return templates.TemplateResponse(
        "_settings_version.html",
        {"request": request, **st},
    )


@router.get("/partials/self-update")
def partial_self_update(request: Request, _=Depends(auth.require_login)):
    return templates.TemplateResponse(
        "_self_update_banner.html",
        {"request": request, **service.status()},
    )


@router.post("/self-update", response_class=HTMLResponse)
async def self_update_now(
    request: Request,
    background_tasks: BackgroundTasks,
    _=Depends(auth.require_login),
    _csrf=Depends(auth.require_csrf),
):
    st = service.status()
    if not st["update_available"] or not st["tag"]:
        return HTMLResponse(
            content=(
                f'<div id="self-update-banner" class="self-update-banner">'
                f'<span class="status-failed">{html.escape(t["self_update_none"])}</span>'
                f"</div>"
            ),
            status_code=400,
        )
    tag = st["tag"]
    background_tasks.add_task(_apply_quiet, tag)
    return HTMLResponse(
        content=(
            f'<div id="self-update-banner" class="self-update-banner">'
            f'<span>{html.escape(t["self_update_installing"])} {html.escape(tag)}</span>'
            f"</div>"
        ),
        headers={"HX-Trigger": "selfUpdateStarted"},
    )


def _apply_quiet(tag: str) -> None:
    try:
        service.apply(tag)
    except (SelfUpdateDisabled, SelfUpdateBusy, InvalidRelease, NotNewer, Exception):
        # apply() stores _last_error; the next banner poll shows it.
        return
