"""The dispatcher owns the tracker strike — agents never touch the file.

Two writers on one file (the agent striking its own line in its branch, the
orchestrator appending items to the base branch behind it) conflicted on every
single dispatched PR. Now the dispatcher strikes the line on the base branch
once the PR has merged. These tests pin the strike itself (match by task id,
idempotent, never guess) and the git guards that keep an unattended writer from
mangling the base branch.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from chela.sources.markdown import MarkdownSource, _title_id, strike_lines
from chela import dispatcher
from chela.workflow import WorkflowDef


# --- the strike itself (pure, no repo) -------------------------------------

TRACKER = "TODO.md"


def _id(title: str) -> str:
    return _title_id(TRACKER, title)


def test_strike_matches_by_task_id_not_position():
    text = "# TODO\n\n- [ ] first\n- [ ] second\n- [ ] third\n"
    new, results = strike_lines(text, TRACKER, [_id("second")])
    assert results == {_id("second"): "struck"}
    assert new == "# TODO\n\n- [ ] first\n- [x] second\n- [ ] third\n"


def test_strike_is_idempotent_on_an_already_struck_line():
    # An agent that struck its own line out of habit must not break the merge.
    text = "- [x] done already\n"
    new, results = strike_lines(text, TRACKER, [_id("done already")])
    assert results == {_id("done already"): "already"}
    assert new == text  # byte-for-byte, so there is nothing to commit


def test_strike_skips_a_task_whose_line_a_human_edited():
    # The id IS the hash of the title, so an edited title is a different task.
    # Guessing at a near-match would strike the wrong line.
    text = "- [ ] the title, but edited by a human\n"
    new, results = strike_lines(text, TRACKER, [_id("the original title")])
    assert results == {_id("the original title"): "missing"}
    assert new == text


def test_strike_preserves_the_rest_of_the_line_byte_for_byte():
    title = "**Bold** item with `[ ]` in its text — and trailing space "
    text = f"- [ ] {title}\n"
    new, results = strike_lines(text, TRACKER, [_id(title)])
    assert results[_id(title)] == "struck"
    assert new == f"- [x] {title}\n"  # only the checkbox changed


def test_strike_batches_several_ids_in_one_pass():
    text = "- [ ] a\n- [ ] b\n- [ ] c\n"
    new, results = strike_lines(text, TRACKER, [_id("a"), _id("c")])
    assert results == {_id("a"): "struck", _id("c"): "struck"}
    assert new == "- [x] a\n- [ ] b\n- [x] c\n"


def test_close_tasks_writes_the_file_and_round_trips_through_the_source(tmp_path):
    (tmp_path / "TODO.md").write_text("- [ ] ship it\n- [ ] later\n")
    wf = WorkflowDef(
        path=tmp_path / "WORKFLOW.md",
        config={"tracker": {"kind": "markdown", "path": "TODO.md"}},
        prompt_template="",
    )
    source = MarkdownSource(wf)
    task = next(t for t in source.list_open_tasks() if t.title == "ship it")

    assert source.close_tasks([task.id]) == {task.id: "struck"}
    assert (tmp_path / "TODO.md").read_text() == "- [x] ship it\n- [ ] later\n"
    # Struck tasks leave the open set — which is what stops a re-dispatch.
    assert [t.title for t in source.list_open_tasks()] == ["later"]
    # And a second call is a no-op, not a second write.
    assert source.close_tasks([task.id]) == {task.id: "already"}


# --- the git guards (an unattended writer on the base branch) ---------------

@pytest.fixture
def repo(tmp_path):
    """A real git repo on `dev` with a tracker and an `origin` it can push to."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "dev", str(origin)], check=True, capture_output=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", str(origin), str(work)], check=True, capture_output=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        subprocess.run(["git", "-C", str(work), "config", k, v], check=True, capture_output=True)
    (work / "TODO.md").write_text("- [ ] alpha\n- [ ] beta\n")
    subprocess.run(["git", "-C", str(work), "add", "TODO.md"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "commit", "-m", "seed"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "push", "-u", "origin", "dev"], check=True, capture_output=True)
    return work


