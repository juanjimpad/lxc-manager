"""HTML adapters: Pico + htmx. Same public URLs as before; they only call services."""
from fastapi import APIRouter

from . import auth, backups, security, update

router = APIRouter()
router.include_router(auth.router)
router.include_router(update.router)
router.include_router(security.router)
router.include_router(backups.router)
