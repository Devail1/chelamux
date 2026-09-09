"""Tests for ``chela.tasklists`` — the read-only ``~/.claude/tasks/<session-id>/N.json``
reader and its wid-safe join to a dispatcher run (issue #462).
"""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

import pytest

from chela import tasklists


def _write_task(root: Path, name: str, **fields) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(json.dumps(fields))


# --- read_tasks --------------------------------------------------------------

def test_read_tasks_returns_none_when_the_directory_is_absent(tmp_path):
    # ⛔ COUNTERWEIGHT (issue #462): most sessions never create this directory — a
    # missing directory must read as "nothing to show", never "0 tasks".
    assert tasklists.read_tasks("no-such-session", base=tmp_path) is None


def test_read_tasks_returns_an_empty_list_for_an_empty_directory(tmp_path):
    (tmp_path / "sid-1").mkdir()
    assert tasklists.read_tasks("sid-1", base=tmp_path) == []


def test_read_tasks_reads_every_valid_file_in_filename_order(tmp_path):
    root = tmp_path / "sid-1"
    _write_task(root, "2.json", id="2", subject="second", status="pending", blockedBy=[])
    _write_task(root, "10.json", id="10", subject="tenth", status="pending", blockedBy=[])
    _write_task(root, "1.json", id="1", subject="first", status="completed", blockedBy=[])

    tasks = tasklists.read_tasks("sid-1", base=tmp_path)

    # Sorted by FILENAME string, not numeric task id — "10.json" sorts before "2.json"
    # lexicographically, which is the honest behaviour for arbitrary Claude Code output;
    # this pins that ordering rather than silently assuming numeric filenames.
    assert [t["id"] for t in tasks] == ["1", "10", "2"]


def test_read_tasks_skips_a_malformed_file_but_keeps_the_rest(tmp_path):
    # A malformed/partially-written N.json is written live by Claude Code — a torn
    # read here is NORMAL, and must never blank the rest of the list (issue #462).
    root = tmp_path / "sid-1"
    _write_task(root, "1.json", id="1", subject="good one", status="completed", blockedBy=[])
    root.mkdir(parents=True, exist_ok=True)
    (root / "2.json").write_text("{not valid json")

    tasks = tasklists.read_tasks("sid-1", base=tmp_path)

    assert [t["id"] for t in tasks] == ["1"]


def test_read_tasks_skips_a_file_missing_required_fields(tmp_path):
    root = tmp_path / "sid-1"
    _write_task(root, "1.json", id="1", subject="good one", status="completed", blockedBy=[])
    _write_task(root, "2.json", subject="no id field", status="pending")
    _write_task(root, "3.json", id="3", status="pending")  # no subject

    tasks = tasklists.read_tasks("sid-1", base=tmp_path)

    assert [t["id"] for t in tasks] == ["1"]


def test_read_tasks_skips_a_file_whose_subject_is_present_but_empty(tmp_path):
    # 🔴 GUARD: `not subject` is a distinct disjunct from `not isinstance(subject, str)` —
    # a record with subject="" passes the isinstance check but must still be skipped. A
    # judge mutation dead-coded this exact clause (`or not subject` -> `or (False and not
    # subject)`) and the suite stayed green because no fixture ever supplied an empty-but-
    # present subject.
    root = tmp_path / "sid-1"
    _write_task(root, "1.json", id="1", subject="good one", status="completed", blockedBy=[])
    _write_task(root, "2.json", id="2", subject="", status="pending")

    tasks = tasklists.read_tasks("sid-1", base=tmp_path)

    assert [t["id"] for t in tasks] == ["1"]


def test_read_tasks_skips_a_file_whose_top_level_json_is_not_an_object(tmp_path):
    # 🔴 GUARD: a torn/malformed write can leave valid JSON that parses to something other
    # than an object (a bare list, string, or number) — the docstring's "a malformed N.json
    # is skipped, not fatal" covers this too, not just invalid JSON syntax. Without this
    # clause, obj.get() raises AttributeError, which the `except (OSError, ValueError)`
    # above does NOT catch, so one such file would 500 /api/dispatcher instead of being
    # skipped. A judge mutation dead-coded this exact clause (`if not isinstance(obj, dict)`
    # -> `if False and not isinstance(obj, dict)`) and the suite stayed green because no
    # fixture ever wrote a non-dict top level.
    root = tmp_path / "sid-1"
    _write_task(root, "1.json", id="1", subject="good one", status="completed", blockedBy=[])
    (root / "2.json").write_text(json.dumps([1, 2, 3]))

    tasks = tasklists.read_tasks("sid-1", base=tmp_path)

    assert [t["id"] for t in tasks] == ["1"]