def _wf(repo: Path) -> WorkflowDef:
    return WorkflowDef(
        path=repo / "WORKFLOW.md",
        config={
            "tracker": {"kind": "markdown", "path": "TODO.md"},
            "workspace": {"base_branch": "dev"},
        },
        prompt_template="",
    )


def _source(repo: Path) -> MarkdownSource:
    return MarkdownSource(_wf(repo))


def _log(repo: Path) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(repo), "log", "--format=%s"], capture_output=True, text=True, check=True
    )
    return out.stdout.split("\n")


def test_strike_commits_and_pushes_to_the_base_branch(repo):
    source = _source(repo)
    alpha, beta = (t.id for t in source.list_open_tasks())

    assert dispatcher._strike_merged_tasks(_wf(repo), source, [alpha]) == 1

    assert (repo / "TODO.md").read_text() == "- [x] alpha\n- [ ] beta\n"
    assert "chore(TODO.md): strike 1 merged task" in _log(repo)[0]
    # It landed on the remote, not just locally.
    local = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout
    remote = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "origin/dev"], capture_output=True, text=True, check=True
    ).stdout
    assert local == remote


def test_two_runs_reconciling_together_produce_one_commit(repo):
    source = _source(repo)
    ids = [t.id for t in source.list_open_tasks()]
    before = len(_log(repo))

    assert dispatcher._strike_merged_tasks(_wf(repo), source, ids) == 2

    assert (repo / "TODO.md").read_text() == "- [x] alpha\n- [x] beta\n"
    assert len(_log(repo)) == before + 1  # ONE commit, not one per task
    assert "strike 2 merged tasks" in _log(repo)[0]


def test_strike_fast_forwards_a_stale_base_branch_before_writing(repo, tmp_path):
    # The orchestrator appended a new item to dev while the run was in flight —
    # the exact race that used to conflict. We must pick it up, not clobber it.
    other = tmp_path / "other"
    subprocess.run(["git", "clone", str(tmp_path / "origin.git"), str(other)], check=True, capture_output=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        subprocess.run(["git", "-C", str(other), "config", k, v], check=True, capture_output=True)
    (other / "TODO.md").write_text("- [ ] brand new item\n- [ ] alpha\n- [ ] beta\n")
    subprocess.run(["git", "-C", str(other), "commit", "-am", "new item"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(other), "push"], check=True, capture_output=True)

    source = _source(repo)
    alpha = next(t.id for t in source.list_open_tasks() if t.title == "alpha")
    assert dispatcher._strike_merged_tasks(_wf(repo), source, [alpha]) == 1

    # Struck alpha AND kept the orchestrator's new item. No conflict, no clobber.
    assert (repo / "TODO.md").read_text() == "- [ ] brand new item\n- [x] alpha\n- [ ] beta\n"


def test_strike_skips_when_the_checkout_is_not_on_the_base_branch(repo):
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "some-feature"], check=True)
    source = _source(repo)
    alpha = next(t.id for t in source.list_open_tasks() if t.title == "alpha")

    assert dispatcher._strike_merged_tasks(_wf(repo), source, [alpha]) == 0
    assert (repo / "TODO.md").read_text() == "- [ ] alpha\n- [ ] beta\n"  # untouched


def test_strike_skips_when_a_human_has_the_tracker_dirty(repo):
    (repo / "TODO.md").write_text("- [ ] alpha\n- [ ] beta\n- [ ] a human is mid-edit\n")
    source = _source(repo)
    alpha = next(t.id for t in source.list_open_tasks() if t.title == "alpha")

    assert dispatcher._strike_merged_tasks(_wf(repo), source, [alpha]) == 0
    # The human's uncommitted work is still there, unstaged and uncommitted.
    assert "a human is mid-edit" in (repo / "TODO.md").read_text()
    assert "- [ ] alpha" in (repo / "TODO.md").read_text()


