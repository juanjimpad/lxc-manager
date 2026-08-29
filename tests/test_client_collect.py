from pathlib import Path

from client import homelab_client as hc


def test_sign_matches_manager():
    from app.core.client_auth import sign as mgr_sign

    args = ("key", "1", "abc", "POST", "/api/v1/heartbeat", b"{}")
    assert hc.sign(*args) == mgr_sign(*args)


def test_meminfo(tmp_path, monkeypatch):
    proc = tmp_path / "meminfo"
    proc.write_text("MemTotal:        2048000 kB\nMemAvailable:    1024000 kB\n")
    monkeypatch.setattr(hc, "_read", lambda p: proc.read_text() if p == "/proc/meminfo" else "")
    used, total = hc.mem_info()
    assert total == 2048000 * 1024
    assert used == (2048000 - 1024000) * 1024


def test_detect_capabilities_includes_self_update():
    caps = hc.detect_capabilities("ubuntu")
    assert "self-update" in caps


def test_unknown_job_kind():
    ok, detail = hc.run_job({"kind": "explode"}, Path("."))
    assert ok is False
    assert "unknown" in detail


def test_print_metrics_exits_zero(capsys):
    rc = hc.main(["--print-metrics"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "hostname" in out
    assert "capabilities" in out
