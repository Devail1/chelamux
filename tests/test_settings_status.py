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
    for label in ("tmux session", "Daemon", "Telegram bridge", "Collaboration relay",
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


def _daemon(monkeypatch, *, dispatch_on: bool):
    """Pin what the RUNNING daemon published. The dashboard is a different process from
    the daemon, so it must never infer this from its own env — and a test must never read
    the developer's real ~/.chela/daemon.json (see conftest)."""
    monkeypatch.setattr(dash.capabilities, "live", lambda: {
        "pid": 4242,
        "capabilities": [{"key": "dispatch", "label": "Work dispatcher", "on": dispatch_on}],
    })


def test_work_dispatcher_row_reports_a_broken_workflow(client, monkeypatch, tmp_path):
    # A WORKFLOW.md that stopped parsing is the one dispatcher fault an operator
    # MUST see: the daemon stays up on its last-good config but starts no new
    # work, and a log line nobody reads is not a notification.
    bad = tmp_path / "WORKFLOW.md"
    bad.write_text("---\ntracker: [unclosed\n---\n")
    monkeypatch.setattr(dash, "_discover_dispatch_workflows", lambda runs: [bad])
    _daemon(monkeypatch, dispatch_on=True)

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
    _daemon(monkeypatch, dispatch_on=True)

    payload = client.get("/api/settings").get_json()
    row = _items(payload)["Work dispatcher"]

    assert row["on"] is True
    assert row["state"] == "1 workflow"
    assert payload["workflow_errors"] == []


def test_work_dispatcher_row_is_OFF_when_the_running_daemon_has_it_off(client, monkeypatch, tmp_path):
    """The nine-hour bug, seen from the drawer. A WORKFLOW.md exists on disk and
    auto-discovery finds it — so this row read "1 workflow · On" while the daemon,
    whose CHELA_DISPATCH_WORKFLOWS was empty, dispatched nothing and reconciled nothing.
    What the filesystem says is not what the daemon does: the daemon wins."""
    good = tmp_path / "WORKFLOW.md"
    good.write_text("---\nproject_key: CMX\n---\nseed\n")
    monkeypatch.setattr(dash, "_discover_dispatch_workflows", lambda runs: [good])
    _daemon(monkeypatch, dispatch_on=False)

    row = _items(client.get("/api/settings").get_json())["Work dispatcher"]

    assert row["on"] is False
    assert row["state"] == "Off"
    assert "reconcile" in row["detail"]            # both, or the row still under-reports
    assert "CHELA_DISPATCH_WORKFLOWS" in row["detail"]


def test_daemon_row_reports_a_daemon_that_is_not_running(client, monkeypatch):
    monkeypatch.setattr(dash.capabilities, "live", lambda: None)
    row = _items(client.get("/api/settings").get_json())["Daemon"]
    assert row["on"] is False and row["state"] == "Off"
    assert "chela run" in row["detail"]


def test_daemon_row_reports_a_running_daemon(client, monkeypatch):
    _daemon(monkeypatch, dispatch_on=True)
    row = _items(client.get("/api/settings").get_json())["Daemon"]
    assert row["on"] is True and row["state"] == "Running"
    assert "4242" in row["detail"]


def test_notify_host_redacts_telegram_token():
    # A Telegram sendMessage URL carries the bot token in its PATH — the status
    # detail must expose only the host, never the token.
    url = "https://api.telegram.org/bot123456:SECRET-TOKEN/sendMessage?chat_id=42"
    host = dash._notify_host(url)
    assert host == "api.telegram.org"
    assert "SECRET-TOKEN" not in host
    assert "123456" not in host


def test_work_dispatcher_row_reports_a_HELD_queue(client, monkeypatch, tmp_path):
    # A held queue is the third way this row can lie about work getting done: configured,
    # daemon up — and claiming nothing, deliberately. Read from the hold FILE, because a
    # hold taken after the daemon booted is not in its startup snapshot.
    _daemon(monkeypatch, dispatch_on=True)
    monkeypatch.setattr(dash.config, "CHELA_DIR", tmp_path)
    monkeypatch.setattr(dash.hold.config, "CHELA_DIR", tmp_path)
    dash.hold.take(reason="rewriting the queue", ttl_seconds=600, by="@0")

    data = client.get("/api/settings").get_json()
    row = _items(data)["Work dispatcher"]

    assert row["on"] is False and row["state"] == "Held"
    assert "rewriting the queue" in row["detail"]
    assert "Reconciliation continues" in row["detail"]   # the hold pauses CLAIMS only
    # ...and a machine-readable twin, so a UI can act on it rather than parse a sentence.
    assert data["dispatch_hold"]["reason"] == "rewriting the queue"


def test_a_released_queue_leaves_no_trace_on_the_row(client, monkeypatch, tmp_path):
    _daemon(monkeypatch, dispatch_on=True)
    monkeypatch.setattr(dash.config, "CHELA_DIR", tmp_path)
    monkeypatch.setattr(dash.hold.config, "CHELA_DIR", tmp_path)
    dash.hold.take(ttl_seconds=600)
    dash.hold.release()

    data = client.get("/api/settings").get_json()
    assert _items(data)["Work dispatcher"]["state"] != "Held"
    assert data["dispatch_hold"] is None


# --- "update" payload: CMX-199, the Settings-drawer twin of doctor's repo.upstream_synced --

def test_update_payload_reports_behind_count(client, monkeypatch):
    monkeypatch.setattr(dash.update, "commits_behind",
                        lambda fetch=True: dash.update.UpdateStatus(
                            ok=True, behind=5, ahead=0, branch="dev"))
    data = client.get("/api/settings").get_json()
    assert data["update"] == {"ok": True, "behind": 5, "ahead": 0, "branch": "dev"}


def test_update_payload_is_clean_when_up_to_date(client, monkeypatch):
    monkeypatch.setattr(dash.update, "commits_behind",
                        lambda fetch=True: dash.update.UpdateStatus(
                            ok=True, behind=0, ahead=0, branch="dev"))
    data = client.get("/api/settings").get_json()
    assert data["update"]["behind"] == 0


def test_update_payload_degrades_gracefully_on_a_pip_install(client, monkeypatch):
    def _boom(fetch=True):
        raise dash.update.NotAGitCheckout("not a git checkout")
    monkeypatch.setattr(dash.update, "commits_behind", _boom)
    data = client.get("/api/settings").get_json()
    assert data["update"]["ok"] is False
    assert data["update"]["git"] is False
