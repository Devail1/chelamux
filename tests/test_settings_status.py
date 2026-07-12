"""READ-ONLY "Connections & Status" aggregation for the Settings drawer
(GET /api/settings). Locks in the section/item shape the drawer renders, the
graceful degradation when tmux is unreachable, and — load-bearing for a public
repo — that a notify URL's secret token never rides along in the status detail.
"""

from __future__ import annotations

import pytest

from chela.dashboard import app as dash


@pytest.fixture
def client():
    return dash.app.test_client()


def _items(payload):
    return {it["label"]: it for sec in payload["sections"] for it in sec["items"]}


def test_settings_shape(client, monkeypatch):
    # Deterministic session probe so the row is present regardless of tmux.
    monkeypatch.setattr(dash.discovery, "get_windows_by_id", lambda: {"@1": "a", "@2": "b"})
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    data = resp.get_json()

    titles = [s["title"] for s in data["sections"]]
    assert titles == ["Connections", "Features"]

    items = _items(data)
    # Every documented row is present...
    for label in ("tmux session", "Telegram bridge", "Collaboration relay",
                  "Needs-input notifications",
                  "Terminal wall", "Work dispatcher", "Scheduler", "Tool-call relay"):
        assert label in items, label
    # ...and every item carries the colorblind-safe badge fields.
    for it in items.values():
        assert isinstance(it["on"], bool)
        assert it["state"]
        assert "detail" in it

    sess = items["tmux session"]
    assert sess["on"] is True
    assert sess["state"] == "Connected"
    assert "2 windows" in sess["detail"]


def test_session_probe_degrades_gracefully(client, monkeypatch):
    def _boom():
        raise RuntimeError("tmux down")
    monkeypatch.setattr(dash.discovery, "get_windows_by_id", _boom)
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    sess = _items(resp.get_json())["tmux session"]
    assert sess["on"] is False
    assert sess["state"] == "Unknown"


def test_telegram_bridge_off_by_default(client, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    tg = _items(client.get("/api/settings").get_json())["Telegram bridge"]
    assert tg["on"] is False
    assert tg["state"] == "Off"


def test_telegram_bridge_configured_hides_secrets(client, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:SECRET-TOKEN")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    tg = _items(client.get("/api/settings").get_json())["Telegram bridge"]
    assert tg["on"] is True
    assert tg["state"] == "Configured"
    # Neither the bot token nor the chat id may ride along in the status detail.
    assert "SECRET-TOKEN" not in tg["detail"]
    assert "42" not in tg["detail"]


def test_notify_host_redacts_telegram_token():
    # A Telegram sendMessage URL carries the bot token in its PATH — the status
    # detail must expose only the host, never the token.
    url = "https://api.telegram.org/bot123456:SECRET-TOKEN/sendMessage?chat_id=42"
    host = dash._notify_host(url)
    assert host == "api.telegram.org"
    assert "SECRET-TOKEN" not in host
    assert "123456" not in host
