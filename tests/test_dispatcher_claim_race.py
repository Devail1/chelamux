"""The dispatcher must not win every race for the queue.

The failure, twice in one day: a PR merges → reconciliation frees the only slot → the
orchestrator starts *writing* the next (higher-priority) task, which takes minutes because
it is reviewing what just landed → the tick fires long before that and claims whatever was
top of the OLD queue, occupying the only slot for a 20–40 minute agent run.

Two mechanisms, tested here at the tick level:

* **fetch-then-claim** — the queue is re-read from ``origin/<base_branch>`` at the instant
  of claiming, so an edit pushed *while this tick was busy* is still honoured. Necessary,
  and NOT sufficient: it cannot read an edit that has not been written yet.
* **the queue hold** — the actual fix. Claims stop; reconciliation does not.

Plus the landmine the task warned about: a REORDER must not re-key an in-flight run.
"""
from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from chela import config, dispatcher, hold
from chela.sources.markdown import MarkdownSource
from chela.workflow import WorkflowDef, load_workflow

# `workspace.root` is under the test's own CHELA_DIR on purpose: a workflow that omits it
# defaults to ~/.chela/worktrees/default — the REAL install — and the workspace fence
# (tests/test_workspace_fence.py) then refuses to tick at all. Tests own their worktrees.
WORKFLOW = """---
project_key: TST
tracker:
  kind: markdown
  path: TODO.md
workspace:
  root: {root}
  base_branch: dev
concurrency:
  max: 1
---
do {{{{task_title}}}}
"""

FIRST = "- [ ] first item\n"
SECOND = "- [ ] second item\n"
URGENT = "- [ ] URGENT item\n"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A git repo on `dev` with a tracker, a WORKFLOW.md and an `origin` to push to —
    and an isolated CHELA_DIR, so neither the runs DB nor the hold is the developer's."""
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(config, "CHELA_DIR", state)             # the hold file
    monkeypatch.setattr(dispatcher, "CHELA_DIR", state)
    monkeypatch.setattr(dispatcher, "DB_PATH", state / "scheduler.db")

    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "dev", str(origin)], check=True, capture_output=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", str(origin), str(work)], check=True, capture_output=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        subprocess.run(["git", "-C", str(work), "config", k, v], check=True, capture_output=True)
    (work / "WORKFLOW.md").write_text(WORKFLOW.format(root=state / "worktrees"))
    (work / "TODO.md").write_text(FIRST + SECOND)
    subprocess.run(["git", "-C", str(work), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "commit", "-m", "seed"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "push", "-u", "origin", "dev"], check=True, capture_output=True)
    return work


def _source(repo: Path) -> MarkdownSource:
    return MarkdownSource(load_workflow(repo / "WORKFLOW.md"))


def _id(repo: Path, title: str) -> str:
    return next(t.id for t in _source(repo).list_open_tasks() if t.title == title)


def _origin_show(repo: Path, tmp_path: Path, rel: str = "TODO.md", ref: str = "dev") -> str:
    """The tracker strike lands through the isolated base-write worktree (CMX-174), never
    `repo`'s own working tree — so this reads what actually landed on `origin`."""
    out = subprocess.run(
        ["git", "--git-dir", str(tmp_path / "origin.git"), "show", f"{ref}:{rel}"],
        capture_output=True, text=True, check=True,
    )
    return out.stdout


def _push_tracker(repo: Path, tmp_path: Path, text: str) -> None:
    """The orchestrator, in ITS checkout, rewriting the queue and pushing to origin —
    a different clone, exactly as it is live (the daemon's repo never sees the edit until
    it fetches)."""
    other = tmp_path / "orchestrator"
    if not other.exists():
        subprocess.run(["git", "clone", str(tmp_path / "origin.git"), str(other)],
                       check=True, capture_output=True)
        for k, v in (("user.email", "o@example.com"), ("user.name", "O"), ("commit.gpgsign", "false")):
            subprocess.run(["git", "-C", str(other), "config", k, v], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(other), "pull", "-q"], check=True, capture_output=True)
    (other / "TODO.md").write_text(text)
    subprocess.run(["git", "-C", str(other), "commit", "-qam", "reorder the queue"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(other), "push", "-q"], check=True, capture_output=True)


