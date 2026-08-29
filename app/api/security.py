"""Security cache + on-demand refresh."""
from fastapi import APIRouter, Depends, HTTPException

from ..core import auth
from ..modules.security import audit
from ..modules.update import service as update_service

router = APIRouter()


@router.get("/guests/{vmid}/security")
def get_security(vmid: int, _user: str = Depends(auth.require_api_auth)):
    if update_service.get_guest(vmid) is None:
        raise HTTPException(status_code=404, detail="Guest not found")
    return audit.get_evaluated(vmid)


@router.post("/guests/{vmid}/security")
def refresh_security(vmid: int, _user: str = Depends(auth.require_api_auth)):
    if update_service.get_guest(vmid) is None:
        raise HTTPException(status_code=404, detail="Guest not found")
    audit.run_audit(vmid)
    return audit.get_evaluated(vmid)
