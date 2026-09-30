"""``chela reopen`` on a ``done`` run (CMX-387) — the exit from the stuck-``done`` trap.

A ``done`` row whose PR is still OPEN and whose head moved had no supported recovery:
``reopen`` refused anything but ``needs_human``, ``merge`` refuses ``done``, and the only
way out was a hand ``UPDATE`` on the runs DB (hit live on #395). ``reopen`` now accepts a
``done`` run, but only when ALL of these hold — every "cannot tell" is a refusal:

1. the PR is live-read from GitHub as OPEN (merged / closed / unreadable → refused);
2. the new-commit gate passes (live head ≠ ``judge_sha``; unreadable head → refused);
3. the task is not struck ``- [x]`` in its tracker.

``gh`` is faked at the ``subprocess.run`` boundary — never by stubbing ``_read_pr_checks``
or ``_read_pr_status`` — so the real parsers (returncode checks included) stand between
the fake's output and the decision. The runs DB is a per-test temp file (and the
end-to-end test runs ``chela`` in a subprocess with an explicit, temp ``CHELA_DIR``).
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

from chela import dispatcher
from chela.sources.markdown import _title_id

TITLE = "do a thing"
TASK_ID = _title_id("TODO.md", TITLE)
PR_URL = "https://github.com/o/r/pull/80"

WORKFLOW = """---
project_key: CMX
tracker:
  kind: markdown
  path: TODO.md
workspace:
  root: {root}
  base_branch: dev
