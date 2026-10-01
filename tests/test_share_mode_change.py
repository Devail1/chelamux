"""🔁 CMX-421 — change a LIVE share's mode (view ⇄ typing ⇄ UNSANDBOXED) in place.

The share keeps its bridge, so the link and pairing code don't change and a guest who
already joined keeps the connection. Down to view applies at once with no confirmation
and is judged on the very next input frame; up passes the SAME gates as minting a share
with that mode (``app._access_gate``). Every change is audited (``share.mode_changed``).

Same stubs as tests/test_share_typing_gate.py: the sandbox probes, the pane writer and
the relay sends are faked; conftest gives each test its own ``CHELA_DIR``.
"""
from __future__ import annotations

import json

import pytest

from chela import collab_stream as cs
from chela.dashboard import app as dash
from tests import test_share_typing_gate as gate
from tests.test_share_typing_gate import _bridge, _events, _joiner, _not_sandboxed, _type

# The gate suite's fixtures, re-bound here so pytest finds them by name.
sandbox, typing_on, typing_off = gate.sandbox, gate.typing_on, gate.typing_off


def _notices(sent):
    return [json.loads(pt)["msg"] for typ, pt in sent
            if typ == cs.e2e.T_CTL and b'"notice"' in pt]


# --- the bridge: set_mode ---------------------------------------------------------------

def test_downgrade_drops_the_very_next_input_frame(monkeypatch, sandbox, typing_on):
    b, _clock, forwarded, sent = _bridge(monkeypatch, allow_typing=True)
    j = _joiner(b)
    _type(b, j, b"a")
    assert forwarded == [b"a"]
    b.set_mode(cs.MODE_VIEW, changed_by="op@example", window="shell-3")
    _type(b, j, b"b")
    assert forwarded == [b"a"], "a downgrade must drop the next frame"
    assert b.mode() == cs.MODE_VIEW
    assert "View only now." in _notices(sent)


def test_downgrade_from_unsandboxed_ends_the_override(monkeypatch, typing_on):
    _not_sandboxed(monkeypatch)
    b, _clock, forwarded, _sent = _bridge(monkeypatch)
    b.set_mode(cs.MODE_UNSANDBOXED, changed_by="op@example", window="shell-3", ttl_s=600.0)
    j = _joiner(b)
    _type(b, j, b"a")
    assert forwarded == [b"a"]
    b.set_mode(cs.MODE_VIEW, changed_by="op@example", window="shell-3")
    _type(b, j, b"b")
    assert forwarded == [b"a"]
    assert len(_events("share.unsandboxed_revoked")) == 1


def test_every_change_is_audited_from_to_by(monkeypatch, sandbox, typing_on):
    b, _clock, _f, _s = _bridge(monkeypatch)
    b.set_mode(cs.MODE_TYPING, changed_by="op@example", window="shell-3")
    b.set_mode(cs.MODE_VIEW, changed_by="op@example", window="shell-3")
    got = [(e["payload"]["from"], e["payload"]["to"], e["payload"]["by"])
           for e in _events("share.mode_changed")]
    assert got == [("view", "typing", "op@example"), ("typing", "view", "op@example")]


def test_same_mode_is_a_no_op(monkeypatch, sandbox, typing_on):
    b, _clock, _f, sent = _bridge(monkeypatch)
    assert b.set_mode(cs.MODE_VIEW, changed_by="op@example")["changed"] is False
    assert _events("share.mode_changed") == [] and _notices(sent) == []


# --- the route: /api/term/<wid>/share-mode, on the REAL bridge ---------------------------

@pytest.fixture
def live_share(monkeypatch):
    """A share minted through the real /share route + start_bridge, with only the pump
    threads, the pane writer and the relay sends stubbed."""
    monkeypatch.setattr(dash.config, "COLLAB_RELAY", "wss://relay.example")
    monkeypatch.setattr(cs.Bridge, "start", lambda self: self)
    monkeypatch.setattr(cs.Bridge, "_forward_input",
                        lambda self, data: self.__dict__.setdefault("fwd", []).append(data))
    monkeypatch.setattr(cs.Bridge, "_seal_send",
                        lambda self, typ, pt: self.__dict__.setdefault("sent", []).append((typ, pt)))
    monkeypatch.setattr(dash, "_terminals_port_map", lambda: {"@9": 5301})
    monkeypatch.setattr(dash, "_require_terminals", lambda: None)
    monkeypatch.setattr(dash, "_window_name", lambda wid: "shell-3")
    monkeypatch.setattr(cs, "_window_dims", lambda wid: (80, 24))
    dash._SHARED.clear()
    dash._share_info.clear()
    cs._bridges.pop("@9", None)

    def mint(mode="view"):
        r = dash.app.test_client().post("/api/term/@9/share", json={"on": True, "mode": mode})
        assert r.status_code == 200, r.get_json()
        return r.get_json()

    yield mint
    cs._bridges.pop("@9", None)
    dash._SHARED.clear()
    dash._share_info.clear()


