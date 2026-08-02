"""Dependency edges for the markdown tracker (CMX-215).

Claim order today is purely POSITIONAL: `_claim_order` says "do this next", never
"this cannot start until that one merges." Every dispatched agent forks a fresh
worktree off `dev`, so a task that needs an unmerged sibling's code forks a `dev`
without it — a recorded scar, not a hypothetical: a same-day follow-up forked `dev`
before its predecessor merged and had to cherry-pick the dependency by hand.

A bullet can now declare `<!-- depends: "other task title" -->` on its own line.
`chela.dispatcher._ready` (the sole gate — see `_claim_order`) drops a task from the
claimable queue until every id it depends on has been struck `[x]` in the tracker.
Position is unaffected: an unmet dependency is skipped in place, not reordered, and
every OTHER task (with no unmet dependency of its own) claims normally around it.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from chela import config, dispatcher

WORKFLOW = """---
project_key: TST
tracker:
  kind: markdown
  path: TODO.md
workspace:
  root: {root}
  base_branch: dev
concurrency:
  max: 2
---
do {{{{task_title}}}}
"""


@pytest.fixture
def repo(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(config, "CHELA_DIR", state)
    monkeypatch.setattr(dispatcher, "CHELA_DIR", state)
    monkeypatch.setattr(dispatcher, "DB_PATH", state / "scheduler.db")

    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "dev", str(origin)], check=True, capture_output=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", str(origin), str(work)], check=True, capture_output=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        subprocess.run(["git", "-C", str(work), "config", k, v], check=True, capture_output=True)
    (work / "WORKFLOW.md").write_text(WORKFLOW.format(root=state / "worktrees"))
    return work


def _seed(repo: Path, text: str) -> None:
    (repo / "TODO.md").write_text(text)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "seed"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "push", "-qu", "origin", "dev"], check=True, capture_output=True)


class _Spawns:
    """Stands in for `_spawn`: records what got claimed, in order."""

    def __init__(self):
        self.titles: list[str] = []

    def __call__(self, wf, task, attempt, conn):
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


def test_a_task_with_an_unmet_dependency_is_skipped_even_though_it_ranks_first(repo, spawns):
    # "follow-up" ranks ABOVE "prerequisite" in the file — pure position would claim it
    # first. Its declared dependency is still open, so it must be held back; the
    # independent "prerequisite" claims normally.
    _seed(
        repo,
        '- [ ] follow-up task <!-- depends: "prerequisite task" -->\n'
        "- [ ] prerequisite task\n",
    )

    summary = dispatcher.tick(repo / "WORKFLOW.md")

    assert summary["dispatched"] == 1
    assert spawns.titles == ["prerequisite task"]


def test_dependency_satisfied_once_struck_done_unblocks_the_follow_up(repo, spawns):
    _seed(
        repo,
        "- [x] prerequisite task\n"
        '- [ ] follow-up task <!-- depends: "prerequisite task" -->\n',
    )

    summary = dispatcher.tick(repo / "WORKFLOW.md")

    assert summary["dispatched"] == 1
    assert len(spawns.titles) == 1
    assert spawns.titles[0].startswith("follow-up task")


def test_an_unknown_dependency_reference_fails_closed_rather_than_being_ignored(repo, spawns, caplog):
    # A typo'd or since-deleted dependency title must not silently be treated as
    # satisfied — that would defeat the whole feature on the first typo. It blocks,
    # loudly (a debug log names the unmet id), rather than dispatching prematurely.
    _seed(
        repo,
        '- [ ] follow-up task <!-- depends: "a title that was never typed correctly" -->\n',
    )

    with caplog.at_level(logging.DEBUG, logger="chela.dispatcher"):
        summary = dispatcher.tick(repo / "WORKFLOW.md")

    assert summary["dispatched"] == 0
    assert spawns.titles == []
    assert "held back" in caplog.text


def test_a_task_with_no_depends_marker_is_unaffected(repo, spawns):
    # Regression guard: plain bullets (the overwhelming common case) must claim exactly
    # as they did before this feature existed.
    _seed(repo, "- [ ] a perfectly ordinary task\n")

    summary = dispatcher.tick(repo / "WORKFLOW.md")

    assert summary["dispatched"] == 1
    assert spawns.titles == ["a perfectly ordinary task"]


def test_multiple_depends_all_must_be_satisfied(repo, spawns):
    # `;`-separated: BOTH must be struck done before the follow-up is claimable — one
    # done and one still open is still an unmet dependency.
    _seed(
        repo,
        "- [x] first prerequisite\n"
        "- [ ] second prerequisite\n"
        '- [ ] follow-up task <!-- depends: "first prerequisite"; "second prerequisite" -->\n',
    )

    summary = dispatcher.tick(repo / "WORKFLOW.md")

    assert summary["dispatched"] == 1
    assert spawns.titles == ["second prerequisite"]

    # Second prerequisite now merges too — both dependencies are struck. The
    # follow-up is claimable on the very next tick (one concurrency slot is still
    # free: max is 2, and only one run is active).
    _seed(
        repo,
        "- [x] first prerequisite\n"
        "- [x] second prerequisite\n"
        '- [ ] follow-up task <!-- depends: "first prerequisite"; "second prerequisite" -->\n',
    )

    dispatcher.tick(repo / "WORKFLOW.md")

    assert any(t.startswith("follow-up task") for t in spawns.titles)
