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


def test_telegram_bridge_off_when_daemon_not_running(client, monkeypatch):
    # Detection is by process, not env — the dashboard has no bridge creds.
    monkeypatch.setattr(dash, "_telegram_bridge_running", lambda: False)
    tg = _items(client.get("/api/settings").get_json())["Telegram bridge"]
    assert tg["on"] is False
    assert tg["state"] == "Off"


def test_telegram_bridge_connected_hides_secrets(client, monkeypatch):
    monkeypatch.setattr(dash, "_telegram_bridge_running", lambda: True)
    # Even if the bridge's secrets happen to be in the env, they must NEVER ride
    # along in the status detail (it reports only a bound count).
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:SECRET-TOKEN")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    tg = _items(client.get("/api/settings").get_json())["Telegram bridge"]
    assert tg["on"] is True
    assert tg["state"] == "Connected"
    assert "SECRET-TOKEN" not in tg["detail"]
    assert "api.telegram.org" not in tg["detail"]


def test_work_dispatcher_row_reports_a_broken_workflow(client, monkeypatch, tmp_path):
    # A WORKFLOW.md that stopped parsing is the one dispatcher fault an operator
    # MUST see: the daemon stays up on its last-good config but starts no new
    # work, and a log line nobody reads is not a notification.
    bad = tmp_path / "WORKFLOW.md"
    bad.write_text("---\ntracker: [unclosed\n---\n")
    monkeypatch.setattr(dash, "_discover_dispatch_workflows", lambda runs: [bad])

    payload = client.get("/api/settings").get_json()
    row = _items(payload)["Work dispatcher"]

    assert row["on"] is False
    assert row["state"] == "Blocked"
    assert "last good config" in row["detail"]
    assert payload["workflow_errors"][0]["path"] == str(bad)


def test_work_dispatcher_row_is_clean_when_the_workflow_parses(client, monkeypatch, tmp_path):
    good = tmp_path / "WORKFLOW.md"
    good.write_text("---\nproject_key: CMX\n---\nseed\n")
    monkeypatch.setattr(dash, "_discover_dispatch_workflows", lambda runs: [good])

    payload = client.get("/api/settings").get_json()
    row = _items(payload)["Work dispatcher"]

    assert row["on"] is True
    assert row["state"] == "1 workflow"
    assert payload["workflow_errors"] == []


def test_notify_host_redacts_telegram_token():
    # A Telegram sendMessage URL carries the bot token in its PATH — the status
    # detail must expose only the host, never the token.
    url = "https://api.telegram.org/bot123456:SECRET-TOKEN/sendMessage?chat_id=42"
    host = dash._notify_host(url)
    assert host == "api.telegram.org"
    assert "SECRET-TOKEN" not in host
    assert "123456" not in host
