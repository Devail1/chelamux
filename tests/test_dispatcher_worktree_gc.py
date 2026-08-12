"""A run's worktree is freed the moment its row goes `done` (CMX-150).

Before this, nothing removed a finished run's worktree at all: `_prune_done_rows`
only ever dropped the DB row, so the checkout + `.venv`/`node_modules` sat on disk
until a human noticed and hand-pruned it (51 orphaned worktrees / 2.6 GB, observed
2026-07-22). These tests pin the fix at the three `tick()` sites that transition a
row to `done` — the worktree directory must be gone right after, without deleting
the branch (task_number collision avoidance still needs it, see
`_max_existing_task_number`).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from chela import config, dispatcher, worktree
from chela.sources.markdown import MarkdownSource
from chela.workflow import WorkflowDef

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


@pytest.fixture
def ticking(repo, tmp_path, monkeypatch):
    """A repo whose WORKFLOW.md drives a real tick(), with tmux/gh/spawn stubbed."""
    (repo / "WORKFLOW.md").write_text(WORKFLOW.format(root=tmp_path / ".chela" / "worktrees"))
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    monkeypatch.setattr(dispatcher, "_tmux_windows", lambda: set())
    monkeypatch.setattr(dispatcher, "_kill_window", lambda name: None)
    monkeypatch.setattr(dispatcher, "_fire_after_done", lambda wf: None)
    monkeypatch.setattr(dispatcher, "_spawn", lambda *a, **kw: False)
    return repo


def _source(repo: Path) -> MarkdownSource:
    wf = WorkflowDef(
        path=repo / "WORKFLOW.md",
        config={
            "tracker": {"kind": "markdown", "path": "TODO.md"},
            "workspace": {"root": str(config.CHELA_DIR / "worktrees"), "base_branch": "dev"},
        },
        prompt_template="",
    )
    return MarkdownSource(wf)


def _branches(repo_path: Path) -> set[str]:
    out = subprocess.run(
        ["git", "-C", str(repo_path), "branch", "--format=%(refname:short)"],
        check=True, capture_output=True, text=True,
    ).stdout
    return {line.strip() for line in out.splitlines() if line.strip()}


def _seed_run_with_worktree(repo: Path, wf_path: Path, task_id: str, worktrees_root: Path) -> Path:
    """An `awaiting_review` run whose branch has a REAL, live worktree attached."""
    wt_path, _ = worktree.ensure_worktree(repo, task_id, "dev", "CMX", 1, worktrees_root)
    with dispatcher._db() as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, window_name, "
            "worktree_path, branch_name, started_at, attempt, pr_url, pr_state) "
            "VALUES (?,?,?,'awaiting_review',?,?,?,?,?,?,?)",
            (task_id, str(wf_path), "t", "@9", str(wt_path), "cmx-1",
             dispatcher._now(), 1, "https://github.com/o/r/pull/1", "open"),
        )
        conn.commit()
    return wt_path


def test_tick_removes_the_worktree_when_a_PR_merges(ticking, monkeypatch):
    repo = ticking
    wf_path = repo / "WORKFLOW.md"
    alpha = next(t.id for t in _source(repo).list_open_tasks() if t.title == "alpha")
    worktrees_root = repo.parent / ".chela" / "worktrees"
    wt_path = _seed_run_with_worktree(repo, wf_path, alpha, worktrees_root)
    assert wt_path.is_dir()
    monkeypatch.setattr(dispatcher, "_read_pr_status", lambda url, d: ("merged", "MERGEABLE"))

    summary = dispatcher.tick(wf_path)

    assert summary["reconciled_done"] == 1
    assert not wt_path.exists()  # disk freed immediately, not on some later prune
    assert "cmx-1" in _branches(repo)  # branch left alone (task_number collision guard)


def test_tick_leaves_an_awaiting_review_worktree_alone(ticking, monkeypatch):
    """⛔ A run still `awaiting_review` is NOT terminal — a rework may re-spawn INTO this
    same worktree. Remove it unconditionally here and this goes RED."""
    repo = ticking
    wf_path = repo / "WORKFLOW.md"
    alpha = next(t.id for t in _source(repo).list_open_tasks() if t.title == "alpha")
    worktrees_root = repo.parent / ".chela" / "worktrees"
    wt_path = _seed_run_with_worktree(repo, wf_path, alpha, worktrees_root)
    assert wt_path.is_dir()
    monkeypatch.setattr(dispatcher, "_read_pr_status", lambda url, d: ("open", "MERGEABLE"))

    summary = dispatcher.tick(wf_path)

    assert summary["reconciled_done"] == 0
    assert wt_path.is_dir()          # still needed — a rework may re-spawn into it


def test_tick_reconciles_a_closed_PR_to_done_and_frees_the_worktree(ticking, monkeypatch):
    """CMX-265: a PR a human closed WITHOUT merging must not park its row in the
    Review lane forever — `pr_state='closed'` is just as terminal as `'merged'`, and
    only the merged branch used to reconcile out of REVIEW_STATUSES. Unhandled, this
    was 7 ghost rows sitting in Review with a dead PR and nothing to do about it."""
    repo = ticking
    wf_path = repo / "WORKFLOW.md"
    alpha = next(t.id for t in _source(repo).list_open_tasks() if t.title == "alpha")
    worktrees_root = repo.parent / ".chela" / "worktrees"
    wt_path = _seed_run_with_worktree(repo, wf_path, alpha, worktrees_root)
    assert wt_path.is_dir()
    monkeypatch.setattr(dispatcher, "_read_pr_status", lambda url, d: ("closed", "UNKNOWN"))

    summary = dispatcher.tick(wf_path)

    assert summary["reconciled_done"] == 1
    assert not wt_path.exists()  # disk freed immediately, same as the merged path
    with dispatcher._db() as conn:
        row = conn.execute("SELECT status FROM runs WHERE task_id=?", (alpha,)).fetchone()
    assert row["status"] == "done"  # off the board's REVIEW_STATUSES list — no longer a ghost


def test_tick_does_not_fire_after_done_for_a_closed_unmerged_PR(ticking, monkeypatch):
    """The merged-PR path fires `hooks.after_done` — a "shipped" signal a repo may wire
    to a deploy. A closed-without-merging PR is a rejected trial, not shipped work, so
    reconciling it to `done` must NOT trip that hook."""
    repo = ticking
    wf_path = repo / "WORKFLOW.md"
    alpha = next(t.id for t in _source(repo).list_open_tasks() if t.title == "alpha")
    worktrees_root = repo.parent / ".chela" / "worktrees"
    _seed_run_with_worktree(repo, wf_path, alpha, worktrees_root)
    monkeypatch.setattr(dispatcher, "_read_pr_status", lambda url, d: ("closed", "UNKNOWN"))
    fired = []
    monkeypatch.setattr(dispatcher, "_fire_after_done", lambda wf: fired.append(wf))

    summary = dispatcher.tick(wf_path)

    assert summary["reconciled_done"] == 1
    assert fired == []  # no after_done — nothing shipped


def test_tick_removes_the_worktree_when_the_tracker_line_is_struck_by_hand(ticking):
    """`row["task_id"] not in open_ids and status in REVIEW_STATUSES` → done: the other
    `tick()` path that reaches `done` without a fresh `pr_state` read this tick."""
    repo = ticking
    wf_path = repo / "WORKFLOW.md"
    alpha = next(t.id for t in _source(repo).list_open_tasks() if t.title == "alpha")
    worktrees_root = repo.parent / ".chela" / "worktrees"
    wt_path = _seed_run_with_worktree(repo, wf_path, alpha, worktrees_root)
    assert wt_path.is_dir()

    # A human struck the line by hand — task_id leaves the tracker's open set.
    subprocess.run(["git", "-C", str(repo), "checkout", "dev"], check=True, capture_output=True)
    (repo / "TODO.md").write_text("- [x] alpha\n- [ ] beta\n")
    subprocess.run(["git", "-C", str(repo), "commit", "-am", "human strike"], check=True, capture_output=True)

    summary = dispatcher.tick(wf_path)

    assert summary["reconciled_done"] == 1
    assert not wt_path.exists()
