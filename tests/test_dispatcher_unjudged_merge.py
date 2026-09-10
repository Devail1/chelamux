"""⚖️🕳️ CMX-358, issue #480: a run whose PR merges before a judge was ever SCHEDULED for it
used to reach `done` with `judge_state` still `''` — the same sentinel an un-judged-YET row
carries. "Not yet" and "never" read as the same thing. These tests pin the reconcile-to-done
branch that now disambiguates them (`judge.J_UNJUDGED_MERGED`), the guard against clobbering a
REAL verdict already on the row, and the changelog note riding along on that same path.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from chela import config, dispatcher, judge, worktree
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


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True,
    )


@pytest.fixture
def repo(tmp_path):
    """A real git repo on `dev` with a tracker and an `origin` it can push to."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "dev", str(origin)], check=True, capture_output=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", str(origin), str(work)], check=True, capture_output=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        _git("config", k, v, cwd=work)
    (work / "TODO.md").write_text("- [ ] alpha\n- [ ] beta\n")
    (work / "CHANGELOG.md").write_text("## [Unreleased]\n")
    _git("add", "TODO.md", "CHANGELOG.md", cwd=work)
    _git("commit", "-m", "seed", cwd=work)
    _git("push", "-u", "origin", "dev", cwd=work)
    return work


@pytest.fixture
def killed_windows():
    return []


@pytest.fixture
def ticking(repo, tmp_path, monkeypatch, killed_windows):
    """A repo whose WORKFLOW.md drives a real tick(), with tmux/gh/spawn stubbed."""
    (repo / "WORKFLOW.md").write_text(WORKFLOW.format(root=tmp_path / ".chela" / "worktrees"))
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    monkeypatch.setattr(dispatcher, "_tmux_windows", lambda: set())
    monkeypatch.setattr(dispatcher, "_kill_window", lambda name: killed_windows.append(name))
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


def _seed_run_with_worktree(
    repo: Path,
    wf_path: Path,
    task_id: str,
    worktrees_root: Path,
    *,
    task_number: int = 1,
    pr_url: str = "https://github.com/o/r/pull/1",
    judge_state: str = "",
    judge_detail: str = "",
) -> Path:
    wt_path, _ = worktree.ensure_worktree(repo, task_id, "dev", "CMX", task_number, worktrees_root)
    with dispatcher._db() as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, window_name, "
            "worktree_path, branch_name, started_at, attempt, pr_url, pr_state, "
            "judge_state, judge_detail) "
            "VALUES (?,?,?,'awaiting_review',?,?,?,?,?,?,?,?,?)",
            (task_id, str(wf_path), "t", f"@{8 + task_number}", str(wt_path), f"cmx-{task_number}",
             dispatcher._now(), 1, pr_url, "open", judge_state, judge_detail),
        )
        conn.commit()
    return wt_path


def _row(task_id: str) -> dict:
    with dispatcher._db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE task_id=?", (task_id,)).fetchone()
    return dict(row)


def test_merge_reconcile_stamps_unjudged_merged_when_never_judged(ticking, monkeypatch):
    """🔴 GUARD (accept case): an empty `judge_state` at merge time gets the new terminal
    value — this is the whole point of the ticket."""
    repo = ticking
    wf_path = repo / "WORKFLOW.md"
    alpha = next(t.id for t in _source(repo).list_open_tasks() if t.title == "alpha")
    worktrees_root = repo.parent / ".chela" / "worktrees"
    _seed_run_with_worktree(repo, wf_path, alpha, worktrees_root)
    monkeypatch.setattr(dispatcher, "_read_pr_status", lambda url, d: ("merged", "MERGEABLE"))

    summary = dispatcher.tick(wf_path)

    assert summary["reconciled_done"] == 1
    row = _row(alpha)
    assert row["status"] == "done"     # ⭐ GUARD: the transition still happens
    assert row["judge_state"] == judge.J_UNJUDGED_MERGED
    assert "no judge ever ran" in (row["judge_detail"] or "")


