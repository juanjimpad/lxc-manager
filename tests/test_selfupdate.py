from pathlib import Path

from app.core import selfupdate


def test_current_revision_is_git_sha():
    rev = selfupdate.current_revision()
    assert rev != "unknown"
    assert len(rev) >= 7


def test_request_restart_writes_flag_in_tests(tmp_path, monkeypatch):
    monkeypatch.setenv("HLMGR_TESTING", "1")
    from app.core import config

    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "x.db"))
    selfupdate.request_restart()
    assert (tmp_path / "RESTART_REQUESTED").is_file()


def test_remote_status_skips_fetch_in_tests():
    info = selfupdate.remote_status()
    assert info["up_to_date"] is True
    assert info["version"]
