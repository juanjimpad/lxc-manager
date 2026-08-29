"""Stable JSON shapes for /api/v1. Adapters map service dicts through these."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    user: str


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str


class StatusOut(BaseModel):
    status: str
    vmid: Optional[int] = None
    detail: Optional[str] = None


class UpdateViaOut(BaseModel):
    kind: str
    target: Optional[str] = None


class ScheduleIn(BaseModel):
    cron: str
    enabled: bool


class ScheduleOut(BaseModel):
    cron: str
    enabled: bool
    next_run: Optional[str] = None


class RunOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    vmid: int
    started_at: str
    finished_at: Optional[str] = None
    status: str
    summary: Optional[str] = None
    detail: Optional[str] = None


class GuestOut(BaseModel):
    """One guest as stored in SQLite, plus optional list-view extras."""

    model_config = ConfigDict(extra="ignore")

    vmid: int
    node: str
    name: str
    type: str
    app_type: str
    tags: str
    maxmem: int
    maxcpu: int
    ip: str
    os_family: str
    os_id: str
    update_supported: bool
    last_seen: str
    last_status: Optional[str] = None
    last_run: Optional[str] = None
    cron: Optional[str] = None
    enabled: Optional[bool] = None
    next_run: Optional[str] = None
    security: Optional[dict[str, Any]] = None
    backups: Optional[dict[str, Any]] = None


class GuestListOut(BaseModel):
    guests: list[GuestOut]


class GuestDetailOut(BaseModel):
    guest: GuestOut
    schedule: Optional[ScheduleOut] = None
    update_via: UpdateViaOut
    next_run: Optional[str] = None
    runner_pending: bool
    runs: list[RunOut] = Field(default_factory=list)
    security: dict[str, Any]
    backups: dict[str, Any]
    backup_pending: bool
    backup_runs: list[RunOut] = Field(default_factory=list)


class KernelOut(BaseModel):
    kernel: str


class BackupsSectionOut(BaseModel):
    backups: dict[str, Any]
    pending: bool
    runs: list[RunOut] = Field(default_factory=list)


class VersionOut(BaseModel):
    enabled: bool
    current: str
    latest: Optional[str] = None
    update_available: bool
    applying: bool = False
    error: Optional[str] = None