def test_merge_reconcile_does_not_overwrite_an_existing_judge_verdict(ticking, monkeypatch):
    """🔴 GUARD: the counterweight. A run that reached `done` with a REAL verdict already on
    the row must not have it stomped — corrupt by stamping `J_UNJUDGED_MERGED` unconditionally
    in the reconcile branch and every merged run (this one included) reads as unjudged."""
    repo = ticking
    wf_path = repo / "WORKFLOW.md"
    alpha = next(t.id for t in _source(repo).list_open_tasks() if t.title == "alpha")
    worktrees_root = repo.parent / ".chela" / "worktrees"
    _seed_run_with_worktree(
        repo, wf_path, alpha, worktrees_root,
        judge_state=judge.J_CLEAN, judge_detail="every guard held",
    )
    monkeypatch.setattr(dispatcher, "_read_pr_status", lambda url, d: ("merged", "MERGEABLE"))

    summary = dispatcher.tick(wf_path)

    assert summary["reconciled_done"] == 1
    row = _row(alpha)
    assert row["status"] == "done"
    assert row["judge_state"] == judge.J_CLEAN          # untouched
    assert row["judge_detail"] == "every guard held"     # untouched


def test_merge_reconcile_does_not_overwrite_a_running_judge(ticking, monkeypatch):
    """The CAS-refused-race shape (CMX-239): a judge still `running` when the merge lands is
    NOT "never scheduled" — it is mid-flight, and its own CAS in `request_changes` is what
    settles it (into `cannot_verify` or `blocked_race`). Stamping `unjudged_merged` over
    `running` here would race that mechanism and misreport a judge that DID get scheduled."""
    repo = ticking
    wf_path = repo / "WORKFLOW.md"
    alpha = next(t.id for t in _source(repo).list_open_tasks() if t.title == "alpha")
    worktrees_root = repo.parent / ".chela" / "worktrees"
    _seed_run_with_worktree(repo, wf_path, alpha, worktrees_root, judge_state=judge.J_RUNNING)
    monkeypatch.setattr(dispatcher, "_read_pr_status", lambda url, d: ("merged", "MERGEABLE"))
    # The judge watchdog (a SEPARATE mechanism) reaps any `running` row whose window is
    # gone — irrelevant to what this test pins, but this fixture's `_tmux_windows` stub
    # always reports no live windows, so it would otherwise fire and turn `running` into
    # `cannot_verify` before this test can observe the reconcile branch's own guard.
    monkeypatch.setattr(judge, "judge_lock_live", lambda *a, **kw: True)

    dispatcher.tick(wf_path)

    row = _row(alpha)
    assert row["status"] == "done"
    assert row["judge_state"] == judge.J_RUNNING


def test_merge_reconcile_changelog_note_fires_for_a_non_docs_diff_without_a_fragment(
    ticking, monkeypatch,
):
    """The changelog gate rode on the judge — this is its footing on the unjudged-merge path
    (issue #480's third bullet)."""
    repo = ticking
    wf_path = repo / "WORKFLOW.md"
    alpha = next(t.id for t in _source(repo).list_open_tasks() if t.title == "alpha")
    worktrees_root = repo.parent / ".chela" / "worktrees"
    wt_path = _seed_run_with_worktree(repo, wf_path, alpha, worktrees_root)
    (wt_path / "feature.py").write_text("def add(a, b):\n    return a + b\n")
    _git("add", "feature.py", cwd=wt_path)
    _git("commit", "-m", "add a feature, no changelog entry", cwd=wt_path)
    monkeypatch.setattr(dispatcher, "_read_pr_status", lambda url, d: ("merged", "MERGEABLE"))

    dispatcher.tick(wf_path)

    row = _row(alpha)
    assert row["judge_state"] == judge.J_UNJUDGED_MERGED
    detail = row["judge_detail"] or ""
    assert "also:" in detail
    assert "never touches CHANGELOG.md" in detail