---
seed
"""


@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")


def _repo(tmp_path: Path, struck: bool = False) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    (repo / "WORKFLOW.md").write_text(WORKFLOW.format(root=tmp_path / "wt"))
    mark = "x" if struck else " "
    (repo / "TODO.md").write_text(f"# TODO\n\n- [{mark}] {TITLE}\n- [ ] another task\n")
    return repo


def _row(conn, repo: Path, **over) -> None:
    fields = {
        "task_id": TASK_ID, "workflow_path": str(repo / "WORKFLOW.md"), "title": TITLE,
        "status": "done", "window_name": None, "worktree_path": None,
        "branch_name": "cmx-1", "started_at": "2026-09-20T10:00:00+00:00", "attempt": 1,
        "task_number": 1, "pr_url": PR_URL, "pr_state": "open",
        "rework_count": 1, "judge_sha": "judged000001", "pr_head_sha": "judged000001",
        "review_history": json.dumps([
            {"round": 1, "at": "t1", "body": "fix the wire", "verdict": "changes_requested"},
        ]),
    }
    fields.update(over)
    cols = ", ".join(fields)
    conn.execute(
        f"INSERT INTO runs ({cols}) VALUES ({', '.join('?' * len(fields))})",
        tuple(fields.values()),
    )
    conn.commit()


class _R:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def _gh(state="OPEN", head="freshfix0002", head_rc=0, calls=None):
    """Route `gh` by shape: `--json state,mergeable` (the PR-state read),
    `--json statusCheckRollup,headRefOid` (the new-commit gate's head read), anything else
    (the PR comment) succeeds. `head_rc` makes the head read exit non-zero while STILL
    printing well-formed JSON — so only `_read_pr_checks`' returncode check stands between
    it and a sha."""
    def _run(cmd, *a, **k):
        if calls is not None:
            calls.append(list(cmd))
        if "--json" in cmd:
            fields = cmd[cmd.index("--json") + 1]
            if fields == "state,mergeable":
                return _R(stdout=json.dumps({"state": state, "mergeable": "MERGEABLE"}))
            return _R(returncode=head_rc,
                      stdout=json.dumps({"headRefOid": head, "statusCheckRollup": []}),
                      stderr="HTTP 502" if head_rc else "")
        return _R()
    return _run


def _reopen(gh):
    with patch.object(dispatcher.subprocess, "run", side_effect=gh):
        return dispatcher.reopen(TASK_ID, "pushed a fix after it went done")


# --- (a) ⭐ the exit: done + OPEN + moved head + not struck ⇒ awaiting_review ------------

def test_a_done_run_with_an_open_pr_and_a_moved_head_is_reopened(tmp_path):
    repo = _repo(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, repo)
    calls: list[list[str]] = []
    result = _reopen(_gh(head="freshfix0002", calls=calls))

    assert result["ok"] is True, result
    assert result["status"] == "awaiting_review"
    assert result["reopen_count"] == 1
    run = dispatcher.resolve_run(TASK_ID)
    assert run["status"] == "awaiting_review"
    # refreshed from the LIVE read, so the judge fires on the new head
    assert run["pr_head_sha"] == "freshfix0002"
    assert run["reopen_count"] == 1
    reviews = dispatcher.reviews_of(dict(run))
    assert len(reviews) == 2 and reviews[-1]["verdict"] == "reopened"
    assert [c for c in calls if c[:3] == ["gh", "pr", "comment"]], "no PR comment posted"


# --- (b) a merged or closed PR stays closed ---------------------------------------------

@pytest.mark.parametrize("state", ["MERGED", "CLOSED"])
def test_a_done_run_whose_pr_is_not_open_is_refused(tmp_path, state):
    repo = _repo(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, repo)
    result = _reopen(_gh(state=state, head="freshfix0002"))

    assert result["ok"] is False
    assert state in result["error"]                       # names the state
    assert dispatcher.resolve_run(TASK_ID)["status"] == "done"


def test_a_done_run_whose_pr_state_is_unreadable_is_refused(tmp_path):
    repo = _repo(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, repo)

    def _run(cmd, *a, **k):
        if "state,mergeable" in cmd:
            return _R(returncode=1, stdout=json.dumps({"state": "OPEN"}), stderr="HTTP 502")
        return _gh()(cmd, *a, **k)

    result = _reopen(_run)
    assert result["ok"] is False
    assert dispatcher.resolve_run(TASK_ID)["status"] == "done"


# --- (c) the new-commit gate still applies to a done run --------------------------------

def test_a_done_run_whose_head_is_the_judged_commit_is_refused(tmp_path):
    repo = _repo(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, repo)
    result = _reopen(_gh(head="judged000001"))

    assert result["ok"] is False
    assert "same" in result["error"].lower()
    run = dispatcher.resolve_run(TASK_ID)
    assert run["status"] == "done"
    assert run["pr_head_sha"] == "judged000001"


# --- (d) an unreadable head is a refusal, never "moved" ---------------------------------

def test_a_done_run_whose_head_read_exits_nonzero_is_refused(tmp_path):
    repo = _repo(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, repo)
    result = _reopen(_gh(head="freshfix0002", head_rc=1))

    assert result["ok"] is False
    assert "could not read" in result["error"]
    assert dispatcher.resolve_run(TASK_ID)["status"] == "done"


# --- (e) a struck task is finished, whatever the PR says --------------------------------

def test_a_done_run_whose_task_is_struck_is_refused(tmp_path):
    repo = _repo(tmp_path, struck=True)
    with dispatcher._db() as conn:
        _row(conn, repo)
    result = _reopen(_gh(head="freshfix0002"))

    assert result["ok"] is False
    assert "struck" in result["error"]
    assert dispatcher.resolve_run(TASK_ID)["status"] == "done"


def test_a_done_run_whose_tracker_cannot_be_read_is_refused(tmp_path):
    repo = _repo(tmp_path)
    (repo / "TODO.md").unlink()
    with dispatcher._db() as conn:
        _row(conn, repo)
    result = _reopen(_gh(head="freshfix0002"))

    assert result["ok"] is False
    assert "tracker could not be read" in result["error"]
    assert dispatcher.resolve_run(TASK_ID)["status"] == "done"


# Every fixture above is a loadable MARKDOWN workflow, so the two arms below were never
# reached: had either refusal been folded into `return None`, the run would have fallen
# straight through to the new-commit gate — which the moved head PASSES — and reopened.
# So each fixture here also moves the head: `ok is True` is the result a missing arm gives.

GH_ISSUES_WORKFLOW = """---
project_key: CMX
tracker:
  kind: gh_issues
  repo: o/r
  require_label: ready-for-agent
workspace:
  root: {root}
  base_branch: dev
---
seed
"""


def test_a_done_run_on_a_tracker_kind_with_no_struck_line_is_refused(tmp_path):
    repo = _repo(tmp_path)
    (repo / "WORKFLOW.md").write_text(GH_ISSUES_WORKFLOW.format(root=tmp_path / "wt"))
    with dispatcher._db() as conn:
        _row(conn, repo)
    result = _reopen(_gh(head="freshfix0002"))

    assert result["ok"] is False, result
    assert "no struck-line" in result["error"]
    assert dispatcher.resolve_run(TASK_ID)["status"] == "done"


@pytest.mark.parametrize("breakage", ["missing", "unknown-kind"])
def test_a_done_run_whose_workflow_cannot_be_loaded_is_refused(tmp_path, breakage):
    repo = _repo(tmp_path)
    wf = repo / "WORKFLOW.md"
    if breakage == "missing":
        wf.unlink()
    else:
        wf.write_text(WORKFLOW.format(root=tmp_path / "wt").replace("kind: markdown",
                                                                    "kind: jira"))
    with dispatcher._db() as conn:
        _row(conn, repo)
    result = _reopen(_gh(head="freshfix0002"))

    assert result["ok"] is False, result
    assert "tracker could not be loaded" in result["error"]
    assert dispatcher.resolve_run(TASK_ID)["status"] == "done"


# --- (f) ⭐ a needs_human reopen behaves exactly as before -------------------------------

def test_a_needs_human_reopen_is_unchanged_no_pr_state_read_no_tracker_read(tmp_path):
    """The `done` checks must not leak onto the `needs_human` path: no extra `gh` call, no
    tracker read (the workflow file here does not even exist), and the same row, result
    and comment the pre-CMX-387 path produced."""
    missing = tmp_path / "nowhere"
    with dispatcher._db() as conn:
        _row(conn, missing, status="needs_human", last_error="rework cap reached")
    calls: list[list[str]] = []
    result = _reopen(_gh(head="freshfix0002", calls=calls))

    assert result == {
        "ok": True, "task_id": TASK_ID, "status": "awaiting_review",
        "branch_name": "cmx-1", "pr_url": PR_URL,
        "rework_count": 1, "max_reworks": dispatcher._rework_cap(dispatcher.resolve_run(TASK_ID)),
        "reopen_count": 1, "comment_posted": True, "comment_detail": result["comment_detail"],
    }
    assert [c[:4] for c in calls] == [
        ["gh", "pr", "view", "80"],                       # the head read, and only it
        ["gh", "pr", "comment", "80"],
    ]
    assert calls[0][calls[0].index("--json") + 1] == "statusCheckRollup,headRefOid"
    run = dispatcher.resolve_run(TASK_ID)
    assert run["status"] == "awaiting_review"
    assert run["pr_head_sha"] == "freshfix0002"
    assert run["last_error"] is None
    assert run["first_reopen_head_sha"] == "freshfix0002"


# --- the compare-and-swap still holds on the done path ----------------------------------

def test_a_done_reopen_will_not_overwrite_a_row_that_moved_under_it(tmp_path):
    repo = _repo(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, repo)
    stale = dict(dispatcher.resolve_run(TASK_ID))         # read: done
    with dispatcher._db() as conn:                        # ...a tick closes it
        conn.execute("UPDATE runs SET status='closed' WHERE task_id=?", (TASK_ID,))
        conn.commit()
    with patch.object(dispatcher, "resolve_run", return_value=stale):
        result = _reopen(_gh(head="freshfix0002"))

    assert result["ok"] is False
    with dispatcher._db() as conn:
        status = conn.execute("SELECT status FROM runs WHERE task_id=?", (TASK_ID,)).fetchone()[0]
    assert status == "closed"


# --- end to end: `chela reopen` in a subprocess, temp CHELA_DIR, fake `gh` on PATH -------

FAKE_GH = """#!{python}
import json, sys
args = sys.argv[1:]
if "--json" in args:
    fields = args[args.index("--json") + 1]
    if fields == "state,mergeable":
        print(json.dumps({{"state": "OPEN", "mergeable": "MERGEABLE"}}))
    else:
        print(json.dumps({{"headRefOid": "freshfix0002", "statusCheckRollup": []}}))
sys.exit(0)
"""


def test_chela_reopen_on_a_done_run_end_to_end(tmp_path):
    chela_dir = tmp_path / "chela-home"
    chela_dir.mkdir()
    repo = _repo(tmp_path)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    gh = bindir / "gh"
    gh.write_text(FAKE_GH.format(python=sys.executable))
    gh.chmod(0o755)

    # seed the SUBPROCESS's db (its DB_PATH is `$CHELA_DIR/scheduler.db`)
    with patch.object(dispatcher, "DB_PATH", chela_dir / "scheduler.db"):
        with dispatcher._db() as conn:
            _row(conn, repo)

    env = {
        "PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}",
        "HOME": str(tmp_path), "CHELA_DIR": str(chela_dir), "CHELA_ENV_FILE": "",
        "CLAUDE_CONFIG_DIR": str(tmp_path / "claude"),
        "PYTHONPATH": str(Path(dispatcher.__file__).resolve().parent.parent),
    }
    out = subprocess.run(
        [sys.executable, "-m", "chela.main", "reopen", TASK_ID, "--reason", "fixed"],
        env=env, capture_output=True, text=True, timeout=60, cwd=tmp_path,
    )
    assert out.returncode == 0, out.stdout + out.stderr

    conn = sqlite3.connect(chela_dir / "scheduler.db")
    status, head = conn.execute(
        "SELECT status, pr_head_sha FROM runs WHERE task_id=?", (TASK_ID,)
    ).fetchone()
    conn.close()
    assert (status, head) == ("awaiting_review", "freshfix0002")