def test_read_tasks_tolerates_a_missing_or_junk_blockedby(tmp_path):
    root = tmp_path / "sid-1"
    _write_task(root, "1.json", id="1", subject="no blockedBy key at all", status="pending")
    _write_task(root, "2.json", id="2", subject="junk blockedBy", status="pending", blockedBy="not-a-list")

    tasks = tasklists.read_tasks("sid-1", base=tmp_path)

    assert tasks[0]["blocked_by"] == []
    assert tasks[1]["blocked_by"] == []


# --- TASKS_DIR — the module's own default, exercised for real -------------------

def test_tasks_dir_defaults_to_claude_config_dir_tasks_without_any_override(monkeypatch, tmp_path):
    # 🔴 GUARD (docs/defeat_shapes 352c): every OTHER test in this file passes base=
    # explicitly, and test_tasklists_dispatcher_api.py monkeypatches TASKS_DIR itself —
    # nothing ever proves the module's own default constant resolves under the real
    # config dir. A judge mutation repointed it (`claude_config_dir() / "tasks"` ->
    # `claude_config_dir() / "tasks-never-here"`) and the suite stayed green because no
    # fixture ever exercised the un-overridden default. Reload the module against a
    # fake CLAUDE_CONFIG_DIR and call read_tasks with NO base= at all — this can only
    # pass if the real TASKS_DIR constant still resolves to <config dir>/tasks.
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    importlib.reload(tasklists)
    try:
        _write_task(tmp_path / "tasks" / "sid-real", "1.json",
                    id="1", subject="found at the real default", status="pending", blockedBy=[])

        tasks = tasklists.read_tasks("sid-real")  # deliberately no base=

        assert tasks == [{
            "id": "1", "subject": "found at the real default",
            "status": "pending", "blocked_by": [],
        }]
    finally:
        importlib.reload(tasklists)


# --- summarize -----------------------------------------------------------------

def test_summarize_is_none_for_no_directory_and_for_an_empty_one_alike():
    # The counterweight guard again, one level up: a run must read identically
    # whether it never had a task directory (None) or has an empty one ([]).
    assert tasklists.summarize(None) is None
    assert tasklists.summarize([]) is None


def test_summarize_counts_done_and_names_the_in_progress_task():
    tasks = [
        {"id": "1", "subject": "first", "status": "completed", "blocked_by": []},
        {"id": "2", "subject": "second", "status": "completed", "blocked_by": []},
        {"id": "3", "subject": "wire the in-flight refusal", "status": "in_progress", "blocked_by": []},
        {"id": "4", "subject": "fourth", "status": "pending", "blocked_by": []},
    ]
    summary = tasklists.summarize(tasks)
    assert summary["total"] == 4
    assert summary["done"] == 2
    assert summary["in_progress"] == {"id": "3", "subject": "wire the in-flight refusal"}
    assert summary["blocked"] == []


def test_summarize_surfaces_blocked_by_relationships():
    tasks = [
        {"id": "5", "subject": "fifth", "status": "pending", "blocked_by": []},
        {"id": "6", "subject": "sixth", "status": "pending", "blocked_by": ["5"]},
    ]
    summary = tasklists.summarize(tasks)
    assert summary["blocked"] == [{"id": "6", "subject": "sixth", "blocked_by": ["5"]}]


def test_summarize_in_progress_is_none_when_nothing_is_in_progress():
    tasks = [{"id": "1", "subject": "first", "status": "completed", "blocked_by": []}]
    assert tasklists.summarize(tasks)["in_progress"] is None


# --- resolve_session_id — the wid-reuse guard -----------------------------------

def test_resolve_session_id_happy_path():
    entries = {"@5": {"session_id": "sid-live", "epoch": "111-222"}}
    assert tasklists.resolve_session_id("@5", "111-222", entries, "111-222") == "sid-live"


@pytest.mark.parametrize("window_id,window_epoch,current_epoch", [
    (None, "111-222", "111-222"),
    ("@5", None, "111-222"),
    ("@5", "111-222", None),
])
def test_resolve_session_id_none_when_any_half_is_missing(window_id, window_epoch, current_epoch):
    entries = {"@5": {"session_id": "sid-live", "epoch": "111-222"}}
    assert tasklists.resolve_session_id(window_id, window_epoch, entries, current_epoch) is None


