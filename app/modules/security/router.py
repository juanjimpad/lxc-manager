from fastapi import APIRouter, Depends, Request

from ...core import auth
from ...core.templates import templates
from . import audit

router = APIRouter()


@router.post("/security/{vmid}/refresh")
def refresh_security(request: Request, vmid: int, _=Depends(auth.require_login)):
    audit.run_audit(vmid)
    sec = audit.get_evaluated(vmid)
    return templates.TemplateResponse(
        "_security_section.html", {"request": request, "vmid": vmid, "sec": sec}
    )
