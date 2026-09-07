"""Tag parsing and panel-refresh scheduling."""
from datetime import datetime, timedelta
from unittest.mock import patch

from app.core.proxmox import parse_tags
from app.modules.update import scheduler as update_scheduler


def test_parse_tags_semicolon_and_whitespace():
    assert parse_tags("managed; auto-update ;docker") == [
        "managed",
        "auto-update",
        "docker",
    ]


def test_parse_tags_commas():
    assert parse_tags("managed,auto-update") == ["managed", "auto-update"]


def test_parse_tags_empty():
    assert parse_tags(None) == []
    assert parse_tags("") == []


def test_start_schedules_panel_refresh_with_real_next_run(monkeypatch):
    """next_run_time=None pauses APScheduler jobs — regression guard."""
    monkeypatch.setattr(update_scheduler, "sync_guests_and_schedules", lambda: None)
    # Fresh scheduler instance state: stop if a previous test started it.
    if update_scheduler.scheduler.running:
        update_scheduler.scheduler.shutdown(wait=False)

    with patch.object(update_scheduler.scheduler, "start") as start_mock:
        update_scheduler.start()
        start_mock.assert_called_once()

    job = update_scheduler.scheduler.get_job("panel-refresh")
    assert job is not None
    assert job.next_run_time is not None
    assert job.next_run_time > datetime.now(update_scheduler.scheduler.timezone)
    assert job.next_run_time < datetime.now(update_scheduler.scheduler.timezone) + timedelta(
        minutes=5
    )
    # Interval trigger is 6 hours
    assert job.trigger.interval == timedelta(hours=6)

    # Clean up pending jobs without leaving a running background thread.
    update_scheduler.scheduler.remove_job("panel-refresh")
    if update_scheduler.scheduler.get_job("selfupdate-check"):
        update_scheduler.scheduler.remove_job("selfupdate-check")
