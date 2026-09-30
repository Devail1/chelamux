"""``chela close <run> --reason`` (CMX-406) — walk away from a run on purpose, visibly.

Before this, the only path to ``status='closed'`` was reconcile's closed-PR branch. A run
superseded by a successor ticket (CMX-400 → CMX-403), whose idle window the orchestrator
closed to free the slot, sat on the Work view as "FAILED — tmux window disappeared": true,
and misleading. The operator must not hand-edit the daemon-owned ``scheduler.db``.

These pin the edge: which statuses close, the stored and SHOWN reason, the refusal to
kill a live busy agent without ``--force``, the PR comment / ``gh pr close`` projection,
and — the terminal half — that a closed run is never claimed again.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from chela import dispatcher, event_log
from tests.test_dispatcher_rework import _FakeTmux, _row, _Source, _status, _wf


@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")


def _seed(tmp_path, **over):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    fields = {"workflow_path": str(repo / "WORKFLOW.md"), "status": "failed",
              "window_name": "test-1",
              "last_error": "tmux window disappeared"}
    fields.update(over)
    with dispatcher._db() as conn:
        _row(conn, **fields)


def _fake(*windows):
    fake = _FakeTmux()
    fake.windows = list(windows)
    return fake


def _closed_events():
    return [e for e in event_log.read()["events"] if e["type"] == "run_closed"]


# --- a failed run → closed, the reason stored AND shown -------------------------------

def test_a_failed_run_closes_with_its_reason_stored_and_recorded(tmp_path):
    _seed(tmp_path, pr_url=None, pr_state=None)
    fake = _fake()                                   # the window is gone
    with patch.object(dispatcher.subprocess, "run", side_effect=fake.run):
        result = dispatcher.close_run("abc123", "superseded by cmx-403")

    assert result["ok"] is True, result
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "closed"
    assert run["close_reason"] == "superseded by cmx-403"
    # the original error is history, not rewritten
    assert run["last_error"] == "tmux window disappeared"
    last = dispatcher.reviews_of(run)[-1]
    assert (last["verdict"], last["body"], last["from_status"]) == (
        "closed", "superseded by cmx-403", "failed")
    events = _closed_events()
    assert len(events) == 1
    assert events[0]["payload"]["reason"] == "superseded by cmx-403"
    assert events[0]["payload"]["task_id"] == "abc123"
    # never the branch: nothing ran `git branch -D` / `git push --delete`
    assert not any(isinstance(c, list) and c[:1] == ["git"] for c in fake.calls)


def test_a_reason_is_required(tmp_path):
    _seed(tmp_path)
    with patch.object(dispatcher.subprocess, "run", side_effect=_fake().run):
        result = dispatcher.close_run("abc123", "   ")
    assert result["ok"] is False
    assert dispatcher.resolve_run("abc123")["status"] == "failed"


@pytest.mark.parametrize("status", ["done", "closed"])
def test_a_terminal_run_is_refused(tmp_path, status):
    """NEGATIVE CONTROL for the acceptance tests: the same call on a terminal row changes
    nothing — so "it closed" above is the status check passing, not a blind UPDATE."""
    _seed(tmp_path, status=status)
    with patch.object(dispatcher.subprocess, "run", side_effect=_fake().run):
        result = dispatcher.close_run("abc123", "superseded")
    assert result["ok"] is False
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == status
    assert run["close_reason"] is None
    assert _closed_events() == []


def test_a_run_whose_pr_merged_is_refused(tmp_path):
    _seed(tmp_path, status="awaiting_review", pr_state="merged")
    with patch.object(dispatcher.subprocess, "run", side_effect=_fake().run):
        result = dispatcher.close_run("abc123", "superseded")
    assert result["ok"] is False
    assert "MERGED" in result["error"]
    assert dispatcher.resolve_run("abc123")["status"] == "awaiting_review"


# --- ⭐ the case that must be ACCEPTED: needs_human closes and frees its slot ----------

def test_closing_a_needs_human_run_succeeds_and_frees_its_slot(tmp_path):
    _seed(tmp_path, status="needs_human", pr_url=None, pr_state=None, window_name=None)
    with dispatcher._db() as conn:
        # a second, genuinely running run — the slot count must see only it afterwards
        _row(conn, task_id="other", status="running", window_name="other-1",
             workflow_path=str(tmp_path / "repo" / "WORKFLOW.md"))
    with patch.object(dispatcher.subprocess, "run", side_effect=_fake().run):
        result = dispatcher.close_run("abc123", "abandoned — needs a redesign")

    assert result["ok"] is True, result
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "closed"
    assert run["close_reason"] == "abandoned — needs a redesign"
    with dispatcher._db() as conn:
        active = conn.execute(
            "SELECT task_id FROM runs WHERE status IN ({})".format(
                ",".join("?" * len(dispatcher.ACTIVE_STATUSES))),
            dispatcher.ACTIVE_STATUSES,
        ).fetchall()
    assert [r["task_id"] for r in active] == ["other"]


# --- a running run: never kill a working agent silently --------------------------------

def test_a_running_run_with_a_live_busy_agent_is_refused_without_force(tmp_path):
    _seed(tmp_path, status="running", pr_url=None, pr_state=None)
    fake = _fake(("@7", "test-1"))
    with patch.object(dispatcher.subprocess, "run", side_effect=fake.run), \
         patch.object(dispatcher, "_agent_status", return_value="busy") as status:
        result = dispatcher.close_run("abc123", "superseded")

    assert result["ok"] is False
    assert "busy" in result["error"] and "--force" in result["error"]
    status.assert_called_once_with("@7")
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "running"
    assert run["close_reason"] is None
    assert not any(isinstance(c, list) and c[:2] == ["tmux", "kill-window"] for c in fake.calls)
    assert _closed_events() == []


@pytest.mark.parametrize("state", ["waiting", None])
def test_a_running_run_whose_agent_is_not_known_idle_is_refused(tmp_path, state):
    _seed(tmp_path, status="running", pr_url=None, pr_state=None)
    with patch.object(dispatcher.subprocess, "run", side_effect=_fake(("@7", "test-1")).run), \
         patch.object(dispatcher, "_agent_status", return_value=state):
        result = dispatcher.close_run("abc123", "superseded")
    assert result["ok"] is False
    assert dispatcher.resolve_run("abc123")["status"] == "running"


def test_force_closes_a_busy_run_and_kills_its_window(tmp_path):
    _seed(tmp_path, status="running", pr_url=None, pr_state=None)
    fake = _fake(("@7", "test-1"))
    with patch.object(dispatcher.subprocess, "run", side_effect=fake.run), \
         patch.object(dispatcher, "_agent_status", return_value="busy"):
        result = dispatcher.close_run("abc123", "superseded", force=True)

    assert result["ok"] is True and result["forced"] is True
    assert dispatcher.resolve_run("abc123")["status"] == "closed"
    kills = [c for c in fake.calls if isinstance(c, list) and c[:2] == ["tmux", "kill-window"]]
    assert [c[-1].rsplit(":", 1)[-1] for c in kills] == ["@7"]


def test_a_running_run_with_an_idle_agent_closes_and_its_window_goes(tmp_path):
    _seed(tmp_path, status="running", pr_url=None, pr_state=None)
    fake = _fake(("@7", "test-1"))
    with patch.object(dispatcher.subprocess, "run", side_effect=fake.run), \
         patch.object(dispatcher, "_agent_status", return_value="idle"):
        result = dispatcher.close_run("abc123", "superseded")
    assert result["ok"] is True and result["forced"] is False
    assert result["window_killed"] is True
    assert dispatcher.resolve_run("abc123")["status"] == "closed"


def test_a_running_run_whose_window_is_gone_closes_without_force(tmp_path):
    _seed(tmp_path, status="running", pr_url=None, pr_state=None)
    with patch.object(dispatcher.subprocess, "run", side_effect=_fake(("@9", "someone-else")).run), \
         patch.object(dispatcher, "_agent_status", return_value="busy") as status:
        result = dispatcher.close_run("abc123", "superseded")
    assert result["ok"] is True
    status.assert_not_called()                       # no window → nobody to ask
    assert dispatcher.resolve_run("abc123")["status"] == "closed"


def test_a_claimed_run_with_no_window_yet_is_refused_without_force(tmp_path):
    """`_spawn` is mid-flight: the window is about to exist."""
    _seed(tmp_path, status="claimed", window_name=None, pr_url=None, pr_state=None)
    with patch.object(dispatcher.subprocess, "run", side_effect=_fake().run):
        result = dispatcher.close_run("abc123", "superseded")
    assert result["ok"] is False
    assert dispatcher.resolve_run("abc123")["status"] == "claimed"


# --- the PR: comment, or close with the comment -------------------------------------------

def test_close_pr_calls_gh_pr_close_with_the_reason_as_its_comment(tmp_path):
    _seed(tmp_path, status="awaiting_review", pr_state="open")
    fake = _fake()
    with patch.object(dispatcher.subprocess, "run", side_effect=fake.run):
        result = dispatcher.close_run("abc123", "superseded by cmx-403", close_pr=True)

    assert result["ok"] is True and result["pr_closed"] is True
    gh = [c for c in fake.calls if isinstance(c, list) and c[:1] == ["gh"]]
    assert len(gh) == 1
    assert gh[0][:4] == ["gh", "pr", "close", "80"]
    assert gh[0][4] == "--comment"
    assert "superseded by cmx-403" in gh[0][5]


def test_without_close_pr_the_pr_stays_open_and_gets_the_reason_as_a_comment(tmp_path):
    _seed(tmp_path, status="awaiting_review", pr_state="open")
    fake = _fake()
    with patch.object(dispatcher.subprocess, "run", side_effect=fake.run):
        result = dispatcher.close_run("abc123", "superseded by cmx-403")

    assert result["ok"] is True and result["pr_closed"] is False
    gh = [c for c in fake.calls if isinstance(c, list) and c[:1] == ["gh"]]
    assert [c[:3] for c in gh] == [["gh", "pr", "comment"]]


@pytest.mark.parametrize("remove", [False, True])
def test_remove_worktree_is_opt_in(tmp_path, remove):
    """Kept by default; removed only with ``--remove-worktree``. ``load_workflow`` is stubbed
    so a missing WORKFLOW.md cannot be what keeps the worktree (it would raise, be caught,
    and mask a cleanup that runs unconditionally)."""
    wt = tmp_path / "wt"
    wt.mkdir()
    _seed(tmp_path, pr_url=None, pr_state=None, worktree_path=str(wt))
    with patch.object(dispatcher.subprocess, "run", side_effect=_fake().run), \
         patch.object(dispatcher, "load_workflow", return_value=object()), \
         patch.object(dispatcher, "_cleanup_worktree_on_done") as cleanup:
        result = dispatcher.close_run("abc123", "superseded", remove_worktree=remove)
    assert result["ok"] is True
    assert cleanup.call_count == (1 if remove else 0)
    assert wt.is_dir()                   # the stubbed cleanup never touched the disk


# --- pr_state NULL is OPEN, never "nothing to close" ---------------------------------------

def test_pr_is_open_counts_an_unrefreshed_null_pr_state_as_open():
    """A PR the tick has not refreshed yet (``pr_state`` NULL) is not known to be settled."""
    assert dispatcher.pr_is_open({"pr_url": "https://x/pull/80", "pr_state": None}) is True
    assert dispatcher.pr_is_open({"pr_url": "https://x/pull/80", "pr_state": "open"}) is True
    # counterweights: settled, or no PR at all
    assert dispatcher.pr_is_open({"pr_url": "https://x/pull/80", "pr_state": "closed"}) is False
    assert dispatcher.pr_is_open({"pr_url": "https://x/pull/80", "pr_state": "merged"}) is False
    assert dispatcher.pr_is_open({"pr_url": None, "pr_state": None}) is False


def test_close_pr_on_a_run_whose_pr_state_is_still_null_closes_that_pr(tmp_path):
    """End to end through close_run: an unrefreshed PR still gets ``gh pr close --comment``."""
    _seed(tmp_path, status="awaiting_review", pr_state=None)
    assert dispatcher.resolve_run("abc123")["pr_url"]          # the fixture carries a PR
    fake = _fake()
    with patch.object(dispatcher.subprocess, "run", side_effect=fake.run):
        result = dispatcher.close_run("abc123", "superseded by cmx-403", close_pr=True)

    assert result["ok"] is True
    assert result["pr_open"] is True and result["pr_closed"] is True
    gh = [c for c in fake.calls if isinstance(c, list) and c[:1] == ["gh"]]
    assert [c[:3] for c in gh] == [["gh", "pr", "close"]]


# --- compare-and-swap: a tick that moved the row meanwhile WINS -------------------------

def test_a_row_moved_by_a_tick_between_read_and_write_is_left_alone(tmp_path):
    """close_run reads the row as ``failed``; before its UPDATE lands, a tick re-claims it.
    The write must NOT land on top of that — status, reason, history and events untouched."""
    _seed(tmp_path, pr_url=None, pr_state=None)
    real_liveness = dispatcher._close_liveness

    def _tick_moves_it(run):
        with dispatcher._db() as conn:
            conn.execute("UPDATE runs SET status='running' WHERE task_id=?", (run["task_id"],))
            conn.commit()
        return real_liveness(run)

    with patch.object(dispatcher.subprocess, "run", side_effect=_fake().run), \
         patch.object(dispatcher, "_close_liveness", side_effect=_tick_moves_it):
        result = dispatcher.close_run("abc123", "superseded by cmx-403")

    assert result["ok"] is False
    assert "'running'" in result["error"]
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "running"
    assert run["close_reason"] is None
    assert _closed_events() == []


def test_the_same_path_with_no_intervening_tick_does_close(tmp_path):
    """NEGATIVE CONTROL for the race test: the identical patched path, minus the tick's
    write, closes — so the refusal above is the CAS, not the patch breaking close_run."""
    _seed(tmp_path, pr_url=None, pr_state=None)
    with patch.object(dispatcher.subprocess, "run", side_effect=_fake().run), \
         patch.object(dispatcher, "_close_liveness", side_effect=dispatcher._close_liveness):
        result = dispatcher.close_run("abc123", "superseded by cmx-403")
    assert result["ok"] is True
    assert dispatcher.resolve_run("abc123")["status"] == "closed"


# --- a closed run is NEVER re-claimed -----------------------------------------------------

def test_a_closed_run_is_never_re_claimed_by_the_next_tick(tmp_path):
    """Its task line is still OPEN in the tracker (nothing struck it) and a slot is free —
    exactly the state in which a `failed` row IS re-dispatched. `closed` must not be."""
    wf = _wf(tmp_path, concurrency={"max": 2})
    source = _Source("abc123")
    task = source.list_open_tasks()[0]
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), status="failed", window_name=None,
             pr_url=None, pr_state=None)
    with patch.object(dispatcher.subprocess, "run", side_effect=_FakeTmux().run):
        assert dispatcher.close_run("abc123", "superseded by cmx-403")["ok"] is True

    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=source), \
         patch.object(dispatcher, "_claim_order", return_value=[task]), \
         patch.object(dispatcher, "ensure_worktree") as fresh_fork, \
         patch.object(dispatcher.subprocess, "run", side_effect=_FakeTmux().run):
        summary = dispatcher.tick(wf.path)

    assert summary["dispatched"] == 0
    assert fresh_fork.call_count == 0
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "closed"
    assert run["attempt"] == 1


# --- the CLI, end to end: a temp CHELA_DIR, a subprocess, stubbed tmux + gh ---------------

FAKE_BIN = """#!{python}
import json, os, sys
with open(os.environ["STUB_LOG"], "a") as fh:
    fh.write(json.dumps([os.path.basename(sys.argv[0])] + sys.argv[1:]) + "\\n")
