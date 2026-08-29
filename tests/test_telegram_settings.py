from app.core import db, telegram
from tests.conftest import csrf_from, login


def test_telegram_settings_persist_and_gate_events(client, monkeypatch):
    sent = []

    def fake_post(url, data=None, timeout=10):
        sent.append(data)

        class R:
            status_code = 200

        return R()

    monkeypatch.setattr(telegram.httpx, "post", fake_post)

    login(client)
    page = client.get("/settings")
    token = csrf_from(page.text)
    r = client.post(
        "/settings/telegram",
        data={
            "csrf_token": token,
            "telegram_token": "123:abc",
            "telegram_chat_id": "42",
            "notify_client_offline": "on",
            # online left unchecked
            "notify_update_failed": "on",
        },
    )
    assert r.status_code == 200
    assert db.get_setting("telegram_token") == "123:abc"
    assert db.get_setting("telegram_chat_id") == "42"
    assert telegram.event_enabled("notify_client_offline")
    assert not telegram.event_enabled("notify_client_online")

    assert telegram.notify("hello", event="notify_client_online") is False
    assert sent == []
    assert telegram.notify("down", event="notify_client_offline") is True
    assert sent[0]["text"] == "down"

    r = client.post("/settings/telegram/test", data={"csrf_token": token})
    assert "Test sent" in r.text
    assert sent[-1]["text"].startswith("homelab-manager")
