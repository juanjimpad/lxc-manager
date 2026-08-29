"""JSON: current vs latest GitHub tag; apply a panel self-update."""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from ..core import auth
from ..core.errors import (
    InvalidRelease,
    NotNewer,
    SelfUpdateBusy,
    SelfUpdateDisabled,
)
from ..modules.selfupdate import service
from . import schemas

router = APIRouter()


@router.get("/version", response_model=schemas.VersionOut)
def api_version(_user: str = Depends(auth.require_api_auth)):
    st = service.status()
    return schemas.VersionOut(
        enabled=st["enabled"],
        current=st["current"],
        latest=st["latest"],
        update_available=st["update_available"],
        applying=st["applying"],
        error=st["error"],
    )


@router.post("/self-update", response_model=schemas.StatusOut, status_code=202)
def api_self_update(
    background_tasks: BackgroundTasks,
    _user: str = Depends(auth.require_api_auth),
):
    st = service.status(force=True)
    if not st["enabled"]:
        raise HTTPException(status_code=503, detail="self-update disabled")
    if not st["update_available"] or not st["tag"]:
        raise HTTPException(status_code=400, detail="no newer release")
    tag = st["tag"]

    def _run(t: str = tag) -> None:
        try:
            service.apply(t)
        except SelfUpdateDisabled:
            pass
        except SelfUpdateBusy:
            pass
        except (InvalidRelease, NotNewer):
            pass
        except Exception:
            pass

    background_tasks.add_task(_run)
    return schemas.StatusOut(status="started", detail=tag)