sys.exit(0)
"""


def test_chela_close_cli_end_to_end(tmp_path):
    chela_dir = tmp_path / "chela-home"
    chela_dir.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("gh", "tmux"):
        stub = bindir / name
        stub.write_text(FAKE_BIN.format(python=sys.executable))
        stub.chmod(0o755)
    stub_log = tmp_path / "stub.log"

    with patch.object(dispatcher, "DB_PATH", chela_dir / "scheduler.db"):
        with dispatcher._db() as conn:
            _row(conn, task_id="714b177556ff", status="failed",
                 workflow_path=str(repo / "WORKFLOW.md"), window_name="cmx-400",
                 branch_name="cmx-400", last_error="tmux window disappeared")

    env = {
        "PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}",
        "HOME": str(tmp_path), "CHELA_DIR": str(chela_dir), "CHELA_ENV_FILE": "",
        "CLAUDE_CONFIG_DIR": str(tmp_path / "claude"), "STUB_LOG": str(stub_log),
        "PYTHONPATH": str(Path(dispatcher.__file__).resolve().parent.parent),
    }
    out = subprocess.run(
        [sys.executable, "-m", "chela.main", "close", "cmx-400",
         "--reason", "superseded by cmx-403", "--close-pr"],
        env=env, capture_output=True, text=True, timeout=60, cwd=tmp_path,
        stdin=subprocess.DEVNULL,
    )
    assert out.returncode == 0, out.stdout + out.stderr
    assert "closed" in out.stdout

    conn = sqlite3.connect(chela_dir / "scheduler.db")
    status, reason = conn.execute(
        "SELECT status, close_reason FROM runs WHERE task_id=?", ("714b177556ff",)
    ).fetchone()
    conn.close()
    assert (status, reason) == ("closed", "superseded by cmx-403")
    calls = [json.loads(line) for line in stub_log.read_text().splitlines()]
    gh = [c for c in calls if c[0] == "gh"]
    assert [c[:5] for c in gh] == [["gh", "pr", "close", "80", "--comment"]]
    assert "superseded by cmx-403" in gh[0][5]
    events = (chela_dir / "events.jsonl").read_text()
    assert '"run_closed"' in events

    # ...and run twice, it refuses: closed is terminal
    again = subprocess.run(
        [sys.executable, "-m", "chela.main", "close", "cmx-400", "--reason", "again"],
        env=env, capture_output=True, text=True, timeout=60, cwd=tmp_path,
        stdin=subprocess.DEVNULL,
    )
    assert again.returncode == 1
    assert "'closed'" in again.stdout