def test_merge_reconcile_changelog_note_is_silent_for_a_docs_only_diff(ticking, monkeypatch):
    """🔴 GUARD: the changelog flag must not fire on a docs-only diff — reuses
    `judge._changelog_missing_note` (which itself reuses the same prose test as
    `judge._docs_only_diff`), never re-derives it. Corrupt by dropping that reuse (e.g.
    flagging on any non-empty diff) and a README-only merge gets flagged."""
    repo = ticking
    wf_path = repo / "WORKFLOW.md"
    alpha = next(t.id for t in _source(repo).list_open_tasks() if t.title == "alpha")
    worktrees_root = repo.parent / ".chela" / "worktrees"
    wt_path = _seed_run_with_worktree(repo, wf_path, alpha, worktrees_root)
    (wt_path / "README.md").write_text("# hello\n\nmore words.\n")
    _git("add", "README.md", cwd=wt_path)
    _git("commit", "-m", "docs only", cwd=wt_path)
    monkeypatch.setattr(dispatcher, "_read_pr_status", lambda url, d: ("merged", "MERGEABLE"))

    dispatcher.tick(wf_path)

    row = _row(alpha)
    assert row["judge_state"] == judge.J_UNJUDGED_MERGED
    assert "CHANGELOG" not in (row["judge_detail"] or "")


def test_merge_reconcile_changelog_note_is_silent_when_a_changelog_fragment_was_added(
    ticking, monkeypatch,
):
    repo = ticking
    wf_path = repo / "WORKFLOW.md"
    alpha = next(t.id for t in _source(repo).list_open_tasks() if t.title == "alpha")
    worktrees_root = repo.parent / ".chela" / "worktrees"
    wt_path = _seed_run_with_worktree(repo, wf_path, alpha, worktrees_root)
    (wt_path / "feature.py").write_text("def add(a, b):\n    return a + b\n")
    (wt_path / "changelog.d").mkdir(exist_ok=True)
    (wt_path / "changelog.d" / "CMX-358.md").write_text("### Added\n\n- A feature. (CMX-358)\n")
    _git("add", "feature.py", "changelog.d/CMX-358.md", cwd=wt_path)
    _git("commit", "-m", "add a feature with a changelog.d fragment", cwd=wt_path)
    monkeypatch.setattr(dispatcher, "_read_pr_status", lambda url, d: ("merged", "MERGEABLE"))

    dispatcher.tick(wf_path)

    row = _row(alpha)
    assert row["judge_state"] == judge.J_UNJUDGED_MERGED
    assert "CHANGELOG" not in (row["judge_detail"] or "")


def test_merge_reconcile_never_spawns_a_judge_for_the_merged_head(ticking, monkeypatch):
    """🔴 GUARD: no judge is spawned for a merged head — a `blocked_race` row must never be
    created this way. `_spawn_judge` is the only thing that can create one; assert it is
    never even called on a tick that reconciles this row to `done`."""
    repo = ticking
    wf_path = repo / "WORKFLOW.md"
    alpha = next(t.id for t in _source(repo).list_open_tasks() if t.title == "alpha")
    worktrees_root = repo.parent / ".chela" / "worktrees"
    _seed_run_with_worktree(repo, wf_path, alpha, worktrees_root)
    monkeypatch.setattr(dispatcher, "_read_pr_status", lambda url, d: ("merged", "MERGEABLE"))
    spawned = []
    monkeypatch.setattr(
        dispatcher, "_spawn_judge",
        lambda *a, **kw: spawned.append(1) or False,
    )

    dispatcher.tick(wf_path)

    assert spawned == []
    row = _row(alpha)
    assert row["judge_state"] == judge.J_UNJUDGED_MERGED
    assert row["judge_state"] != judge.J_BLOCKED_RACE
