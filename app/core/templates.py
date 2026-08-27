"""Single Jinja2Templates instance, shared by main.py and every module's
router — they all render against the same app/templates/."""
from pathlib import Path

from fastapi.templating import Jinja2Templates

from .strings import t
from .version import ASSET_VERSION

_templates_dir = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))


def _human_mem(n_bytes: int) -> str:
    gib = n_bytes / (1024 ** 3)
    if gib >= 1:
        return f"{gib:.0f} GB" if gib == int(gib) else f"{gib:.1f} GB"
    return f"{n_bytes // (1024 ** 2)} MB"


templates.env.filters["human_mem"] = _human_mem
templates.env.globals["t"] = t
templates.env.globals["asset_v"] = ASSET_VERSION
