"""``chela restore`` — the report for every epoch-stamped row a hard tmux death orphaned
in the three stores CMX-82's inbox self-heal does not reach: inbox ``watches``, the
dispatcher's ``runs`` table (agent + judge window stamps), and ``session-ids.json``.

Pure scanner tests only — no live tmux, no sqlite, no filesystem. See ``chela/restore.py``
for why this is report-only (never touches a store, never relaunches/spawns/resumes).
"""
from __future__ import annotations

from chela.restore import Orphan, scan_all, scan_runs, scan_session_ids, scan_watches

OLD = "786-1784045825"        # the tmux server that was OOM-killed
NEW = "9001-1784099999"       # the one that came back, numbering from @0 again


# --------------------------------------------------------------------------
# inbox.json watches
# --------------------------------------------------------------------------

def test_scan_watches_flags_a_dangling_stamp():
    watches = {"@3": {"note": "reviewing cmx-41", "epoch": OLD}}
    orphans = scan_watches(watches, NEW)
    assert orphans == [Orphan("inbox.watches", "@3", "reviewing cmx-41", OLD)]


def test_scan_watches_ignores_a_current_stamp():
    watches = {"@3": {"note": "reviewing cmx-41", "epoch": NEW}}
    assert scan_watches(watches, NEW) == []


def test_scan_watches_falls_back_to_name_with_no_note():
    watches = {"@3": {"note": "", "name": "cmx-41", "epoch": OLD}}
    orphans = scan_watches(watches, NEW)
    assert orphans[0].label == "cmx-41"


def test_scan_watches_empty_store():
    assert scan_watches({}, NEW) == []
    assert scan_watches(None, NEW) == []


# --------------------------------------------------------------------------
# dispatcher runs table (agent + judge window stamps)
# --------------------------------------------------------------------------

def test_scan_runs_flags_a_dangling_agent_window():
    runs = [{"task_id": "cmx-77", "status": "running", "window_id": "@9",
             "window_epoch": OLD}]
    orphans = scan_runs(runs, NEW)
    assert orphans == [Orphan("dispatcher.runs", "@9", "cmx-77 (running)", OLD)]


def test_scan_runs_flags_a_dangling_judge_window_independently():
    # The agent's own window survived (current epoch); only its judge orphaned.
    runs = [{"task_id": "cmx-77", "status": "awaiting_review",
             "window_id": "@9", "window_epoch": NEW,
             "judge_window_id": "@10", "judge_window_epoch": OLD,
             "judge_state": "running"}]
    orphans = scan_runs(runs, NEW)
    assert orphans == [Orphan("dispatcher.runs (judge)", "@10", "cmx-77 judge (running)", OLD)]


def test_scan_runs_row_can_orphan_on_both_halves():
    runs = [{"task_id": "cmx-77", "status": "running", "window_id": "@9",
             "window_epoch": OLD, "judge_window_id": "@10",
             "judge_window_epoch": OLD, "judge_state": "running"}]
    orphans = scan_runs(runs, NEW)
    assert {o.wid for o in orphans} == {"@9", "@10"}


def test_scan_runs_ignores_current_and_unstamped_rows():
    runs = [
        {"task_id": "cmx-1", "status": "running", "window_id": "@1", "window_epoch": NEW},
        {"task_id": "cmx-2", "status": "running", "window_id": "@2", "window_epoch": None},
        {"task_id": "cmx-3", "status": "done", "window_id": None, "window_epoch": None},
    ]
    assert scan_runs(runs, NEW) == []


def test_scan_runs_empty():
    assert scan_runs([], NEW) == []
    assert scan_runs(None, NEW) == []


# --------------------------------------------------------------------------
# session-ids.json
# --------------------------------------------------------------------------

def test_scan_session_ids_flags_a_dangling_entry():
    entries = {"@5": {"session_id": "abc-123", "epoch": OLD}}
    orphans = scan_session_ids(entries, NEW)
    assert orphans == [Orphan("session-ids", "@5", "abc-123", OLD)]


def test_scan_session_ids_ignores_a_current_entry():
    entries = {"@5": {"session_id": "abc-123", "epoch": NEW}}
    assert scan_session_ids(entries, NEW) == []


def test_scan_session_ids_empty():
    assert scan_session_ids({}, NEW) == []
    assert scan_session_ids(None, NEW) == []


# --------------------------------------------------------------------------
# an unreadable current epoch accuses NOTHING (unknown, not stale)
# --------------------------------------------------------------------------

def test_unknown_now_epoch_never_flags_anything():
    """`chela restore` must report CANNOT VERIFY, never a false orphan, when tmux itself
    cannot be asked — an unstamped comparison is unknown, not proof of staleness
    (chela/epoch.py::is_dangling).
    """
    watches = {"@3": {"note": "x", "epoch": OLD}}
    runs = [{"task_id": "cmx-1", "status": "running", "window_id": "@9", "window_epoch": OLD}]
    entries = {"@5": {"session_id": "abc", "epoch": OLD}}
    assert scan_all(watches, runs, entries, None) == []


# --------------------------------------------------------------------------
# scan_all combines all three stores, in order
# --------------------------------------------------------------------------

def test_scan_all_combines_every_store():
    watches = {"@3": {"note": "watch", "epoch": OLD}}
    runs = [{"task_id": "cmx-1", "status": "running", "window_id": "@9", "window_epoch": OLD}]
    entries = {"@5": {"session_id": "abc", "epoch": OLD}}
    orphans = scan_all(watches, runs, entries, NEW)
    assert [o.store for o in orphans] == [
        "inbox.watches", "dispatcher.runs", "session-ids",
    ]


def test_scan_all_nothing_orphaned():
    assert scan_all({}, [], {}, NEW) == []
