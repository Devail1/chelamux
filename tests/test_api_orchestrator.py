"""POST /api/orchestrator/{subscribe,release} + GET /status — the pane-title
toggle's HTTP surface (CMX-106).

These wrap ``chela.inbox.register``/``unregister``, which already give the ONE
property that matters here: an ATOMIC take-over of the single ``orchestrator``
slot (register always wins) and a GUARDED release (unregister is a no-op unless
the caller currently holds the slot). This suite proves that property holds
THROUGH the HTTP layer — a route that swapped ``register`` for a check-then-set,
or that let ``release`` clear the slot unconditionally, would reintroduce the
two-live-recipients bug these routes exist to close, and every test below would
go red on that exact mutation.

The decisions LOG itself needs no new test here: chela/inbox.py::tick() already
appends every event to event_log unconditionally (see its own module docstring
and tests/test_inbox.py) — these routes don't touch that write path at all, only
who owns delivery.
"""
from __future__ import annotations

import itertools

import pytest

from chela import agent_manager, epoch, inbox
from chela.dashboard import app as dash

PANE_A = "@11"
PANE_B = "@12"


@pytest.fixture(autouse=True)
def store_file(tmp_path, monkeypatch):
    """Never the real ``~/.chela/inbox.json``."""
    monkeypatch.setenv("CHELA_INBOX_FILE", str(tmp_path / "inbox.json"))
    monkeypatch.delenv("CHELA_ORCHESTRATOR_WID", raising=False)
    monkeypatch.setattr(inbox, "INBOX_ENABLED", True)


@pytest.fixture(autouse=True)
def no_session_identity(monkeypatch):
    """Purity: register()/unregister() resolve a session identity off tmux + /proc
    (CMX-82 self-heal) — stubbed inert so this suite never reaches the live fleet."""
    monkeypatch.setattr(inbox.sessions, "session_of_window", lambda wid, pane_map=None: None)


@pytest.fixture(autouse=True)
def fixed_epoch(monkeypatch):
    """A deterministic tmux epoch, not a live ``tmux display-message`` call."""
    monkeypatch.setattr(epoch, "current", lambda: "1-100")


@pytest.fixture
def windows(monkeypatch):
    live = {PANE_A: "pane-a", PANE_B: "pane-b"}
    monkeypatch.setattr(inbox.discovery, "get_windows_by_id", lambda: dict(live))
    return live


@pytest.fixture(autouse=True)
def live_statuses(monkeypatch):
    """Both panes report ``idle`` by default — a live claude session in each."""
    monkeypatch.setattr(agent_manager, "status_by_wid", lambda: {PANE_A: "idle", PANE_B: "idle"})


@pytest.fixture
def client():
    return dash.app.test_client()


def _subscribe(client, wid):
    return client.post("/api/orchestrator/subscribe", json={"wid": wid})


def _release(client, wid):
    return client.post("/api/orchestrator/release", json={"wid": wid})


def _status(client):
    return client.get("/api/orchestrator/status").get_json()


# --- atomic take-over ------------------------------------------------------

def test_subscribe_registers_the_pane_as_the_sole_owner(client, windows):
    resp = _subscribe(client, PANE_A)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["wid"] == PANE_A
    assert _status(client)["wid"] == PANE_A


def test_a_second_subscribe_is_an_atomic_takeover_not_two_owners(client, windows):
    """🔴 GUARD: two subscribes in a row ⇒ only the second is the owner, ever.

    Corrupt the route to a check-then-set ("refuse if someone already owns it")
    and this goes red on the second assert — PANE_A would still (wrongly) be
    reported the owner, or the call would be refused instead of taking over.
    """
    first = _subscribe(client, PANE_A).get_json()
    assert first["wid"] == PANE_A

    second = _subscribe(client, PANE_B).get_json()
    assert second["ok"] is True
    assert second["wid"] == PANE_B

    # The single source of truth agrees: PANE_B, not PANE_A, not both.
    status = _status(client)
    assert status["wid"] == PANE_B
    assert status["wid"] != PANE_A


def test_subscribe_refuses_a_dead_window(client, windows):
    resp = _subscribe(client, "@999")
    assert resp.status_code == 404
    assert resp.get_json()["ok"] is False
    assert _status(client)["wid"] is None


def test_subscribe_requires_a_wid(client):
    resp = _subscribe(client, "")
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


# --- guarded release ---------------------------------------------------------

def test_release_by_the_current_owner_clears_the_slot(client, windows):
    _subscribe(client, PANE_A)
    resp = _release(client, PANE_A)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert _status(client)["wid"] is None