def test_strike_skips_when_the_base_branch_has_diverged(repo, tmp_path):
    # Local dev has a commit the remote doesn't, and the remote has one local
    # doesn't → ff-only fails. Leave it for a human; never force, never rebase.
    other = tmp_path / "other"
    subprocess.run(["git", "clone", str(tmp_path / "origin.git"), str(other)], check=True, capture_output=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        subprocess.run(["git", "-C", str(other), "config", k, v], check=True, capture_output=True)
    (other / "other.txt").write_text("remote side\n")
    subprocess.run(["git", "-C", str(other), "add", "other.txt"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(other), "commit", "-m", "remote"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(other), "push"], check=True, capture_output=True)

    (repo / "local.txt").write_text("local side\n")
    subprocess.run(["git", "-C", str(repo), "add", "local.txt"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "local"], check=True, capture_output=True)

    source = _source(repo)
    alpha = next(t.id for t in source.list_open_tasks() if t.title == "alpha")
    assert dispatcher._strike_merged_tasks(_wf(repo), source, [alpha]) == 0
    assert (repo / "TODO.md").read_text() == "- [ ] alpha\n- [ ] beta\n"
    assert _log(repo)[0] == "local"  # nothing committed on top


def test_strike_rolls_back_its_commit_when_the_push_is_rejected(repo):
    source = _source(repo)
    alpha = next(t.id for t in source.list_open_tasks() if t.title == "alpha")
    head_before = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()

    real_git = dispatcher._git

    def flaky_git(repo_path, *args, **kw):
        if args and args[0] == "push":
            return subprocess.CompletedProcess(args, 1, "", "rejected: non-fast-forward")
        return real_git(repo_path, *args, **kw)

    with patch.object(dispatcher, "_git", side_effect=flaky_git):
        assert dispatcher._strike_merged_tasks(_wf(repo), source, [alpha]) == 0

    # Rolled all the way back — no orphan commit, no half-written tracker — so
    # the next tick recomputes the pending strike and simply retries.
    head_after = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert head_after == head_before
    assert (repo / "TODO.md").read_text() == "- [ ] alpha\n- [ ] beta\n"
    status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True, check=True
    ).stdout
    assert status.strip() == ""


def test_strike_is_a_noop_for_a_source_that_cannot_close_tasks(repo):
    class GhIssuesLike:  # no close_tasks, no path — a merged PR closes the issue
        def list_open_tasks(self):
            return []

    assert dispatcher._strike_merged_tasks(_wf(repo), GhIssuesLike(), ["abc123"]) == 0
    assert (repo / "TODO.md").read_text() == "- [ ] alpha\n- [ ] beta\n"


def test_strike_logs_and_skips_a_task_that_is_gone_from_the_tracker(repo, caplog):
    source = _source(repo)
    before = len(_log(repo))

    with caplog.at_level("WARNING"):
        assert dispatcher._strike_merged_tasks(_wf(repo), source, ["deadbeef0000"]) == 0

    assert "not guessing" in caplog.text  # skipped AND logged, never guessed
    assert len(_log(repo)) == before  # nothing committed


# --- the tick: a merged PR is what finishes a run, and what strikes the line -

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


@pytest.fixture
def ticking(repo, tmp_path, monkeypatch):
    """A repo whose WORKFLOW.md drives a real tick(), with tmux/gh/spawn stubbed."""
    (repo / "WORKFLOW.md").write_text(WORKFLOW.format(root=tmp_path / "worktrees"))
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    monkeypatch.setattr(dispatcher, "_tmux_windows", lambda: set())
    monkeypatch.setattr(dispatcher, "_kill_window", lambda name: None)
    monkeypatch.setattr(dispatcher, "_fire_after_done", lambda wf: None)
    # No new dispatches: this is about finishing runs, not starting them.
    monkeypatch.setattr(dispatcher, "_spawn", lambda *a, **kw: False)
    return repo


