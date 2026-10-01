"""CMX-427 — every share mint gets a fresh, non-secret ``share_epoch``.

A share that is stopped and re-created keeps its relay room but gets a new pairing
code. The dashboard's owner presence (presence-owner.js) is keyed from that code, and
a page that didn't run the stop + re-share itself only learns of the rotation from the
``/api/agents`` poll — which must never carry the code. ``share_epoch`` is that signal:
it moves on every mint, is the same in ``/share-info`` and ``/api/agents`` for one
share, and is absent once the share stops.
"""
from __future__ import annotations

from chela import collab_stream as cs
from chela.dashboard import app as dash

from tests.test_api_agents_done import _by_wid, _fleet


def _setup(monkeypatch):
    dash._SHARED.clear()
    dash._share_info.clear()
    monkeypatch.setattr(dash, "_terminals_port_map", lambda: {"@9": 5301})
    monkeypatch.setattr(dash, "_require_terminals", lambda: None)
    monkeypatch.setattr(dash.config, "COLLAB_RELAY", "wss://relay.example")
    monkeypatch.setattr(cs, "_window_dims", lambda wid: (80, 24))
    codes = iter(["CODEA", "CODEB"])
    monkeypatch.setattr(cs, "start_bridge", lambda wid, on_revoke=None, **kw: next(codes))
    monkeypatch.setattr(cs, "join_url", lambda wid: "https://relay/j/r-tty")
    monkeypatch.setattr(cs, "stop_bridge", lambda wid: None)
    monkeypatch.setattr(cs, "share_state", lambda wid: None)


def test_a_re_created_share_gets_a_new_epoch_on_every_surface(monkeypatch):
    _setup(monkeypatch)
    c = dash.app.test_client()
    try:
        first = c.post("/api/term/@9/share", json={"on": True}).get_json()
        info1 = c.get("/api/term/@9/share-info").get_json()
        assert first["share_epoch"] == info1["share_epoch"] is not None
        with _fleet(status={"@1": "idle", "@9": "idle"}):
            row = _by_wid(c)["@9"]
        assert row["share_epoch"] == info1["share_epoch"]
        assert "pairing_code" not in row   # the code itself stays owner-only

        c.post("/api/term/@9/share", json={"on": False})
        with _fleet(status={"@1": "idle", "@9": "idle"}):
            assert _by_wid(c)["@9"]["share_epoch"] is None   # stopped ⇒ no epoch

        second = c.post("/api/term/@9/share", json={"on": True}).get_json()
        info2 = c.get("/api/term/@9/share-info").get_json()
        assert info2["pairing_code"] == "CODEB"
        assert second["share_epoch"] == info2["share_epoch"] != info1["share_epoch"]
        with _fleet(status={"@1": "idle", "@9": "idle"}):
            assert _by_wid(c)["@9"]["share_epoch"] == info2["share_epoch"]
    finally:
        dash._SHARED.clear()
        dash._share_info.clear()