def test_resolve_session_id_refuses_a_reused_window_id_pointing_at_a_different_session():
    # 🔴 GUARD (issue #462's core anti-misattribution case): tmux only reissues a
    # small @N after a server restart — i.e. a new epoch. Run A was dispatched at
    # epoch "111-222" onto @5; the server then restarted (epoch "999-888") and @5
    # was handed to an unrelated agent, whose spawn OVERWROTE the session-ids.json
    # entry for @5 with the new epoch. A naive wid-keyed lookup would now return
    # the WRONG (new) session for run A. Comparing run A's own recorded
    # window_epoch against the current epoch is what must refuse this join.
    entries = {"@5": {"session_id": "sid-of-the-NEW-unrelated-agent", "epoch": "999-888"}}
    resolved = tasklists.resolve_session_id(
        "@5", "111-222",  # run A's window_id + the epoch it was dispatched under
        entries, "999-888",  # current epoch — the server restarted
    )
    assert resolved is None


def test_resolve_session_id_refuses_when_the_entries_row_itself_is_stale():
    # Defensive counterpart: even if a run's own window_epoch happened to match
    # current (shouldn't happen in practice), a session-ids.json row that is
    # itself stamped with a different epoch must not be trusted either.
    entries = {"@5": {"session_id": "sid-stale", "epoch": "111-222"}}
    assert tasklists.resolve_session_id("@5", "999-888", entries, "999-888") is None


def test_resolve_session_id_none_when_the_wid_has_no_entry_at_all():
    assert tasklists.resolve_session_id("@9", "111-222", {}, "111-222") is None


# --- progress_for_run — end to end ----------------------------------------------

def test_progress_for_run_end_to_end(tmp_path):
    root = tmp_path / "sid-live"
    _write_task(root, "1.json", id="1", subject="first", status="completed", blockedBy=[])
    _write_task(root, "2.json", id="2", subject="second", status="in_progress", blockedBy=[])
    run = {"window_id": "@5", "window_epoch": "111-222"}
    entries = {"@5": {"session_id": "sid-live", "epoch": "111-222"}}

    progress = tasklists.progress_for_run(run, entries, "111-222", base=tmp_path)

    assert progress == {
        "total": 2, "done": 1,
        "in_progress": {"id": "2", "subject": "second"},
        "blocked": [],
    }


def test_progress_for_run_none_when_the_run_has_no_window_yet(tmp_path):
    # A just-claimed run with no window_id recorded: no join attempted at all.
    run = {"window_id": None, "window_epoch": None}
    assert tasklists.progress_for_run(run, {}, "111-222", base=tmp_path) is None


def test_progress_for_run_none_when_the_session_never_wrote_a_task_directory(tmp_path):
    run = {"window_id": "@5", "window_epoch": "111-222"}
    entries = {"@5": {"session_id": "sid-quiet", "epoch": "111-222"}}
    assert tasklists.progress_for_run(run, entries, "111-222", base=tmp_path) is None


# --- read-only guard -------------------------------------------------------------

def test_never_writes_renames_or_deletes_under_the_tasks_root(tmp_path, monkeypatch):
    """Assert read-only (issue #462): stub every mutating filesystem entry point so
    the test fails loudly if the reader ever calls one, then drive it against a real
    fixture and confirm it still returns the right data untouched."""
    root = tmp_path / "sid-1"
    _write_task(root, "1.json", id="1", subject="first", status="completed", blockedBy=[])

    def _boom(*a, **k):
        raise AssertionError("chela.tasklists touched a mutating filesystem call")

    monkeypatch.setattr(os, "remove", _boom)
    monkeypatch.setattr(os, "rename", _boom)
    monkeypatch.setattr(os, "replace", _boom)
    monkeypatch.setattr(os, "unlink", _boom)
    monkeypatch.setattr(Path, "unlink", _boom)
    monkeypatch.setattr(Path, "write_text", _boom)
    monkeypatch.setattr(Path, "write_bytes", _boom)
    monkeypatch.setattr(Path, "rename", _boom)
    monkeypatch.setattr(Path, "replace", _boom)
    monkeypatch.setattr(Path, "mkdir", _boom)
    monkeypatch.setattr(Path, "rmdir", _boom)

    tasks = tasklists.read_tasks("sid-1", base=tmp_path)
    assert tasks == [{"id": "1", "subject": "first", "status": "completed", "blocked_by": []}]

    run = {"window_id": "@5", "window_epoch": "111-222"}
    entries = {"@5": {"session_id": "sid-1", "epoch": "111-222"}}
    progress = tasklists.progress_for_run(run, entries, "111-222", base=tmp_path)
    assert progress["total"] == 1