class _Spawns:
    """Stands in for _spawn: records what the tick CLAIMED, in order, and claims the row
    the way the real one does (so concurrency.max still bites)."""

    def __init__(self):
        self.titles: list[str] = []

    def __call__(self, wf: WorkflowDef, task, attempt, conn):
        self.titles.append(task.title)
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, attempt, started_at) "
            "VALUES (?, ?, ?, 'running', ?, ?)",
            (task.id, str(wf.path), task.title, attempt, dispatcher._now()),
        )
        conn.commit()
        return True


@pytest.fixture
def spawns():
    s = _Spawns()
    with patch.object(dispatcher, "_spawn", side_effect=s):
        yield s


# --- fetch-then-claim -------------------------------------------------------


def test_a_tracker_edit_pushed_after_the_tick_began_is_honoured_at_claim_time(
    repo, tmp_path, spawns
):
    # The tick parses the tracker up front, then spends real time on the network (PR
    # polling, the tracker strike). The queue it CLAIMS from must be the one that exists
    # at the moment of claiming — so a push that lands mid-tick still wins.
    real_list = MarkdownSource.list_open_tasks

    def list_then_push(self):
        tasks = real_list(self)
        _push_tracker(repo, tmp_path, URGENT + FIRST + SECOND)   # ...while we were busy
        return tasks

    with patch.object(MarkdownSource, "list_open_tasks", list_then_push):
        summary = dispatcher.tick(repo / "WORKFLOW.md")

    assert summary["dispatched"] == 1
    assert spawns.titles == ["URGENT item"]   # not "first item", which was top at parse


def test_an_item_that_is_only_on_disk_is_still_dispatched_but_ranks_below_the_pushed_queue(
    repo, spawns
):
    # A local-only checkout (or an orchestrator mid-write) must not become undispatchable
    # just because origin has not seen the item yet — it queues after what WAS pushed.
    (repo / "TODO.md").write_text("- [ ] never pushed\n" + FIRST + SECOND)

    dispatcher.tick(repo / "WORKFLOW.md")

    assert spawns.titles == ["first item"]


def test_a_task_struck_on_origin_is_not_re_dispatched_from_a_stale_checkout(
    repo, tmp_path, spawns
):
    # origin says `first item` is done; this checkout has not pulled that yet. Claiming
    # from origin must honour origin's strike, not resurrect the task from local disk.
    _push_tracker(repo, tmp_path, "- [x] first item\n" + SECOND)

    dispatcher.tick(repo / "WORKFLOW.md")

    assert spawns.titles == ["second item"]


def test_no_remote_falls_back_to_the_on_disk_queue(tmp_path, monkeypatch, spawns):
    # A dispatcher that refuses to work offline is a worse bug than the race it fixes.
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(config, "CHELA_DIR", state)
    monkeypatch.setattr(dispatcher, "CHELA_DIR", state)
    monkeypatch.setattr(dispatcher, "DB_PATH", state / "scheduler.db")
    solo = tmp_path / "solo"
    solo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "dev", str(solo)], check=True)
    (solo / "WORKFLOW.md").write_text(WORKFLOW.format(root=state / "worktrees"))
    (solo / "TODO.md").write_text(FIRST + SECOND)

    dispatcher.tick(solo / "WORKFLOW.md")

    assert spawns.titles == ["first item"]


# --- the queue hold ---------------------------------------------------------


def test_a_hold_blocks_the_claim(repo, spawns):
    hold.take(reason="rewriting the queue", ttl_seconds=600, by="@0")

    summary = dispatcher.tick(repo / "WORKFLOW.md")

    assert summary["held"] is True
    assert summary["dispatched"] == 0
    assert spawns.titles == []               # nothing claimed, which is the entire point
    assert "rewriting the queue" in summary["hold"]["summary"]


def test_releasing_the_hold_claims_the_NEW_top_item(repo, tmp_path, spawns):
    # The live sequence that failed twice, end to end: hold → the slot frees → the queue
    # is rewritten and pushed → release → the next tick claims the NEW top item.
    hold.take(reason="reprioritising", ttl_seconds=600)
    assert dispatcher.tick(repo / "WORKFLOW.md")["dispatched"] == 0

    _push_tracker(repo, tmp_path, URGENT + FIRST + SECOND)
    assert dispatcher.tick(repo / "WORKFLOW.md")["dispatched"] == 0   # still held

    hold.release()
    summary = dispatcher.tick(repo / "WORKFLOW.md")

    assert summary["dispatched"] == 1
    assert spawns.titles == ["URGENT item"]


