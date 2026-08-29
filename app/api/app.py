"""JSON API sub-app, mounted at /api. OpenAPI lives at /api/docs."""
from fastapi import FastAPI

from ..core.version import APP_VERSION
from . import auth, backups, guests, security, selfupdate

api = FastAPI(
    title="lxc-manager API",
    version=APP_VERSION,
    description=(
        "JSON façade over the lxc-manager core. Authenticate with the same "
        "cookie session as the HTML panel, or with `Authorization: Bearer` "
        "and `LXCMGR_API_TOKEN`."
    ),
)

api.include_router(auth.router, prefix="/v1", tags=["auth"])
api.include_router(guests.router, prefix="/v1", tags=["guests"])
api.include_router(security.router, prefix="/v1", tags=["security"])
api.include_router(backups.router, prefix="/v1", tags=["backups"])
api.include_router(selfupdate.router, prefix="/v1", tags=["self-update"])
