"""Single Jinja2Templates instance, shared by main.py and every module's
router — they all render against the same app/templates/."""
from pathlib import Path

from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from . import auth, config
from .strings import t
from .version import APP_VERSION, ASSET_VERSION

_templates_dir = Path(__file__).resolve().parent.parent / "templates"
_base = Jinja2Templates(directory=str(_templates_dir))


def _human_mem(n_bytes) -> str:
    try:
        n = int(n_bytes)
    except (TypeError, ValueError):
        return "—"
    gib = n / (1024 ** 3)
    if gib >= 1:
        return f"{gib:.0f} GB" if gib == int(gib) else f"{gib:.1f} GB"
    return f"{n // (1024 ** 2)} MB"


def _human_uptime(seconds) -> str:
    try:
        n = int(seconds)
    except (TypeError, ValueError):
        return "—"
    days, rem = divmod(n, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _pct(value) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.0f}%"
    except (TypeError, ValueError):
        return "—"


_base.env.filters["human_mem"] = _human_mem
_base.env.filters["human_uptime"] = _human_uptime
_base.env.filters["pct"] = _pct
_base.env.globals["t"] = t
_base.env.globals["asset_v"] = ASSET_VERSION
_base.env.globals["app_version"] = APP_VERSION
_base.env.globals["pve_enabled"] = config.PVE_ENABLED


class _CsrfTemplates:
    """Inject csrf_token into every template context that has a Request."""

    def TemplateResponse(self, name: str, context: dict, **kwargs):
        request = context.get("request")
        if isinstance(request, Request):
            context.setdefault("csrf_token", auth.ensure_csrf(request))
        return _base.TemplateResponse(name, context, **kwargs)


templates = _CsrfTemplates()
