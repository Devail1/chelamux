"""⚖️ CMX-40: a DETACHED judge battery's live progress on the window that launched it.

Measured on CMX-32, 2026-10-08: the judge agent launched `chela judge run --detach` and went
idle, as designed — and its window read idle for the 15 minutes the battery ran, because
Claude Code only lists background tasks it started itself. These pin:

* a live battery (alive pid + status json at 3/6) ⇒ the model, `/api/agents` and `chela peek`
  all say ``3/6`` and the elapsed time;
* a finished battery (its status json removed by the run itself) ⇒ no badge;
* a dead pid whose status json outlived it ⇒ "died — no verdict", never idle;
* a torn status read is retried, never raised;
* a REAL `judge_run` writes the tally, the current label and the window the badge belongs on.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from chela import agent_manager, dispatcher, judge, orchestrator, sessions
from chela.dashboard import app as dash
from tests.test_judge import REAL_GUARD_TEST, _exp, _run_row, _workflow_repo

WINDOW = "judge-liavacc/cmx-32-wall-ring"


@pytest.fixture(autouse=True)
def _logs(tmp_path, monkeypatch):
    logs = tmp_path / "judge-logs"
    monkeypatch.setattr(judge, "judge_logs_dir", lambda: logs)
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    return logs


def _write_status(logs: Path, *, task_id="T32", pid=None, window=WINDOW, elapsed=15 * 60,
                  done=3, total=6, **extra) -> dict:
    """A status json exactly as `judge_run` writes it, owned by ``pid`` (this process by
    default — alive, with a matching /proc start time)."""
    ident = judge.owner_identity(os.getpid()) if pid is None else {"pid": pid, "started": 1.0}
    status = {**ident, "task_id": task_id, "run_started_at": time.time() - elapsed,
              "detached": True, "done": done, "total": total, "phase": "battery",
              "window": window, "killed": 2, "survived": 1, "invalid": 0,
              "current": "chela/wall.py: the ring is drawn", "log": "/x/T32.log", **extra}
    logs.mkdir(parents=True, exist_ok=True)
    (logs / f"{task_id}.json").write_text(json.dumps(status))
    return status


def _dead_pid() -> int:
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


# --- the model --------------------------------------------------------------------------

def test_a_live_battery_reads_testing_with_its_count_and_elapsed(_logs):
    _write_status(_logs)
    b = judge.battery_for_window(WINDOW)
    assert b["state"] == judge.BATTERY_TESTING
    assert (b["done"], b["total"], b["killed"], b["survived"]) == (3, 6, 2, 1)
    assert b["label"] == "⚖️ testing · 3/6 · 15m"


def test_a_finished_battery_shows_no_badge(_logs):
    """The run removes its own status json in its `finally`, after the verdict."""
    assert judge.battery_for_window(WINDOW) is None
    _logs.mkdir(parents=True)
    assert judge.battery_for_window(WINDOW) is None


def test_a_dead_pid_with_no_verdict_reads_died_not_idle(_logs):
    """GUARD: a status json whose owner is gone is a run that never reached a verdict."""
    _write_status(_logs, pid=_dead_pid())
    b = judge.battery_for_window(WINDOW)
    assert b["state"] == judge.BATTERY_DIED
    assert b["label"] == "⚖️ judge run died — no verdict"


def test_only_the_window_that_launched_the_battery_gets_it(_logs):
    _write_status(_logs)
    assert judge.battery_for_window("judge-some-other-branch") is None
    assert judge.battery_for_window("cmx-32") is None


def test_the_newest_run_for_a_window_wins_over_a_stale_dead_one(_logs):
    _write_status(_logs, task_id="OLD", pid=_dead_pid(), elapsed=3600)
    _write_status(_logs, task_id="NEW", elapsed=60, done=1)
    b = judge.battery_for_window(WINDOW)
    assert (b["state"], b["task_id"], b["label"]) == (
        judge.BATTERY_TESTING, "NEW", "⚖️ testing · 1/6 · 1m")


def test_a_torn_status_read_is_retried_not_raised(_logs, monkeypatch):
    _write_status(_logs)
    real = judge._read_judge_lock
    calls = {"n": 0}

    def flaky(path):
        calls["n"] += 1
        return None if calls["n"] == 1 else real(path)

    monkeypatch.setattr(judge, "_read_judge_lock", flaky)
    b = judge.battery_for_window(WINDOW)
    assert b is not None and b["label"] == "⚖️ testing · 3/6 · 15m"
    assert calls["n"] >= 2


def test_a_permanently_torn_status_file_is_no_badge_not_an_error(_logs):
    _logs.mkdir(parents=True)
    (_logs / "T32.json").write_text('{"pid": 12, "window": "judge-')
    assert judge.battery_for_window(WINDOW) is None


def test_the_label_before_the_battery_knows_its_total_names_the_phase():
    b = {"state": judge.BATTERY_TESTING, "done": 0, "total": None, "phase": "setup",
         "elapsed": 75}
    assert judge.battery_label(b) == "⚖️ testing · setup · 1m"


# --- chela peek -------------------------------------------------------------------------

def _peek(monkeypatch, name=WINDOW):
    monkeypatch.setattr(orchestrator.discovery, "get_windows_by_id", lambda: {"@40": name})
    monkeypatch.setattr(orchestrator.discovery, "get_window_cwd_by_id", lambda wid: "/w")
    monkeypatch.setattr(sessions, "transcript_for_window", lambda wid, base=None: None)
    monkeypatch.setattr(agent_manager, "session_status_map",
                        lambda force=False: {"by_pid": {7: "idle"}, "cwd_by_pid": {}})
    monkeypatch.setattr(agent_manager, "claude_pid", lambda wid: 7)
    monkeypatch.setattr(agent_manager, "window_type", lambda wid, running=None: "claude")
    p = orchestrator.peek("@40")
    return p, [ln for ln in orchestrator.format_peek(p).splitlines()
               if ln.strip().startswith("judge:")]


def test_peek_on_a_judge_window_prints_the_batterys_progress(_logs, monkeypatch):
    _write_status(_logs)
    p, lines = _peek(monkeypatch)
    assert p["judge_battery"]["label"] == "⚖️ testing · 3/6 · 15m"
    assert lines == ["  judge:   ⚖️ testing · 3/6 · 15m (KILLED 2 · SURVIVED 1) — "
                     "chela/wall.py: the ring is drawn"]


def test_peek_on_a_judge_window_whose_run_died_says_so(_logs, monkeypatch):
    _write_status(_logs, pid=_dead_pid())
    _, lines = _peek(monkeypatch)
    assert lines == ["  judge:   ⚖️ judge run died — no verdict — see /x/T32.log"]


def test_peek_with_no_battery_prints_no_judge_line(_logs, monkeypatch):
    p, lines = _peek(monkeypatch)
    assert p["judge_battery"] is None and lines == []


# --- /api/agents (the pane pill + the sidebar row read this) ------------------------------

@contextmanager
def _fleet():
    live = {WINDOW: "@40", "plain": "@41"}
    pids = {"@40": 1040, "@41": 1041}
    with (
        patch("chela.discovery.get_all_windows", return_value=dict(live)),
        patch("chela.dispatcher.list_runs", return_value=[]),
        patch("chela.agent_manager.session_status_map",
              return_value={"by_pid": {1040: "idle", 1041: "idle"}, "cwd_by_pid": {}}),
        patch("chela.agent_manager.claude_pid", side_effect=lambda wid: pids.get(wid)),
        patch("chela.agent_manager.window_type", return_value="claude"),
        patch("chela.scheduler.list_tasks", return_value=[]),
        patch("chela.transcripts.agent_transcript_summary",
              return_value={"recap": None, "recap_ts": None, "pr": None, "ai_title": None}),
        patch("chela.messenger.capture_pane", return_value=""),
        patch("chela.inbox.is_done", return_value=False),
    ):
        yield


def test_api_agents_carries_the_battery_on_the_judge_window_only(_logs):
    _write_status(_logs)
    with _fleet():
        rows = {a["window_id"]: a for a in dash.app.test_client().get("/api/agents").get_json()}
    assert rows["@40"]["session_status"] == "idle"          # what the pane USED to say
    assert rows["@40"]["judge_battery"]["label"] == "⚖️ testing · 3/6 · 15m"
    assert rows["@41"]["judge_battery"] is None


def test_the_work_card_gets_the_battery_of_a_judging_run(_logs):
    _write_status(_logs)
    runs = [{"task_id": "T32", "branch_name": "liavacc/cmx-32-wall-ring",
             "judge_state": judge.J_RUNNING},
            {"task_id": "T33", "branch_name": "liavacc/cmx-32-wall-ring", "judge_state": "clean"}]
    dash._attach_judge_battery(runs)
    assert runs[0]["judge_battery"]["label"] == "⚖️ testing · 3/6 · 15m"
    assert runs[1]["judge_battery"] is None


# --- the producer: a REAL judge_run writes what the badge reads -------------------------

def test_judge_run_writes_the_tally_the_label_and_the_window(tmp_path, _logs, monkeypatch):
    """GUARD (wiring): each experiment's verdict lands in the status json as it happens, the
    running experiment is named, and the json names the judge window it belongs to — then
    the run removes it, so a finished run shows no badge."""
    monkeypatch.setattr(dispatcher, "_kill_windows_named", lambda name: None)
    task_id = "abc123"
    repo = _workflow_repo(tmp_path, task_id, REAL_GUARD_TEST)
    with dispatcher._db() as conn:
        _run_row(conn, repo, task_id)
    branch = dispatcher.resolve_run(task_id)["branch_name"]
    exp_file = tmp_path / "experiments.json"
    exp_file.write_text(json.dumps({"experiments": [_exp(), _exp(guard="again")]}))
    seen: list[dict] = []
    real_apply = judge._apply_experiments

    def _apply(*a, progress=None, **kw):
        if progress is None:
            return real_apply(*a, **kw)

        def _spy(done, total):
            progress(done, total)
            seen.append(json.loads((_logs / f"{task_id}.json").read_text()))
        return real_apply(*a, progress=_spy, **kw)

    monkeypatch.setattr(judge, "_apply_experiments", _apply)
    window = judge.judge_window_name(branch)
    with patch.object(dispatcher, "_post_pr_comment", return_value=(True, "")):
        judge.judge_run(task_id, exp_file, cleanup=True, detached=True)

    assert [s["killed"] for s in seen] == [0, 1, 2], seen
    assert all(s["survived"] == 0 for s in seen), seen
    # `progress(n)` fires as experiment n starts, before it is named — so each snapshot
    # names the experiment that just finished, and the last one names the last experiment.
    assert [s["current"] for s in seen] == [
        None, "guard.py: the colourblind glyph cue", "guard.py: again"], seen
    assert seen[-1]["window"] == window
    assert judge.battery_for_window(window) is None            # finished ⇒ no badge


def test_api_dispatcher_puts_the_battery_on_the_judging_awaiting_review_card(
    _logs, monkeypatch,
):
    """GUARD (wiring): the Work board's `/api/dispatcher` attaches it — end to end."""
    from tests.test_tasklists_dispatcher_api import _run, _setup

    _write_status(_logs)
    runs = [_run(status="awaiting_review", branch_name="liavacc/cmx-32-wall-ring",
                 judge_state=judge.J_RUNNING, pr_url="https://example.invalid/pr/1")]
    _setup(monkeypatch, _logs.parent, runs, entries={}, current_epoch="1-2")
    data = dash.app.test_client().get("/api/dispatcher").get_json()
    [card] = data["workflows"][0]["awaiting_review_runs"]
    assert card["judge_battery"]["label"] == "⚖️ testing · 3/6 · 15m"