def _switch(mode, **extra):
    return dash.app.test_client().post("/api/term/@9/share-mode", json={"mode": mode, **extra})


def _forwarded(b):
    return b.__dict__.get("fwd", [])


def test_view_to_typing_lets_an_already_joined_guest_type(live_share, sandbox, typing_on):
    """⭐ ACCEPTED: view → typing on a verified sandboxed session — the guest who joined
    under view only types with no rejoin, and the link + code are unchanged."""
    minted = live_share("view")
    b = cs._bridges["@9"]
    j = _joiner(b)
    _type(b, j, b"x")
    assert _forwarded(b) == [], "view only before the change"
    r = _switch("typing")
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["mode"] == cs.MODE_TYPING and (body["from"], body["to"]) == ("view", "typing")
    assert cs._bridges["@9"] is b, "the same bridge — never re-minted"
    assert (body["pairing_code"], body["join_url"]) == (minted["pairing_code"], minted["join_url"])
    assert dash._share_info["@9"]["pairing_code"] == minted["pairing_code"]
    _type(b, j, b"ls\r")
    assert _forwarded(b) == [b"ls\r"]
    assert "You can type now." in _notices(b.sent)
    (ev,) = _events("share.mode_changed")
    assert ev["payload"]["from"] == "view" and ev["payload"]["to"] == "typing"


def test_upgrade_to_typing_on_a_non_sandboxed_window_is_refused(monkeypatch, live_share, typing_on):
    _not_sandboxed(monkeypatch)
    live_share("view")
    r = _switch("typing")
    assert r.status_code == 403
    assert r.get_json()["error"] == dash.NOT_SANDBOXED_REASON
    assert cs._bridges["@9"].mode() == cs.MODE_VIEW
    assert _events("share.mode_changed") == []


def test_upgrade_to_typing_with_the_setting_off_is_refused(live_share, sandbox, typing_off):
    live_share("view")
    r = _switch("typing")
    assert r.status_code == 403
    assert r.get_json()["error"] == dash.TYPING_OFF_REASON
    assert cs._bridges["@9"].mode() == cs.MODE_VIEW


def test_upgrade_to_unsandboxed_without_the_typed_name_is_refused(monkeypatch, live_share, typing_on):
    _not_sandboxed(monkeypatch)
    live_share("view")
    for extra in ({}, {"confirm": "shell-4"}):
        r = _switch("unsandboxed", **extra)
        assert r.status_code == 403
        assert r.get_json()["error"] == dash.CONFIRM_REASON
    assert cs._bridges["@9"].mode() == cs.MODE_VIEW
    assert _events("share.unsandboxed_granted") == []


def test_upgrade_to_unsandboxed_with_the_typed_name_binds_one_joiner(monkeypatch, live_share, typing_on):
    _not_sandboxed(monkeypatch)
    minted = live_share("view")
    b = cs._bridges["@9"]
    first, second = _joiner(b), _joiner(b)
    r = _switch("unsandboxed", confirm="shell-3")
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["mode"] == cs.MODE_UNSANDBOXED and r.get_json()["expires_at"]
    assert r.get_json()["pairing_code"] == minted["pairing_code"]
    _type(b, first, b"whoami\r")
    _type(b, second, b"id\r")
    assert _forwarded(b) == [b"whoami\r"], "the first guest to type after the upgrade is bound"
    assert len(_events("share.unsandboxed_granted")) == 1
    assert [e["payload"]["to"] for e in _events("share.mode_changed")] == ["unsandboxed"]


def test_downgrade_needs_no_confirmation_and_no_setting(monkeypatch, live_share, typing_on):
    _not_sandboxed(monkeypatch)
    live_share("view")
    assert _switch("unsandboxed", confirm="shell-3").status_code == 200
    monkeypatch.setenv("CHELA_SHARE_TYPING", "false")   # even with typing since turned off
    r = _switch("view")
    assert r.status_code == 200 and r.get_json()["mode"] == cs.MODE_VIEW
    assert cs._bridges["@9"].mode() == cs.MODE_VIEW


def test_mode_change_on_an_unshared_window_is_404(live_share, typing_on):
    assert _switch("view").status_code == 404


def test_unknown_mode_is_400(live_share, typing_on):
    live_share("view")
    assert _switch("root").status_code == 400


def test_the_mint_route_still_never_re_mints_a_live_share(live_share, sandbox, typing_on):
    minted = live_share("view")
    r = dash.app.test_client().post("/api/term/@9/share", json={"on": True, "mode": "typing"})
    assert r.status_code == 409
    assert dash._share_info["@9"]["pairing_code"] == minted["pairing_code"]
    assert _events("share.mode_changed") == []
