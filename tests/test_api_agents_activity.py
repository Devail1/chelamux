"""``/api/agents`` — each window's ``last_activity`` and ``created`` (CMX-66).

The sidebar's VIEW menu filters by Last activity, groups by Date and sorts by Last
activity / Created. Those need two timestamps per window, read where they are free: the
transcript's last write (one ``stat`` alongside the summary already read) and the window's
Claude process start (``Pane.started``, already in the request's shared pane snapshot).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import patch

from chela import probecache, sessions, transcripts
from chela.dashboard import app as dash


def test_summary_for_path_carries_the_transcripts_last_write(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text('{"type": "user"}\n')
    when = 1_790_000_000
    os.utime(f, (when, when))
    got = transcripts.summary_for_path(f)["last_activity"]
    assert datetime.fromisoformat(got) == datetime.fromtimestamp(when, tz=timezone.utc)
    assert transcripts.summary_for_path(None)["last_activity"] is None
    assert transcripts.summary_for_path(tmp_path / "missing.jsonl")["last_activity"] is None


def test_window_started_reads_the_shared_pane_snapshot_without_a_probe_of_its_own():
    panes = {"@3": sessions.Pane(wid="@3", claude_pid=77, started=1_790_000_000.0)}
    snap = datetime.fromtimestamp(1_790_000_000, tz=timezone.utc)
    reads = []
    with (
        patch("chela.sessions.panes", return_value=panes),
        patch("chela.sessions.proc_started", side_effect=lambda pid: reads.append(pid) or 1_700_000_000.0),
    ):
        with probecache.batch() as b:
            b.pane("@3")                                    # the endpoint's claude_pid takes it
            assert datetime.fromisoformat(dash._window_started("@3", 77)) == snap
            assert reads == [], "a pane already in the snapshot must not be re-read"
            assert dash._window_started("@3", None) is None  # no Claude → no start time
            # a pane naming another process: the pid's own start, not the snapshot's
            assert datetime.fromisoformat(dash._window_started("@3", 88)).timestamp() == 1_700_000_000
        # outside a batch: the same pid's start, so both paths answer the same
        assert datetime.fromisoformat(dash._window_started("@3", 77)).timestamp() == 1_700_000_000
    assert reads == [88, 77]


def test_api_agents_ships_last_activity_and_created():
    started = 1_790_000_000.0
    panes = {"@3": sessions.Pane(wid="@3", started=started, claude_pid=1234, direct_claude_pid=1234)}
    summary = {"recap": None, "recap_ts": None, "pr": None, "ai_title": None,
               "last_activity": "2026-10-09T08:00:00+00:00"}
    with (
        patch("chela.discovery.get_all_windows", return_value={"proj": "@3"}),
        patch("chela.dispatcher.list_runs", return_value=[]),
        patch("chela.sessions.panes", return_value=panes),
        patch("chela.agent_manager.session_status_map", return_value={"by_pid": {}, "cwd_by_pid": {}}),
        # the REAL claude_pid: inside the batch it takes the pane snapshot `created` reads
        patch("chela.agent_manager.session_entry", return_value=None),
        patch("chela.agent_manager.window_type", return_value="claude"),
        patch("chela.agent_manager.is_manual_name", return_value=False),
        patch("chela.scheduler.list_tasks", return_value=[]),
        patch("chela.transcripts.agent_transcript_summary", return_value=summary),
        patch("chela.messenger.capture_pane", return_value=""),
        patch("chela.inbox.is_done", return_value=False),
        patch("chela.epoch.is_dangling", return_value=False),
    ):
        rows = dash.app.test_client().get("/api/agents").get_json()
    (row,) = rows
    assert row["last_activity"] == "2026-10-09T08:00:00+00:00"
    assert datetime.fromisoformat(row["created"]) == datetime.fromtimestamp(started, tz=timezone.utc)
