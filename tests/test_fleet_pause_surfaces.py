"""CMX-252: the dashboard and Telegram front doors onto ``chela dispatch --pause --all`` /
``--resume`` (CMX-214 slice 4).

``fleet.pause_all`` / ``fleet.resume_all`` are replaced by spies that record whether the
hold stood at the moment they ran: the order (hold before pause, resume before release) is
the contract, the same one ``chela.main.cmd_dispatch_hold`` keeps.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from chela import config, fleet, hold
from chela.dashboard import app as dash
from chela.telegram import fleetcmd

STATE = {"since": 1000.0, "by": "dashboard",
         "agents": {"T2": {"wid": "@6", "state": "finishing", "finishing": "judge sweep"},
                    "T1": {"wid": "@5", "state": "interrupted"}},
         "judges": {"T3": {"pids": [11, 12, 13], "frozen_at": 1060.0}}}


@pytest.fixture
def spies(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path)
    world = {"calls": [], "resumed": None, "pause_raises": None}

    def pause_all(by=""):
        world["calls"].append(("pause_all", by, hold.active() is not None))
        if world["pause_raises"]:
            raise world["pause_raises"]
        return {**STATE, "by": by}

    def resume_all():
        world["calls"].append(("resume_all", hold.active() is not None))
        return world["resumed"]

    monkeypatch.setattr(fleet, "pause_all", pause_all)
    monkeypatch.setattr(fleet, "resume_all", resume_all)
    return world


@pytest.fixture
def client():
    return dash.app.test_client()


# -- fleet.snapshot: what the dashboard draws ---------------------------------------------

def test_snapshot_lists_agents_and_judges_sorted_with_since_and_the_holds_expiry():
    held = hold.Hold(reason="", by="dashboard", pid=1, created_at=1000.0, expires_at=2800.0)
    snap = fleet.snapshot(STATE, held, now=1200.0)
    assert snap == {
        "expires_at": 2800.0, "expires_in": "26m",
        "since": 1000.0, "since_ago": "3m", "by": "dashboard",
        "agents": [{"task": "T1", "wid": "@5", "state": "interrupted", "finishing": None},
                   {"task": "T2", "wid": "@6", "state": "finishing",
                    "finishing": "judge sweep"}],
        "judges": [{"task": "T3", "frozen_at": 1060.0, "frozen_for": "2m", "processes": 3}],
        "services": [],
    }


def test_snapshot_without_a_hold_has_no_expiry_and_tolerates_missing_times():
    snap = fleet.snapshot({"agents": {}, "judges": {"T9": {}}}, None, now=5.0)
    assert (snap["expires_at"], snap["expires_in"], snap["since_ago"], snap["by"]) == (
        None, None, None, "")
    assert snap["judges"] == [{"task": "T9", "frozen_at": None, "frozen_for": None,
                               "processes": 0}]


def test_snapshot_of_a_hold_that_lapsed_has_no_expiry():
    held = hold.Hold(reason="", by="x", pid=1, created_at=0.0, expires_at=100.0)
    assert fleet.snapshot(STATE, held, now=100.0)["expires_in"] is None
    assert fleet.snapshot(STATE, held, now=99.0)["expires_in"] == "1s"


def test_snapshot_is_none_when_no_fleet_pause_is_on_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path)
    assert fleet.snapshot() is None
    fleet.path().write_text(json.dumps(STATE), encoding="utf-8")
    assert [a["task"] for a in fleet.snapshot()["agents"]] == ["T1", "T2"]


def test_resume_fleet_resumes_while_held_then_releases(spies):
    hold.take(reason="r", by="x")
    spies["resumed"] = {"since": 1.0, "resumed": {}, "thawed": {}}
    resumed, released = fleet.resume_fleet(by="telegram")
    assert spies["calls"] == [("resume_all", True)]
    assert resumed == spies["resumed"]
    assert released is not None and released.by == "x"
    assert hold.active() is None
    assert hold.last_release()["by"] == "telegram"


# -- the dashboard routes -----------------------------------------------------------------

@pytest.mark.parametrize("body, fleet_paused", [
    ({"all": True}, True),
    ({}, False),
    ({"all": False}, False),
    ({"all": "true"}, False),          # only a JSON true is --all; a string is not
])
def test_pause_route_pauses_the_fleet_only_for_all_true_and_after_the_hold(
        spies, client, body, fleet_paused):
    resp = client.post("/api/dispatcher/pause", json=body)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True and data["dispatch_hold"]["by"] == "dashboard"
    assert hold.active() is not None
    if fleet_paused:
        assert spies["calls"] == [("pause_all", "dashboard", True)]
        assert [a["task"] for a in data["fleet_pause"]["agents"]] == ["T1", "T2"]
        assert data["fleet_pause"]["expires_in"] in ("29m", "30m")   # the default TTL
    else:
        assert spies["calls"] == []
        assert "fleet_pause" not in data


def test_pause_all_route_that_cannot_take_the_hold_pauses_nobody(spies, client, monkeypatch):
    def refuse(**_k):
        raise PermissionError("read-only")             # an OSError subclass

    monkeypatch.setattr(hold, "take", refuse)
    resp = client.post("/api/dispatcher/pause", json={"all": True})
    assert resp.status_code == 500
    assert resp.get_json()["error"] == "could not take the hold: read-only"
    assert spies["calls"] == []


def test_pause_all_route_whose_fleet_pause_fails_says_the_hold_stands(spies, client):
    spies["pause_raises"] = RuntimeError("tmux gone")
    resp = client.post("/api/dispatcher/pause", json={"all": True})
    assert resp.status_code == 500
    data = resp.get_json()
    assert data["ok"] is False
    assert data["error"] == "dispatch is held, but pausing the agents failed: tmux gone"
    assert data["dispatch_hold"]["by"] == "dashboard"
    assert hold.active() is not None


def test_resume_route_resumes_the_fleet_while_held_then_releases(spies, client):
    hold.take(reason="r", by="dashboard")
    spies["resumed"] = {"since": 1.0, "resumed": {"T1": True}, "thawed": {"T3": True}}
    resp = client.post("/api/dispatcher/resume")
    assert resp.status_code == 200
    data = resp.get_json()
    assert spies["calls"] == [("resume_all", True)]
    assert data["fleet_resumed"] == spies["resumed"]
    assert data["released"]["by"] == "dashboard"
    assert hold.active() is None


def test_resume_route_whose_fleet_resume_fails_keeps_the_hold(spies, client, monkeypatch):
    hold.take(reason="r", by="dashboard")

    def boom():
        raise RuntimeError("tmux gone")

    monkeypatch.setattr(fleet, "resume_all", boom)
    resp = client.post("/api/dispatcher/resume")
    assert resp.status_code == 500
    assert resp.get_json() == {"ok": False, "error": "resuming the agents failed: tmux gone. "
                                                     "Dispatch is still held."}
    assert hold.active() is not None


def test_resume_route_with_neither_a_fleet_pause_nor_a_hold(spies, client):
    data = client.post("/api/dispatcher/resume").get_json()
    assert (data["ok"], data["released"], data["fleet_resumed"]) == (True, None, None)
    assert spies["calls"] == [("resume_all", False)]


def test_dispatcher_payload_carries_the_fleet_pause_and_its_holds_expiry(
        tmp_path, monkeypatch, client):
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path)
    monkeypatch.setattr(dash, "_repo_root_workflow", lambda: None)
    assert client.get("/api/dispatcher").get_json()["fleet_pause"] is None
    hold.take(reason="", ttl_seconds=3600, by="dashboard")
    fleet.path().write_text(json.dumps(STATE), encoding="utf-8")
    snap = client.get("/api/dispatcher").get_json()["fleet_pause"]
    assert [a["task"] for a in snap["agents"]] == ["T1", "T2"]
    assert [j["task"] for j in snap["judges"]] == ["T3"]
    assert snap["expires_in"] in ("59m", "1h00m")
    hold.release()
    assert client.get("/api/dispatcher").get_json()["fleet_pause"]["expires_in"] is None


# -- Telegram: fleetcmd (the replies) ------------------------------------------------------

def test_telegram_pause_takes_the_hold_with_its_reason_then_pauses(spies):
    reply = fleetcmd.pause_all("batch merge")
    held = hold.active()
    assert held is not None and (held.by, held.reason) == ("telegram", "batch merge")
    assert spies["calls"] == [("pause_all", "telegram", True)]
    lines = reply.splitlines()
    assert lines[0].startswith("⏸ Dispatch HELD: held by telegram — batch merge (expires in ")
    assert "  agent T1 @ @5: interrupted" in lines
    assert "  agent T2 @ @6: finishing judge sweep" in lines
    assert lines[-1] == "Resume: /resumeall"


def test_telegram_pause_that_cannot_take_the_hold_pauses_nobody(spies, monkeypatch):
    def refuse(**_k):
        raise PermissionError("read-only")

    monkeypatch.setattr(hold, "take", refuse)
    assert fleetcmd.pause_all() == ("❌ Could not take the hold (read-only). "
                                    "Dispatch is NOT paused.")
    assert spies["calls"] == []


def test_telegram_pause_whose_fleet_pause_fails_says_the_hold_stands(spies):
    spies["pause_raises"] = RuntimeError("tmux gone")
    reply = fleetcmd.pause_all()
    assert reply.startswith("⚠️ Dispatch is held (held by telegram (expires in ")
    assert reply.endswith("), but pausing the agents failed: tmux gone")
    assert hold.active() is not None


def test_telegram_resume_names_what_did_not_land_then_releases(spies):
    hold.take(reason="", by="dashboard")
    spies["resumed"] = {"since": 1.0, "resumed": {"T1": True, "T2": False},
                        "thawed": {"T3": True, "T4": False}}
    assert fleetcmd.resume_all().splitlines() == [
        "▶️ Fleet RESUMED: sent 'resume' to 1 of 2 agent(s).",
        "⚠️ T2: 'resume' was NOT delivered. Tell it by hand.",
        "judge T3: thawed",
        "judge T4: ⚠️ NOT fully thawed. Check it.",
        "▶️ Dispatch RESUMED: released a hold 0s old (dashboard).",
    ]
    assert spies["calls"] == [("resume_all", True)]
    assert hold.active() is None


def test_telegram_resume_whose_fleet_resume_fails_keeps_the_hold(spies, monkeypatch):
    hold.take(reason="r", by="dashboard")

    def boom():
        raise RuntimeError("tmux gone")

    monkeypatch.setattr(fleet, "resume_all", boom)
    assert fleetcmd.resume_all() == ("❌ Resuming the agents failed: tmux gone. "
                                     "Dispatch is still held.")
    assert hold.active() is not None


def test_telegram_resume_with_nothing_paused(spies):
    assert fleetcmd.resume_all() == "Dispatch was not held. Nothing to resume."
    assert spies["calls"] == [("resume_all", False)]


# -- Telegram: the bridge commands ---------------------------------------------------------

OPERATOR = 111


class _Msg:
    def __init__(self, text):
        self.text, self.caption, self.message_thread_id = text, None, None
        self.replies: list[str] = []

    async def reply_text(self, text, **_kw):
        self.replies.append(text)


class _Update:
    def __init__(self, text, chat_id=777):
        self.message = _Msg(text)
        self.callback_query = None
        self.effective_chat = type("Chat", (), {"id": chat_id})()
        self.effective_user = type("User", (), {"id": OPERATOR})()


def _handlers():
    pytest.importorskip("telegram")
    from chela.telegram.inbound import TopicRouter, build_application

    app = build_application("123:fake-token", TopicRouter("777", "@3", "4", sender=None),
                            allowed_users=(OPERATOR,))
    return {getattr(h.callback, "__wrapped__", h.callback).__name__: h.callback
            for group in app.handlers.values() for h in group}


def _send(cb, update):
    asyncio.run(cb(update, None))
    return update.message.replies


def test_the_commands_are_in_the_menu_and_never_shadow_claude_codes_resume():
    from chela.telegram.inbound import BRIDGE_COMMANDS

    names = [name for name, _ in BRIDGE_COMMANDS]
    assert {"pauseall", "resumeall"} <= set(names)
    assert "resume" not in names and "pause" not in names


def test_pauseall_from_general_passes_its_reason_and_resumeall_resumes(spies):
    hs = _handlers()
    # thread id None = General: fleet-wide commands gate on the chat alone, like /new
    replies = _send(hs["_on_pauseall"], _Update("/pauseall@chelabot  batch merge "))
    assert spies["calls"] == [("pause_all", "telegram", True)]
    assert hold.active().reason == "batch merge"
    assert replies and replies[0].startswith("⏸ Dispatch HELD: held by telegram — batch merge")

    spies["resumed"] = {"since": 1.0, "resumed": {"T1": True}, "thawed": {}}
    replies = _send(hs["_on_resumeall"], _Update("/resumeall"))
    assert spies["calls"][-1] == ("resume_all", True)
    assert hold.active() is None
    assert replies[0].splitlines()[0] == "▶️ Fleet RESUMED: sent 'resume' to 1 of 1 agent(s)."


def test_a_bare_pauseall_has_no_reason(spies):
    _send(_handlers()["_on_pauseall"], _Update("/pauseall"))
    assert hold.active().reason == ""


@pytest.mark.parametrize("name", ["_on_pauseall", "_on_resumeall"])
def test_the_commands_from_another_chat_do_nothing_and_say_nothing(spies, name):
    hold.take(reason="keep", by="x")
    assert _send(_handlers()[name], _Update(f"/{name[4:]}", chat_id=999)) == []
    assert spies["calls"] == []
    assert hold.active().reason == "keep"
