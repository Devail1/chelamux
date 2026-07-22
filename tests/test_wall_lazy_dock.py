"""``/api/agents`` — the two facts the Wall's lazy tiles are built on (CMX-76).

The rule is the Telegram lazy-bind's (CMX-73), on the second surface a dispatched
worker can occupy: **it must not take a Wall tile until it needs a human.** The
*behaviour* is the client's (terminals.js docks the tile and pops it out — see
``tests/walldock.test.mjs``); what the server owes it is two honest facts per window,
and these are the contracts on them:

* ``dispatched`` is the **run row's** answer, reused verbatim from
  :func:`chela.telegram.reconcile.dispatched_window_ids` — never re-derived, and never
  guessed from a window *name*. Two surfaces asking "is this a dispatched worker?" and
  answering separately is two answers; one of them would eventually be wrong.

* ``needs_human`` must see a **permission gate**. That is the whole load-bearing half:
  a Bash/Edit approval prompt is the likeliest thing to stop a worktree worker, it is
  **never in the transcript**, and ``claude agents --json`` calls the window ``busy``
  while it sits there. Read status alone and the pop-out never fires — the worker stays
  minimized, blocked, forever, with nobody told. It is exactly the CMX-73 failure mode,
  arriving on the wall instead of the phone.

The pane probe's **cost** is a contract too: it is one tmux capture per *dispatched*
window per poll (they are the only windows the wall ever hides, so the only ones whose
answer is load-bearing), not one per window in the fleet.
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

import pytest

from chela.dashboard import app as dash

# A fleet: one human session, two dispatcher-spawned workers.
LIVE = {"orchestrator": "@1", "cmx-76": "@9", "cmx-77": "@10"}

RUNS = [
    {"status": "running", "window_id": "@9", "window_name": "cmx-76"},
    {"status": "running", "window_id": "@10", "window_name": "cmx-77"},
]

# A real Bash permission gate, as Claude Code draws it: nowhere in the transcript, and
# `claude agents --json` says the window is BUSY. The pane is the only witness.
BASH_GATE_PANE = """\
 Bash command
   rm -rf build/

 Do you want to proceed?
 ❯ 1. Yes
   2. Yes, and don't ask again
   3. No, and tell Claude what to do differently (esc)
