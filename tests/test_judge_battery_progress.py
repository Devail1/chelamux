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
    assert lines == ["  judge:   ⚖️ testing · 3/6 · 15m (KILLED 2 · SURVIVED 1 · INVALID 0) — "
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
def _fleet(window=WINDOW):
    live = {window: "@40", "plain": "@41"}
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


# --- the producer: a REAL judge_run writes what the badge reads -------------------------

def test_judge_run_writes_the_tally_the_label_and_the_window(tmp_path, _logs, monkeypatch):
    """GUARD (wiring): every status write the run makes is captured, over a battery with one
    of EACH verdict (KILLED, SURVIVED, INVALID — both a path-escape and a MALFORMED one) plus a held-out experiment, and with the
    consistency re-run ON (the default sample re-runs the survivor and a killed one). Pins:

    * the tally moves one verdict at a time, each to its OWN key, and ends at exactly 2/1/2, summing to the 5 processed —
      the consistency re-run re-adjudicates experiments already tallied and must not count
      them again (it demonstrably ran: some writes carry ``phase == "consistency"``);
    * the running experiment is named, except a held-out one, whose guard text never reaches
      ANY write (CMX-395) — the badge names it only as "a held-out experiment";
    * the json names the judge window it belongs to, and the run removes it when finished.
    """
    monkeypatch.setattr(dispatcher, "_kill_windows_named", lambda name: None)
    task_id = "abc123"
    repo = _workflow_repo(tmp_path, task_id, REAL_GUARD_TEST)
    with dispatcher._db() as conn:
        _run_row(conn, repo, task_id)
    branch = dispatcher.resolve_run(task_id)["branch_name"]
    secret = "SECRET-held-out-guard-text"
    exp_file = tmp_path / "experiments.json"
    exp_file.write_text(json.dumps({"experiments": [
        _exp(),                                                         # KILLED
        _exp(guard="the off hue", before='else "grey"', after='else "gray"'),   # SURVIVED
        _exp(guard="outside", file="../outside.py"),                    # INVALID (path)
        {"guard": "malformed", "file": "guard.py"},                     # INVALID (no diff)
        _exp(guard=secret, held_out=True),                              # KILLED, held out
    ]}))
    writes: list[dict] = []
    real_write = judge._write_run_status

    def _spy(tid, status):
        if tid == task_id:
            writes.append(json.loads(json.dumps(status)))
        real_write(tid, status)

    monkeypatch.setattr(judge, "_write_run_status", _spy)
    window = judge.judge_window_name(branch)
    with patch.object(dispatcher, "_post_pr_comment", return_value=(True, "")):
        result = judge.judge_run(task_id, exp_file, cleanup=True, detached=True)

    assert result["ok"], result
    assert result["consistency"].get("sampled") == 2, result     # the re-run really ran
    tallies = [(w["killed"], w["survived"], w["invalid"]) for w in writes]
    progression = [t for i, t in enumerate(tallies) if i == 0 or t != tallies[i - 1]]
    assert progression == [(0, 0, 0), (1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 1, 2),
                           (2, 1, 2)], tallies
    rerun = [w for w in writes if w["phase"] == "consistency"]
    assert rerun, writes
    assert {(w["killed"], w["survived"], w["invalid"]) for w in rerun} == {(2, 1, 2)}, rerun
    # Every experiment processed is counted exactly once — a malformed one's INVALID too.
    last = writes[-1]
    assert last["total"] == 5 and sum(tallies[-1]) == last["total"], writes[-1]

    currents = [w["current"] for w in writes]
    named = [c for i, c in enumerate(currents) if c and c not in currents[:i]]
    assert named == ["guard.py: the colourblind glyph cue", "guard.py: the off hue",
                     "../outside.py: outside", "guard.py: malformed",
                     "a held-out experiment"], currents
    assert not [w for w in writes if secret in json.dumps(w)], "held-out guard leaked"

    assert all(w["window"] == window for w in writes)
    assert judge.battery_for_window(window) is None            # finished ⇒ no badge


@pytest.mark.parametrize("raw, label", [
    ({"file": "a.py", "guard": "g", "held_out": True}, "a held-out experiment"),
    ({"file": "a.py", "guard": "g", "held_out": "yes"}, "a.py: g"),     # only literal True
    ({"file": "a.py", "guard": "x" * 80}, "a.py: " + "x" * 60),
    ({"guard": "g"}, "?: g"),
    ("not a dict", "a malformed experiment"),
])
def test_the_progress_label_names_a_visible_experiment_and_hides_a_held_out_one(raw, label):
    assert judge._progress_label(raw) == label


@pytest.mark.parametrize("seconds, text", [
    (None, "?"), (-5, "0s"), (59, "59s"), (60, "1m"), (3599, "59m"), (3600, "1h00m"),
    (2 * 3600 + 7 * 60 + 30, "2h07m"),
])
def test_elapsed_formats_seconds_minutes_and_hours(seconds, text):
    assert judge.format_elapsed(seconds) == text


def test_a_died_battery_has_no_elapsed_and_never_reads_testing(_logs):
    _write_status(_logs, pid=_dead_pid())
    b = judge.battery_for_window(WINDOW)
    assert b["elapsed"] is None
    assert "testing" not in b["label"] and "idle" not in b["label"]


@pytest.mark.parametrize("order", [("A", "B", "C"), ("C", "B", "A"), ("B", "C", "A")])
def test_the_newest_run_wins_whatever_order_the_files_are_written(_logs, order):
    ages = {"A": 3000, "B": 600, "C": 1800}
    for tid in order:
        _write_status(_logs, task_id=tid, elapsed=ages[tid], done=ages[tid] // 600)
    b = judge.battery_for_window(WINDOW)
    assert (b["task_id"], b["done"]) == ("B", 1)


def _every_surface(window: str) -> dict[str, str]:
    """Everything an agent (or a human) can read about the battery right now: the model,
    the `chela peek` text and the `/api/agents` row the pane pill / dot / sidebar draw."""
    battery = judge.battery_for_window(window)
    with (
        patch.object(orchestrator.discovery, "get_windows_by_id", return_value={"@40": window}),
        patch.object(orchestrator.discovery, "get_window_cwd_by_id", return_value="/w"),
        patch.object(sessions, "transcript_for_window", return_value=None),
        patch.object(agent_manager, "session_status_map",
                     return_value={"by_pid": {7: "idle"}, "cwd_by_pid": {}}),
        patch.object(agent_manager, "claude_pid", return_value=7),
        patch.object(agent_manager, "window_type", return_value="claude"),
    ):
        peek_text = orchestrator.format_peek(orchestrator.peek("@40"))
    with _fleet(window):
        agents = dash.app.test_client().get("/api/agents").get_json()
    return {"model": json.dumps(battery, ensure_ascii=False),
            "label": battery["label"] if battery else "",
            "peek": peek_text,
            "api": json.dumps(agents, ensure_ascii=False)}


def test_a_held_out_experiment_running_now_is_named_on_no_surface(
    tmp_path, _logs, monkeypatch,
):
    """GUARD (CMX-395): a held-out experiment is staged as the CURRENT experiment of a REAL
    run, and at every status write the run makes, every surface is read back — the model,
    `chela peek` and `/api/agents`. Its guard text, and the ``file: guard`` label a visible
    experiment gets, appear on none of them; only the counts do. Positive control: the
    VISIBLE experiment's label does reach peek, so the surfaces really print `current`."""
    monkeypatch.setattr(dispatcher, "_kill_windows_named", lambda name: None)
    task_id = "abc124"
    repo = _workflow_repo(tmp_path, task_id, REAL_GUARD_TEST)
    with dispatcher._db() as conn:
        _run_row(conn, repo, task_id)
    window = judge.judge_window_name(dispatcher.resolve_run(task_id)["branch_name"])
    secret = "SECRET-held-out-guard-text"
    visible = "the off hue"
    exp_file = tmp_path / "experiments.json"
    exp_file.write_text(json.dumps({"experiments": [
        _exp(guard=secret, held_out=True),                                      # runs FIRST
        _exp(guard=visible, before='else "grey"', after='else "gray"'),
    ]}))
    snaps: list[tuple[dict, dict[str, str]]] = []
    real_write = judge._write_run_status

    def _spy(tid, status):
        real_write(tid, status)
        if tid == task_id:
            snaps.append((dict(status), _every_surface(window)))

    monkeypatch.setattr(judge, "_write_run_status", _spy)
    with patch.object(dispatcher, "_post_pr_comment", return_value=(True, "")):
        result = judge.judge_run(task_id, exp_file, cleanup=True, detached=True)
    assert result["ok"], result

    # Staged: the first experiment the run names is the held-out one, with a 0/2 count.
    staged = [(st, sf) for st, sf in snaps if st.get("current")]
    assert staged, snaps
    first_status, first_surfaces = staged[0]
    assert (first_status["done"], first_status["total"]) == (0, 2), first_status
    assert "0/2" in first_surfaces["label"] and "0/2" in first_surfaces["peek"], first_surfaces

    hidden_label = f"guard.py: {secret}"[:60]
    for status, surfaces in snaps:
        for where, text in surfaces.items():
            assert secret not in text and hidden_label not in text, (where, status, text)
    assert any(f"guard.py: {visible}" in sf["peek"] for _, sf in snaps), "positive control"