def test_release_by_a_non_owner_is_a_guarded_no_op(client, windows):
    """🔴 GUARD: a stale/duplicate release from a pane that is NOT the current
    owner must never clear someone else's live registration.

    Corrupt ``api_orchestrator_release`` to call ``inbox.unregister`` unconditionally
    (or to always clear the slot) and this goes red: PANE_B's registration would
    vanish on PANE_A's stale click.
    """
    _subscribe(client, PANE_B)
    resp = _release(client, PANE_A)      # PANE_A never owned it
    assert resp.status_code == 409
    assert resp.get_json()["ok"] is False
    # PANE_B is still the owner — nothing was cleared out from under it.
    assert _status(client)["wid"] == PANE_B


def test_release_requires_a_wid(client):
    resp = _release(client, "")
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


# --- status reporting ---------------------------------------------------------

def test_status_reports_unregistered_when_nobody_has_subscribed(client):
    data = _status(client)
    assert data["wid"] is None
    assert data["state"] == inbox.ADDR_NONE


def test_status_reports_a_dead_pane_as_gone_not_ok(client, windows, monkeypatch):
    """🔴 GUARD: the pane toggle must not read 'live' for an owner whose window
    has no claude running any more — that is exactly the silent-orphan bug this
    task exists to surface (a dangling/gone owner must be VISIBLE, not green).
    """
    _subscribe(client, PANE_A)
    # PANE_A's claude exited — no longer in the live status map.
    monkeypatch.setattr(agent_manager, "status_by_wid", lambda: {PANE_B: "idle"})
    data = _status(client)
    assert data["wid"] == PANE_A
    assert data["state"] == inbox.ADDR_GONE
    assert data["why"]


def test_status_reports_the_queue_depth(client, windows):
    _subscribe(client, PANE_A)
    with inbox.locked_store() as store:
        store["queue"].append({"kind": "finished", "summary": "x", "payload": {}})
    assert _status(client)["queued"] == 1


# --- the SSE `orchestrator` delta ---------------------------------------------
#
# terminals.js's pane buttons and decisions.js's owner chip both repaint off the
# SSE `orchestrator` event (orchestrator_ui.test.mjs proves the CLIENT half: a
# frame arriving repaints them). This proves the SERVER half — that a takeover
# actually PUSHES that frame — which the route tests above never touch: they call
# inbox.register directly, not the polling loop in app.py::_sse_stream.
#
# 🔴 GUARD: stub the diff (`if cur_orch != prev_orch:`) to never fire — e.g.
# `if False and cur_orch != prev_orch:` — and this goes red FAST: a real take-over
# happens, but the generator never yields the frame that would tell a live
# client's SSE listener to repaint, so the very next frame is the idle keepalive.
#
# ⏱️ WHY monotonic is mocked to jump the keepalive interval every call: without it,
# under the corruption above the loop has nothing to yield and spins in real time
# until `time.monotonic()` naturally crosses SSE_KEEPALIVE_INTERVAL (15s) — the
# assert still fires, but ~15s later. A judge that time-boxes each mutation reads
# that slow fail as a hang ("SURVIVED") rather than a clean RED, and the 15s stall
# poisons the shared suite run for sibling guards too. Advancing monotonic makes the
# keepalive fire on the FIRST idle iteration, so the corruption fails in milliseconds.
# The real path is unaffected: the orchestrator frame is yielded earlier in the loop
# body than the keepalive check, so a genuine take-over still returns it first.

def test_a_takeover_pushes_an_sse_orchestrator_frame(client, windows, monkeypatch):
    monkeypatch.setattr(dash, "_sse_windows_snapshot", lambda: {})
    monkeypatch.setattr(dash, "_sse_runs_snapshot", lambda: {})
    monkeypatch.setattr(dash, "_sse_terms_snapshot", lambda: set())
    monkeypatch.setattr(dash, "_sse_log_snapshot", lambda: {})
    monkeypatch.setattr(dash.time, "sleep", lambda _s: None)
    # Each call jumps past the keepalive interval, so an idle iteration emits the
    # keepalive immediately instead of spinning ~15 real seconds for it.
    ticks = itertools.count(0.0, dash.SSE_KEEPALIVE_INTERVAL + 1.0)
    monkeypatch.setattr(dash.time, "monotonic", lambda: next(ticks))

    stream = dash._sse_stream()
    assert next(stream).startswith("event: hello")   # baseline, no owner yet

    _subscribe(client, PANE_A)
    frame = next(stream)

    assert frame.startswith("event: orchestrator\n"), (
        f"a pane took over the slot but the SSE loop never pushed the frame that "
        f"tells a live client to repaint (got: {frame!r})"
    )
    assert f'"wid": "{PANE_A}"' in frame