def _seed_run(wf_path: Path, task_id: str, pr_state: str = "open") -> None:
    """A run that has opened its PR and is waiting on a human (`chela task-finished`)."""
    with dispatcher._db() as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, window_name, "
            "started_at, attempt, pr_url, pr_state) "
            "VALUES (?,?,?,'awaiting_review',?,?,?,?,?)",
            (task_id, str(wf_path), "t", "@9", dispatcher._now(), 1,
             "https://github.com/o/r/pull/1", pr_state),
        )
        conn.commit()


def _status(task_id: str) -> str:
    with dispatcher._db() as conn:
        return conn.execute(
            "SELECT status FROM runs WHERE task_id=?", (task_id,)
        ).fetchone()["status"]


def test_tick_finishes_a_run_when_its_PR_MERGES_and_strikes_the_line(ticking, monkeypatch):
    repo = ticking
    wf_path = repo / "WORKFLOW.md"
    alpha = next(t.id for t in _source(repo).list_open_tasks() if t.title == "alpha")
    _seed_run(wf_path, alpha)

    # The agent left the line ALONE (that is the whole point) — so "the line is
    # gone from dev" cannot be the done signal any more. The merge is.
    assert "- [ ] alpha" in (repo / "TODO.md").read_text()
    monkeypatch.setattr(dispatcher, "_read_pr_status", lambda url, d: ("merged", "MERGEABLE"))

    summary = dispatcher.tick(wf_path)

    assert _status(alpha) == "done"
    assert summary["reconciled_done"] == 1
    assert summary["tracker_struck"] == 1
    assert (repo / "TODO.md").read_text() == "- [x] alpha\n- [ ] beta\n"


def test_tick_leaves_an_open_PR_awaiting_review_and_does_not_strike(ticking, monkeypatch):
    repo = ticking
    wf_path = repo / "WORKFLOW.md"
    alpha = next(t.id for t in _source(repo).list_open_tasks() if t.title == "alpha")
    _seed_run(wf_path, alpha)
    monkeypatch.setattr(dispatcher, "_read_pr_status", lambda url, d: ("open", "MERGEABLE"))

    summary = dispatcher.tick(wf_path)

    assert _status(alpha) == "awaiting_review"
    assert summary["tracker_struck"] == 0
    assert (repo / "TODO.md").read_text() == "- [ ] alpha\n- [ ] beta\n"


def test_tick_retries_a_strike_that_could_not_land(ticking, monkeypatch):
    """The pending set is DERIVED from the runs table, never remembered — so a
    strike blocked by a dirty tracker is simply retried on the next tick."""
    repo = ticking
    wf_path = repo / "WORKFLOW.md"
    alpha = next(t.id for t in _source(repo).list_open_tasks() if t.title == "alpha")
    _seed_run(wf_path, alpha)
    monkeypatch.setattr(dispatcher, "_read_pr_status", lambda url, d: ("merged", "MERGEABLE"))

    # Tick 1: a human is mid-edit, so the strike must not land...
    (repo / "TODO.md").write_text("- [ ] alpha\n- [ ] beta\n- [ ] human edit\n")
    assert dispatcher.tick(wf_path)["tracker_struck"] == 0
    assert _status(alpha) == "done"  # the run is still finished — the PR merged

    # ...tick 2: the human committed; the strike is recomputed and lands.
    subprocess.run(["git", "-C", str(repo), "commit", "-am", "human"], check=True, capture_output=True)
    assert dispatcher.tick(wf_path)["tracker_struck"] == 1
    assert (repo / "TODO.md").read_text() == "- [x] alpha\n- [ ] beta\n- [ ] human edit\n"
