"""``/api/agents`` — the `done` field (issue #475).

`inbox.is_done` is the transcript-level evidence ("has the agent said anything since
you last spoke"); this is the *wiring* around it — the gate that keeps it scoped to
what issue #475 actually asked for:

* REGULAR sessions only. A dispatched worker kills its own window on every terminal
  transition (dispatcher._kill_window), so its row is gone before a badge could show
  — its outcome belongs on the Dispatch board, never here. Corrupt the gate by
  dropping the ``not is_dispatched`` term and a dispatched worker's row would badge
  `done` in the instant before its window disappears.
* Only once every OTHER reason to show attention has been ruled out — busy or
  already `needs_human` — so `done` never fights the dot colour it is meant to
  refine (a row cannot be both "waiting" and "done").
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

import pytest

from chela.dashboard import app as dash

LIVE = {"orchestrator": "@1", "cmx-76": "@9"}

RUNS = [{"status": "running", "window_id": "@9", "window_name": "cmx-76"}]


@pytest.fixture
def client():
    return dash.app.test_client()


@contextmanager
def _fleet(*, status: dict[str, str], runs=(), is_done=True):
    """``status`` is ``{window_id: busy|idle|waiting}``; ``runs`` makes a window
    dispatcher-owned (empty means every window is a REGULAR, hand-launched one)."""
    pids = {wid: 1000 + i for i, wid in enumerate(LIVE.values())}
    smap = {
        "by_pid": {pids[wid]: st for wid, st in status.items()},
        "cwd_by_pid": {},
    }
    with (
        patch("chela.discovery.get_all_windows", return_value=dict(LIVE)),
        patch("chela.dispatcher.list_runs", return_value=list(runs)),
        patch("chela.agent_manager.session_status_map", return_value=smap),
        patch("chela.agent_manager.claude_pid", side_effect=lambda wid: pids.get(wid)),
        patch("chela.agent_manager.window_type", return_value="claude"),
        patch("chela.scheduler.list_tasks", return_value=[]),
        patch("chela.transcripts.agent_transcript_summary",
              return_value={"recap": None, "recap_ts": None, "pr": None, "ai_title": None}),
        patch("chela.messenger.capture_pane", return_value=""),
        # The transcript-level evidence itself is exercised in tests/test_inbox.py
        # (test_is_done_*) — here it is stubbed True so the WIRING around it (which
        # windows ever get to ask it) is what's under test.
        patch("chela.inbox.is_done", return_value=is_done),
    ):
        yield


def _by_wid(client) -> dict[str, dict]:
    rows = client.get("/api/agents").get_json()
    return {a["window_id"]: a for a in rows}


def test_an_idle_regular_session_with_evidence_reads_done(client):
    with _fleet(status={"@1": "idle", "@9": "idle"}):
        agents = _by_wid(client)
    assert agents["@9"]["done"] is True


def test_a_dispatched_window_never_reads_done_even_with_evidence(client):
    """⛔ Scope guard: dispatch outcomes belong on the Dispatch board, not the sidebar."""
    with _fleet(status={"@1": "idle", "@9": "idle"}, runs=RUNS):
        agents = _by_wid(client)
    assert agents["@9"]["dispatched"] is True
    assert agents["@9"]["done"] is False


def test_a_waiting_session_never_reads_done_even_with_evidence(client):
    """`done` must never fight `needs_human` — a row is either waiting or done, not both."""
    with _fleet(status={"@1": "idle", "@9": "waiting"}):
        agents = _by_wid(client)
    assert agents["@9"]["needs_human"] is True
    assert agents["@9"]["done"] is False


def test_a_busy_session_never_reads_done_even_with_evidence(client):
    with _fleet(status={"@1": "idle", "@9": "busy"}):
        agents = _by_wid(client)
    assert agents["@9"]["session_status"] == "busy"
    assert agents["@9"]["done"] is False


def test_no_evidence_means_no_done_even_though_otherwise_eligible(client):
    with _fleet(status={"@1": "idle", "@9": "idle"}, is_done=False):
        agents = _by_wid(client)
    assert agents["@9"]["done"] is False