"""

QUIET_PANE = "· Cerebrating… (2m 45s · ↓ 12.0k tokens)\n"


@pytest.fixture
def client():
    return dash.app.test_client()


@contextmanager
def _fleet(*, panes: dict[str, str], status: dict[str, str]):
    """The live fleet, with tmux / `claude agents --json` / the runs DB all stubbed.

    ``panes`` is ``{window_id: pane text}`` — what a tmux capture of that window returns.
    ``status`` is ``{window_id: busy|idle|waiting}`` — what Claude says about it.
    """
    pids = {wid: 1000 + i for i, wid in enumerate(LIVE.values())}
    smap = {
        "by_pid": {pids[wid]: st for wid, st in status.items()},
        "cwd_by_pid": {},
    }
    with (
        patch("chela.discovery.get_all_windows", return_value=dict(LIVE)),
        patch("chela.dispatcher.list_runs", return_value=list(RUNS)),
        patch("chela.agent_manager.session_status_map", return_value=smap),
        patch("chela.agent_manager.claude_pid", side_effect=lambda wid: pids.get(wid)),
        patch("chela.agent_manager.window_type", return_value="claude"),
        patch("chela.scheduler.list_tasks", return_value=[]),
        patch("chela.transcripts.agent_transcript_summary",
              return_value={"recap": None, "recap_ts": None, "pr": None, "ai_title": None}),
        patch("chela.messenger.capture_pane", side_effect=lambda wid: panes.get(wid, "")) as cap,
    ):
        yield cap


def _by_wid(client) -> dict[str, dict]:
    rows = client.get("/api/agents").get_json()
    return {a["window_id"]: a for a in rows}


def test_the_run_row_says_who_is_dispatched_not_the_window_name(client):
    # @1 is a human's session; @9/@10 are the dispatcher's, because the RUNS rows say so.
    # Nothing here parses "cmx-76" out of a name — rename either one and this holds.
    with _fleet(panes={}, status={"@1": "idle", "@9": "busy", "@10": "busy"}):
        agents = _by_wid(client)
    assert agents["@1"]["dispatched"] is False
    assert agents["@9"]["dispatched"] is True
    assert agents["@10"]["dispatched"] is True


def test_a_permission_gate_is_needs_human_even_though_claude_says_busy(client):
    """🔴 The one that matters. Read `session_status` alone and this worker never pops out.

    @9 is stopped at a Bash approval — `busy` to `claude agents --json`, frozen in fact.
    The gate exists only as pixels, so only the pane probe can answer, and the wall's
    pop-out (and its amber dot) hangs entirely off this bit.
    """
    with _fleet(panes={"@9": BASH_GATE_PANE, "@10": QUIET_PANE},
                status={"@1": "idle", "@9": "busy", "@10": "busy"}):
        agents = _by_wid(client)
    assert agents["@9"]["session_status"] == "busy"   # …and yet:
    assert agents["@9"]["needs_human"] is True
    # The worker actually working is NOT dragged onto the wall.
    assert agents["@10"]["needs_human"] is False


def test_ai_title_surfaces_on_the_agent_row(client):
    """CMX-146: Claude Code's own auto-generated session title rides `/api/agents`
    alongside (not instead of) the recap — the title bar and sidebar both need it."""
    with (
        patch("chela.discovery.get_all_windows", return_value=dict(LIVE)),
        patch("chela.dispatcher.list_runs", return_value=list(RUNS)),
        patch("chela.agent_manager.session_status_map",
              return_value={"by_pid": {}, "cwd_by_pid": {}}),
        patch("chela.agent_manager.claude_pid", return_value=None),
        patch("chela.agent_manager.window_type", return_value="claude"),
        patch("chela.scheduler.list_tasks", return_value=[]),
        patch("chela.transcripts.agent_transcript_summary",
              return_value={"recap": "recap text", "recap_ts": None, "pr": None,
                             "ai_title": "Fix the flaky wall test"}),
        patch("chela.messenger.capture_pane", return_value=""),
    ):
        agents = _by_wid(client)
    assert agents["@9"]["ai_title"] == "Fix the flaky wall test"
    assert agents["@9"]["recap"] == "recap text"   # the two ride together, neither replaces the other


def test_claudes_own_waiting_needs_no_pane_capture(client):
    """`waiting` is free and already true — the probe must short-circuit on it.

    It is also the answer for every window the pane probe never runs on: a human's
    session is never hidden by the wall, so it is never probed, and `needs_human` must
    still be right for it (the sidebar's "Needs you" cluster and the tab badge read it).
    """
    with _fleet(panes={"@1": BASH_GATE_PANE, "@9": QUIET_PANE, "@10": QUIET_PANE},
                status={"@1": "waiting", "@9": "waiting", "@10": "idle"}) as cap:
        agents = _by_wid(client)
    assert agents["@1"]["needs_human"] is True     # from status alone…
    assert agents["@9"]["needs_human"] is True
    # …so neither of those cost a tmux capture. Only @10 — dispatched, not obviously
    # waiting — is worth the pixels.
    assert [c.args[0] for c in cap.call_args_list] == ["@10"]


def test_the_pane_probe_costs_one_capture_per_DISPATCHED_window_only(client):
    """A human's window is never hidden, so it is never probed. That is what bounds this.

    /api/agents is polled every 4s by every open tab; a capture per window per poll would
    make the fleet's cost scale with the fleet. It scales with the *dispatcher's
    concurrency cap* instead — a handful, and zero when nothing is dispatched.
    """
    with _fleet(panes={}, status={"@1": "busy", "@9": "busy", "@10": "idle"}) as cap:
        _by_wid(client)
    assert sorted(c.args[0] for c in cap.call_args_list) == ["@10", "@9"]


def test_a_tmux_hiccup_reports_not_blocked_rather_than_500ing_the_wall(client):
    # The probe is best-effort: an unreadable pane must cost the wall a pop-out, never
    # the whole agent list (which every view on the dashboard is built from).
    def boom(wid):
        raise RuntimeError("tmux is having a moment")

    with _fleet(panes={}, status={"@9": "busy"}):
        with patch("chela.messenger.capture_pane", side_effect=boom):
            agents = _by_wid(client)
    assert agents["@9"]["needs_human"] is False
    assert agents["@9"]["dispatched"] is True
