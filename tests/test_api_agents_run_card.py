"""``/api/agents`` — each dispatched window's ``run`` card and ``cwd_is_home`` (CMX-35).

The sidebar folds a run's agent window and its judge window into ONE row
(``CMX-37 · <title>``). It can only do that if the server says which run each window
belongs to, and whether it is that run's agent or its judge — read off the runs table
(the row records both window ids), never guessed from a window name.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from unittest.mock import patch

from chela.dashboard import app as dash

LIVE = {
    "orchestrator": "@1",
    "liavacc/cmx-37-theme": "@7",
    "judge-liavacc/cmx-37-theme": "@8",
    "liavacc/cmx-41-new": "@9",
    "shell-2": "@4",
}

RUNS = [
    # newest first, as list_runs returns them
    {"task_id": "CMX-41", "title": "Freshly claimed", "status": "claimed",
     "window_id": None, "window_name": "liavacc/cmx-41-new"},
    {"task_id": "CMX-37", "title": "Theme hover text", "status": "awaiting_review",
     "judge_state": "running", "window_id": "@7", "window_name": "liavacc/cmx-37-theme",
     "judge_window_id": "@8", "branch_name": "liavacc/cmx-37-theme", "pr_url": "https://x/pr/1"},
    # an OLDER run that recorded the same @7 (a recycled id) must not win
    {"task_id": "CMX-2", "title": "Old", "status": "done", "window_id": "@7",
     "window_name": "liavacc/cmx-37-theme"},
]


@contextmanager
def _fleet(runs, cwd_by_wid=None):
    cwd_by_wid = cwd_by_wid or {}
    pids = {wid: 1000 + i for i, wid in enumerate(LIVE.values())}
    entries = {pid: {"status": "idle", "cwd": cwd_by_wid.get(wid), "name": None, "moved": False}
               for wid, pid in pids.items()}
    with (
        patch("chela.discovery.get_all_windows", return_value=dict(LIVE)),
        patch("chela.dispatcher.list_runs", return_value=[dict(r) for r in runs]),
        patch("chela.agent_manager.session_status_map", return_value={"by_pid": {}, "cwd_by_pid": {}}),
        patch("chela.agent_manager.claude_pid", side_effect=lambda wid: pids.get(wid)),
        patch("chela.agent_manager.session_entry", side_effect=lambda pid, _m: entries.get(pid)),
        patch("chela.agent_manager.window_type", return_value="claude"),
        patch("chela.agent_manager.is_manual_name", return_value=False),
        patch("chela.scheduler.list_tasks", return_value=[]),
        patch("chela.transcripts.agent_transcript_summary",
              return_value={"recap": None, "recap_ts": None, "pr": None, "ai_title": None}),
        patch("chela.messenger.capture_pane", return_value=""),
        patch("chela.inbox.is_done", return_value=False),
        patch("chela.epoch.is_dangling", return_value=False),
    ):
        yield


def _by_wid(runs=RUNS, **kw) -> dict[str, dict]:
    with _fleet(runs, **kw):
        rows = dash.app.test_client().get("/api/agents").get_json()
    return {a["window_id"]: a for a in rows}


def test_agent_and_judge_windows_carry_the_same_run_with_their_role():
    agents = _by_wid()
    agent, judge = agents["@7"]["run"], agents["@8"]["run"]
    assert agent == {"task_id": "CMX-37", "title": "Theme hover text", "status": "awaiting_review",
                     "judge_state": "running", "role": "agent", "pr_url": "https://x/pr/1"}
    assert judge == {**agent, "role": "judge"}


def test_a_window_the_dispatcher_does_not_own_has_no_run():
    agents = _by_wid()
    assert agents["@1"]["run"] is None
    assert agents["@4"]["run"] is None
    assert agents["@4"]["dispatched"] is False


def test_a_claimed_run_with_no_window_id_yet_is_matched_by_its_recorded_name():
    """The spawn gap (CMX-308): the row is claimed before tmux stamps its @id back."""
    assert _by_wid()["@9"]["run"]["task_id"] == "CMX-41"


def test_no_runs_means_no_run_cards():
    agents = _by_wid(runs=[])
    assert all(a["run"] is None for a in agents.values())


def test_cwd_is_home_is_true_only_for_the_home_dir_itself():
    home = os.path.expanduser("~")
    agents = _by_wid(cwd_by_wid={"@1": home, "@4": os.path.join(home, "projects", "x")})
    assert agents["@1"]["cwd_is_home"] is True
    assert agents["@4"]["cwd_is_home"] is False
    assert agents["@7"]["cwd_is_home"] is False   # no cwd at all
