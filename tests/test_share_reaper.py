"""Reaper + revoke unit tests for the live-dashboard share integration (CHUNK 3):
the deferred half of "no share outlives its session". We exercise _reap_shares /
_revoke_share directly, stubbing the tmux + bridge side effects so no ttyd or
relay is needed.
"""

from __future__ import annotations

import pytest

from chela.dashboard import app as dash


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    # Clean share state per test; stub the real-world side effects.
    dash._SHARED.clear()
    dash._share_info.clear()
    dash._share_dead_since.clear()
    stopped = []
    monkeypatch.setattr(dash.collab_stream, "stop_bridge", lambda w: stopped.append(w))
    monkeypatch.setattr(dash, "_unpin_grid", lambda w: None)
    yield stopped
    dash._SHARED.clear()
    dash._share_info.clear()
    dash._share_dead_since.clear()


def test_reaper_keeps_live_share(monkeypatch, _isolate):
    monkeypatch.setattr(dash, "_terminals_port_map", lambda: {"@9": 5301})
    dash._SHARED["@9"] = {"cols": 137, "rows": 39}
    dash._reap_shares({"@9": True})            # window present, agent running
    assert "@9" in dash._SHARED
    assert _isolate == []                      # no revoke


def test_reaper_grace_then_revoke_on_agent_end(monkeypatch, _isolate):
    monkeypatch.setattr(dash, "_terminals_port_map", lambda: {"@9": 5301})
    dash._SHARED["@9"] = {"cols": 137, "rows": 39}
    dash._share_info["@9"] = {"pairing_code": "X", "join_url": "u"}

    # Agent ended (claude_running False) but ttyd still alive → within grace, kept.
    t = [1000.0]
    monkeypatch.setattr(dash.time, "monotonic", lambda: t[0])
    dash._reap_shares({"@9": False})
    assert "@9" in dash._SHARED                # grace not elapsed
    assert _isolate == []

    # Past the grace → revoked: flag + info dropped, bridge stopped.
    t[0] += dash._SHARE_REAP_GRACE + 1
    dash._reap_shares({"@9": False})
    assert "@9" not in dash._SHARED
    assert "@9" not in dash._share_info
    assert _isolate == ["@9"]                  # stop_bridge called


def test_reaper_revokes_when_window_gone(monkeypatch, _isolate):
    # ttyd reaped (wid absent from the port map) → revoke after grace even though
    # the agents map might still momentarily claim it running.
    monkeypatch.setattr(dash, "_terminals_port_map", lambda: {})
    dash._SHARED["@9"] = {"cols": 137, "rows": 39}
    t = [2000.0]
    monkeypatch.setattr(dash.time, "monotonic", lambda: t[0])
    dash._reap_shares({"@9": True})
    assert "@9" in dash._SHARED                # grace
    t[0] += dash._SHARE_REAP_GRACE + 1
    dash._reap_shares({"@9": True})
    assert "@9" not in dash._SHARED
    assert _isolate == ["@9"]


def test_reaper_blip_does_not_revoke(monkeypatch, _isolate):
    """A transient dead tick that recovers before the grace must NOT revoke."""
    monkeypatch.setattr(dash, "_terminals_port_map", lambda: {"@9": 5301})
    dash._SHARED["@9"] = {"cols": 137, "rows": 39}
    t = [3000.0]
    monkeypatch.setattr(dash.time, "monotonic", lambda: t[0])
    dash._reap_shares({"@9": False})           # blip
    t[0] += 2                                   # < grace
    dash._reap_shares({"@9": True})            # recovered
    assert "@9" in dash._SHARED
    assert "@9" not in dash._share_dead_since   # dead-timer cleared
    assert _isolate == []


def test_revoke_share_is_idempotent(monkeypatch, _isolate):
    monkeypatch.setattr(dash, "_terminals_port_map", lambda: {"@9": 5301})
    dash._SHARED["@9"] = {"cols": 137, "rows": 39}
    dash._share_info["@9"] = {"pairing_code": "X"}
    dash._revoke_share("@9")
    dash._revoke_share("@9")                    # second call must not raise
    assert "@9" not in dash._SHARED
    assert _isolate == ["@9", "@9"]            # stop_bridge is safe to call twice


def test_share_pins_grid(monkeypatch, _isolate):
    """Turning a share ON must PIN the source window to the presenter's dims, so the
    tmux window can't float underneath the stream and desync the joiner's grid."""
    monkeypatch.setattr(dash, "_terminals_port_map", lambda: {"@9": 5301})
    monkeypatch.setattr(dash, "_require_terminals", lambda: None)
    pins = []
    monkeypatch.setattr(dash, "_pin_grid", lambda wid, c, r: pins.append((wid, c, r)))
    monkeypatch.setattr(dash.collab_stream, "start_bridge", lambda wid, on_revoke=None: "CODE")
    monkeypatch.setattr(dash.collab_stream, "join_url", lambda wid: "https://relay/j/room")

    client = dash.app.test_client()
    resp = client.post("/api/term/@9/share", json={"on": True, "cols": 111, "rows": 22})

    assert resp.status_code == 200
    assert dash._SHARED["@9"] == {"cols": 111, "rows": 22}
    assert pins == [("@9", 111, 22)]           # pinned to the posted presenter dims


def test_bridge_resend_on_source_resize(monkeypatch):
    """The bridge watches the source window size and resends a fresh keyframe
    (T_META + full snapshot) ONLY when it changes — so a mid-session resize keeps
    the joiner's xterm grid in lockstep with the reflowed OUTPUT stream."""
    from chela import collab_stream as cs

    sent = []
    dims = {"v": (100, 30)}
    monkeypatch.setattr(cs, "_window_dims", lambda wid: dims["v"])
    monkeypatch.setattr(cs, "_snapshot", lambda wid: b"SNAP")
    b = cs.Bridge("@9")
    monkeypatch.setattr(b, "_seal_send", lambda typ, pt: sent.append(typ))

    b._maybe_resize()                          # first poll: seed _last_dims, send nothing
    assert b._last_dims == (100, 30)
    assert sent == []

    b._maybe_resize()                          # unchanged → still nothing
    assert sent == []

    dims["v"] = (120, 40)                       # source window resized
    b._maybe_resize()
    assert sent == [cs.e2e.T_META, cs.e2e.T_OUTPUT]   # one fresh keyframe
    assert b._last_dims == (120, 40)           # watch advanced, won't re-fire