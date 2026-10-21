"""⚖️🪜 CMX-265 — a prose-only PR never waits for a judge SLOT it would never use.

Seen 2026-10-19: a docs-only PR sat 1h+ "queued for the judge behind CMX-263, 2 slots", and
`chela merge` refused it with judge "never ran". The CMX-208 prose-only check lived inside
`_spawn_judge` — past the slot gate and a worktree checkout — so it only ran once a judge
slot was free. These tests pin that the trigger classifies BEFORE the gate:

* with every judge slot busy, a prose-only run gets `prose_only` on its head on the next tick
  and no judge spawns (negative control: move the check back after the gate ⇒ RED);
* a code PR, an always-human PR, and an unreadable file list in the same state still queue;
* the classifier reads the PR's HEAD COMMIT in the repo itself — no worktree needed.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from chela import dispatcher, inbox, judge
from chela.workflow import WorkflowDef
from tests.test_judge import _run_row, _tick, _wf

HEAD = "cafe1234"


@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    monkeypatch.setattr(judge, "judge_logs_dir", lambda: tmp_path / "judge-logs")
    monkeypatch.setenv("CHELA_JUDGE_MAX_CONCURRENT", "1")


def _busy_and_waiting(tmp_path) -> WorkflowDef:
    """One judge RUNNING (its window alive) fills the only slot; `abc123` awaits review."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, task_id="busy1", workflow_path=str(wf.path),
                 window_name="test-9", branch_name="test-9", judge_state=judge.J_RUNNING,
                 judge_sha=HEAD, judge_started_at=dispatcher._now())
        _run_row(conn, tmp_path, task_id="abc123", workflow_path=str(wf.path))
    return wf


def _tick_with_files(wf, files):
    reads: list[tuple] = []
    spawns: list[str] = []

    def changed(*args):
        reads.append(args)
        return files

    with patch.object(judge, "_changed_files", side_effect=changed):
        summary = _tick(wf, lambda w, row, sha, conn, task=None: (spawns.append(row["task_id"]), True)[1],
                        windows=("judge-test-9",), open_ids=("busy1", "abc123"))
    return summary, spawns, reads


def test_a_prose_pr_is_stamped_prose_only_while_every_judge_slot_is_busy(tmp_path):
    """⭐ The bug. Seen RED with the `_stamp_prose_only` call moved below the slot gate."""
    wf = _busy_and_waiting(tmp_path)
    summary, spawns, reads = _tick_with_files(wf, ["docs/FRONTEND_PORT_PLAN.md"])

    run = dispatcher.resolve_run("abc123")
    assert (run["judge_state"], run["judge_sha"]) == ("prose_only", HEAD)
    assert run["judge_detail"] == judge.PROSE_ONLY_DETAIL
    assert run["status"] == "awaiting_review"
    assert spawns == [] and summary["judged"] == 0
    # The HEAD commit is diffed in the repo itself, against the workflow's base branch.
    assert reads == [(wf.path.parent, "dev", HEAD)]
    # …so the "queued for the judge" notice has nothing left to fire on.
    assert inbox._judge_not_started(run) is False
    assert dispatcher.resolve_run("busy1")["judge_state"] == judge.J_RUNNING


@pytest.mark.parametrize("files", [
    ["chela/dispatcher.py"],                       # code
    ["docs/guide.md", "chela/dispatcher.py"],      # code riding along with prose
    ["docs/guide.md", "WORKFLOW.md"],              # always-human
    None,                                          # unreadable — never read as prose
], ids=["code", "code+prose", "always-human", "unknown"])
def test_a_non_prose_pr_in_the_same_state_still_queues(tmp_path, files):
    wf = _busy_and_waiting(tmp_path)
    summary, spawns, _ = _tick_with_files(wf, files)

    run = dispatcher.resolve_run("abc123")
    assert (run["judge_state"], run["judge_sha"]) == (None, None)
    assert spawns == [] and summary["judged"] == 0
    assert inbox._judge_not_started(run) is True


def test_a_free_slot_still_spawns_a_judge_for_code(tmp_path):
    """COUNTERWEIGHT: the pre-gate check must not swallow a code PR when a slot IS free."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, workflow_path=str(wf.path))
    summary, spawns, _ = _tick_with_files(wf, ["chela/dispatcher.py"])
    assert spawns == ["abc123"] and summary["judged"] == 1


def test_a_prose_pr_with_a_free_slot_is_stamped_and_never_spawned(tmp_path):
    """The stamp REPLACES the spawn: a free slot must not also launch a judge on it."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, workflow_path=str(wf.path))
    summary, spawns, _ = _tick_with_files(wf, ["docs/guide.md"])
    assert spawns == [] and summary["judged"] == 0
    assert dispatcher.resolve_run("abc123")["judge_state"] == "prose_only"


# --- rows the stamp must leave to `_spawn_judge` ------------------------------------------


