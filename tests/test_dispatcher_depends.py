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
from chela.sources import Task

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
    # LOUDLY (a WARNING naming the offending task — see test_dispatcher_depends.py's
    # unit-level tests below for the level/counterweight guards), rather than
    # dispatching prematurely.
    _seed(
        repo,
        '- [ ] follow-up task <!-- depends: "a title that was never typed correctly" -->\n',
    )

    with caplog.at_level(logging.WARNING, logger="chela.dispatcher"):
        summary = dispatcher.tick(repo / "WORKFLOW.md")

    assert summary["dispatched"] == 0
    assert spawns.titles == []
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "follow-up task" in warnings[0].getMessage()
    assert "held back" in warnings[0].getMessage()


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


# --- `_ready` unit-level: WARNING vs INFO, and the inert no-depends control --

def test_ready_warns_on_an_unresolved_dependency_reference(caplog):
    # 🔴 GUARD: an edge naming no task at all — open or closed — anywhere in the
    # tracker (a typo, a retitled/deleted blocker) is a TRACKER BUG, not an
    # ordinary wait. It must be loud: a WARNING naming the offending task, not
    # buried at debug where it never prints (chela/main.py's basicConfig is
    # INFO).
    blocked = Task(id="blocked", title="follow-up", file="", line_number=1, raw="", depends=("ghost",))

    with caplog.at_level(logging.WARNING, logger="chela.dispatcher"):
        ready = dispatcher._ready([blocked], set())

    assert ready == []
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "follow-up" in warnings[0].getMessage()
    assert "ghost" in warnings[0].getMessage()


def test_ready_does_not_warn_on_a_merely_unmet_but_resolvable_dependency(caplog):
    # The counterweight to the guard above: a dependency that names a REAL task
    # (open, just not yet struck) is the ordinary, expected wait — warning on
    # this too would make "warn on everything" pass the previous guard.
    prereq = Task(id="prereq", title="prerequisite", file="", line_number=1, raw="", depends=())
    follow = Task(id="follow", title="follow-up", file="", line_number=2, raw="", depends=("prereq",))

    with caplog.at_level(logging.DEBUG, logger="chela.dispatcher"):
        ready = dispatcher._ready([prereq, follow], set())

    assert ready == [prereq]
    assert not [r for r in caplog.records if r.levelname == "WARNING"]


def test_ready_is_silent_for_a_task_with_no_depends_marker(caplog):
    # Inert control: the overwhelming common case (no `depends:` at all) must
    # produce neither a warning nor a hold — only a corruption that touches the
    # unmet/unresolved-reference paths above should ever redden this sweep.
    plain = Task(id="plain", title="a plain task", file="", line_number=1, raw="", depends=())

    with caplog.at_level(logging.WARNING, logger="chela.dispatcher"):
        ready = dispatcher._ready([plain], set())

    assert ready == [plain]
    assert not caplog.records


# --- /api/dispatcher: open_tasks payload carries the blocked state ----------

def _find(tasks: list[dict], prefix: str) -> dict:
    return next(t for t in tasks if t["title"].startswith(prefix))


def test_open_tasks_payload_carries_blocked_state_and_unmet_depends(monkeypatch, tmp_path):
    # 🔴 GUARD: drop the "blocked"/"unmet_depends"/"unresolved_depends" fields
    # from api_dispatcher's open_tasks comprehension and this goes RED — a
    # permanently-blocked task (an unresolvable `depends:` reference) would
    # render in the Kanban Open column identically to a claimable one, with
    # nothing in the payload for the UI to tell them apart.
    from chela.dashboard import app as dash

    monkeypatch.setattr(dash, "_repo_root_workflow", lambda: None)
    repo = tmp_path / "proj"
    repo.mkdir()
    (repo / "WORKFLOW.md").write_text(
        "---\nproject_key: XYZ\ntracker:\n  kind: markdown\n  path: TODO.md\n---\nprompt\n"
    )
    (repo / "TODO.md").write_text(
        "## Open\n\n"
        "- [ ] plain task\n"
        '- [ ] waiting task <!-- depends: "prerequisite task" -->\n'
        "- [ ] prerequisite task\n"
        '- [ ] stuck task <!-- depends: "a title that was never typed correctly" -->\n'
    )
    wf_path = (repo / "WORKFLOW.md").resolve()
    monkeypatch.setattr(dash, "DISPATCH_WORKFLOWS", [wf_path])
    monkeypatch.setattr(dash.dispatcher, "list_runs", lambda: [])

    resp = dash.app.test_client().get("/api/dispatcher")
    tasks = resp.get_json()["workflows"][0]["open_tasks"]

    plain = _find(tasks, "plain task")
    assert plain["blocked"] is False
    assert plain["unmet_depends"] == []
    assert plain["unresolved_depends"] == []

    waiting = _find(tasks, "waiting task")
    assert waiting["blocked"] is True
    assert len(waiting["unmet_depends"]) == 1
    # Resolvable (a real "prerequisite task" bullet exists, just not struck yet)
    # — not a tracker bug, so no id should show up as unresolved.
    assert waiting["unresolved_depends"] == []

    stuck = _find(tasks, "stuck task")
    assert stuck["blocked"] is True
    assert stuck["unresolved_depends"] == stuck["unmet_depends"]
    assert stuck["unresolved_depends"] != []
