"""Single Jinja2Templates instance, shared by every HTML adapter in
app/web/ — they all render against the same app/templates/."""
from pathlib import Path

from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from . import auth, config
from .strings import t
from .version import APP_VERSION, ASSET_VERSION

_templates_dir = Path(__file__).resolve().parent.parent / "templates"
_base = Jinja2Templates(directory=str(_templates_dir))


def _human_mem(n_bytes: int) -> str:
    gib = n_bytes / (1024 ** 3)
    if gib >= 1:
        return f"{gib:.0f} GB" if gib == int(gib) else f"{gib:.1f} GB"
    return f"{n_bytes // (1024 ** 2)} MB"


_base.env.filters["human_mem"] = _human_mem
_base.env.globals["t"] = t
_base.env.globals["asset_v"] = ASSET_VERSION
_base.env.globals["app_version"] = APP_VERSION
_base.env.globals["repo_url"] = f"https://github.com/{config.UPDATE_REPO}"


class _CsrfTemplates:
    """Inject csrf_token into every template context that has a Request."""

    def TemplateResponse(self, name: str, context: dict, **kwargs):
        request = context.get("request")
        if isinstance(request, Request):
            context.setdefault("csrf_token", auth.ensure_csrf(request))
        return _base.TemplateResponse(name, context, **kwargs)


templates = _CsrfTemplates()