def test_a_hold_does_NOT_stop_reconciliation(repo, spawns, tmp_path):
    # CMX-53's lesson: dispatch and reconcile ride the same tick and went dark together.
    # A hold that also froze reconciliation would jam the very slot the orchestrator is
    # holding the queue to fill — the merged PR would never close out.
    first = _id(repo, "first item")
    with dispatcher._db() as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, attempt, "
            "started_at, pr_url, pr_state) VALUES (?, ?, ?, 'awaiting_review', 1, ?, ?, 'merged')",
            (first, str((repo / "WORKFLOW.md").resolve()), "first item", dispatcher._now(),
             "https://example.invalid/pull/1"),
        )
    hold.take(reason="rewriting", ttl_seconds=600)

    with patch.object(dispatcher, "_read_pr_status", return_value=(None, None)):
        summary = dispatcher.tick(repo / "WORKFLOW.md")

    assert summary["held"] is True
    assert summary["dispatched"] == 0
    assert summary["reconciled_done"] == 1          # the run closed out and freed its slot
    assert summary["tracker_struck"] == 1           # and the dispatcher still struck it
    assert _origin_show(repo, tmp_path) == "- [x] first item\n" + SECOND


def test_an_expired_hold_resumes_dispatch_and_says_so_loudly(repo, spawns, caplog):
    # An orchestrator that crashes mid-rewrite must not park the fleet for eternity.
    hold.take(reason="crashed mid-rewrite", ttl_seconds=1)
    time.sleep(1.05)

    with caplog.at_level(logging.WARNING):
        summary = dispatcher.tick(repo / "WORKFLOW.md")

    assert summary["hold_expired"] is True
    assert summary["held"] is False
    assert spawns.titles == ["first item"]          # dispatch resumed
    assert "EXPIRED" in caplog.text                 # ...and nobody had to guess why
    assert hold.read() is None                      # self-released


def test_a_hold_survives_a_restart_of_the_process_that_honours_it(repo, spawns):
    # The daemon runs under PM2 and loads Python at start: a hold in module state dies
    # with a restart. This one is a file, so a brand-new tick still sees it.
    hold.take(reason="queue rewrite", ttl_seconds=600)

    assert dispatcher.tick(repo / "WORKFLOW.md")["held"] is True   # "restarted" tick
    assert spawns.titles == []


# --- the out-of-band-merge guard: a merged task is never re-claimed (CMX-140) ---


def test_a_merged_task_is_never_claimed_even_if_reconcile_missed_it(repo, spawns, monkeypatch):
    """Belt-and-suspenders claim guard. The reconcile pass (1.) already flips a
    `failed`+merged row to `done` before the claim loop runs, so to prove THIS
    guard (not that one) is what stops the re-claim, neuter the reconcile
    widening for this test only and confirm the claim loop still refuses a
    `pr_state='merged'` row on its own."""
    first = _id(repo, "first item")
    with dispatcher._db() as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, attempt, "
            "started_at, pr_url, pr_state) VALUES (?, ?, ?, 'failed', 1, ?, ?, 'merged')",
            (first, str((repo / "WORKFLOW.md").resolve()), "first item", dispatcher._now(),
             "https://example.invalid/pull/1"),
        )
    monkeypatch.setattr(dispatcher, "RECONCILE_MERGE_STATUSES", dispatcher.REVIEW_STATUSES)

    summary = dispatcher.tick(repo / "WORKFLOW.md")

    assert "first item" not in spawns.titles
    assert summary["dispatched"] == 1
    assert spawns.titles == ["second item"]


# --- the landmine: a reorder must not re-key an in-flight run ---------------


def test_moving_a_line_in_the_tracker_does_not_re_key_a_run(repo, tmp_path):
    # task_id = hash(tracker FILENAME, title) — position is not part of it. If a reorder
    # re-keyed a task, the in-flight run would be orphaned and the task re-dispatched into
    # a second window: worse than the bug being fixed.
    before = _id(repo, "second item")

    _push_tracker(repo, tmp_path, SECOND + URGENT + FIRST)
    subprocess.run(["git", "-C", str(repo), "pull", "-q"], check=True, capture_output=True)

    assert _id(repo, "second item") == before