def _helper(tmp_path, files=("docs/guide.md",), raises=None, live=None, **over):
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, workflow_path=str(wf.path), **over)
        row = conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()
    reads: list = []

    def changed(*a):
        reads.append(a)
        if raises is not None:
            raise raises
        return list(files)

    with patch.object(judge, "_changed_files", side_effect=changed), \
         patch.object(judge, "live_judge_run", return_value=live) as probe:
        stamped = dispatcher._stamp_prose_only(wf, row)
    return stamped, reads, probe, wf


def test_the_helper_stamps_a_plain_prose_row(tmp_path):
    """COUNTERWEIGHT for the refusals below: the same harness, nothing in the way."""
    stamped, reads, probe, wf = _helper(tmp_path)
    assert stamped is True and len(reads) == 1
    probe.assert_called_once_with("abc123", judge.judge_worktree_path(wf, "abc123"))


@pytest.mark.parametrize("over", [
    {"judge_state": judge.J_RUNNING, "judge_sha": HEAD},  # incl. an interrupted launch
    {"pr_head_sha": ""},                                   # would diff the repo's own HEAD
], ids=["running", "empty-head"])
def test_the_helper_never_stamps_a_running_or_headless_row(tmp_path, over):
    stamped, reads, _, _ = _helper(tmp_path, **over)
    assert stamped is False and reads == []
    assert dispatcher.resolve_run("abc123")["judge_state"] == over.get("judge_state")


def test_the_helper_never_stamps_under_a_live_judge_run(tmp_path):
    stamped, reads, _, _ = _helper(tmp_path, live={"pid": 1})
    assert stamped is False and reads == []
    assert dispatcher.resolve_run("abc123")["judge_state"] is None


@pytest.mark.parametrize("exc", [FileNotFoundError("git"), ValueError("embedded null byte")])
def test_an_unreadable_file_list_queues_and_never_raises(tmp_path, exc):
    stamped, reads, _, _ = _helper(tmp_path, raises=exc)
    assert stamped is False and len(reads) == 1
    assert dispatcher.resolve_run("abc123")["judge_state"] is None


# --- the real git read: the PR's head commit, with no checkout of it -----------------------


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path) -> Path:
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "dev", str(origin)], check=True, capture_output=True)
    work = tmp_path / "repo"
    subprocess.run(["git", "clone", str(origin), str(work)], check=True, capture_output=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        _git("config", k, v, cwd=work)
    (work / "app.py").write_text("VALUE = 1\n")
    _git("add", "app.py", cwd=work)
    _git("commit", "-m", "seed", cwd=work)
    _git("push", "-u", "origin", "dev", cwd=work)
    return work


def _pr(repo: Path, change) -> str:
    """Commit ``change(repo)`` on branch ``pr-1``, return to ``dev``; return the head sha."""
    _git("checkout", "-b", "pr-1", cwd=repo)
    change(repo)
    _git("commit", "-m", "the PR", cwd=repo)
    sha = _git("rev-parse", "HEAD", cwd=repo)
    _git("checkout", "dev", cwd=repo)
    return sha


def _add_doc(repo: Path):
    (repo / "docs").mkdir()
    (repo / "docs" / "plan.md").write_text("# plan\n")
    _git("add", "docs/plan.md", cwd=repo)


def _stamp(repo: Path, sha: str) -> bool:
    wf = _wf(repo)
    with dispatcher._db() as conn:
        _run_row(conn, repo, workflow_path=str(wf.path), pr_head_sha=sha)
        row = conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()
    return dispatcher._stamp_prose_only(wf, row)


def test_changed_files_reads_the_named_head_not_the_checkout(repo):
    sha = _pr(repo, _add_doc)
    assert judge._changed_files(repo, "dev", sha) == ["docs/plan.md"]
    assert judge._changed_files(repo, "dev") == []           # the checkout is `dev` itself


def test_stamp_prose_only_on_a_real_docs_commit(repo):
    sha = _pr(repo, _add_doc)
    assert _stamp(repo, sha) is True
    run = dispatcher.resolve_run("abc123")
    assert (run["judge_state"], run["judge_sha"]) == ("prose_only", sha)


def test_stamp_prose_only_refuses_the_code_side_of_a_rename(repo):
    """The rename lists `app.py` too, so it is never prose — CMX-296: it is standard."""
    def rename(r):
        (r / "docs").mkdir()
        _git("mv", "app.py", "docs/app.md", cwd=r)
    sha = _pr(repo, rename)
    assert _stamp(repo, sha) is True
    assert dispatcher.resolve_run("abc123")["judge_state"] == "standard_no_judge"


def test_stamp_prose_only_refuses_a_head_the_repo_does_not_have(repo):
    assert _stamp(repo, "0" * 40) is False
    assert dispatcher.resolve_run("abc123")["judge_state"] is None
