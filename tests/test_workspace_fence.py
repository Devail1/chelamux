"""A daemon on a scratch ``CHELA_DIR`` may not dispatch into a workspace it does not own.

Real incident, 2026-07-14: ``pytest`` ran the production dispatcher against the production
tracker. ``tests/test_graceful_shutdown.py`` spawns the REAL ``chela run`` daemon with
``CHELA_DIR`` pointed at a ``tmp_path`` — but ``CHELA_DIR`` isolates chela's STATE, not its
WORKSPACE. The workspace comes from ``workspace.root`` in the workflow file, so the test
daemon loaded the real ``WORKFLOW.md``, read the real ``TODO.md``, and dispatched real open
tasks into the real ``~/.chela/worktrees``. It failed only because it collided with a live
run's worktree; on a clean box it would have spawned agents. It was misread as a shutdown
flake for hours.

Two mechanisms, and this file guards both, because one without the other is theatre:

* the suite cannot dispatch — ``conftest`` blanks ``CHELA_DISPATCH_WORKFLOWS``, so no test
  and no subprocess a test starts has a work queue at all;
* **and the landmine itself is disarmed** — a *process*, test or human, whose ``CHELA_DIR``
  is not the default REFUSES to dispatch a workflow whose workspace lives outside it.
  A test-only patch would leave the next `CHELA_DIR=/tmp/dbg chela run` armed.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from chela import config, dispatcher, workflow

WF = """---
project_key: TST
workspace:
  root: {root}
  base_branch: dev
tracker:
  kind: markdown
  path: {tracker}
concurrency:
  max: 1
agent:
  cmd: "echo agent"
---
do the thing: {{{{task_title}}}}
"""


def _workflow(dir: Path, root: Path) -> Path:
    tracker = dir / "TODO.md"
    tracker.write_text("# TODO\n\n- [ ] **A real open task nobody asked pytest to run.**\n")
    path = dir / "WORKFLOW.md"
    path.write_text(WF.format(root=root, tracker=tracker))
    return path


# --- the suite has no work queue ----------------------------------------------

def test_the_suite_cannot_dispatch_at_all():
    # conftest blanks it at import, so nothing pytest starts — including the real daemon
    # subprocess in test_graceful_shutdown.py, which passes os.environ straight through —
    # inherits the developer's WORKFLOW.md.
    assert os.environ["CHELA_DISPATCH_WORKFLOWS"] == ""
    assert config.DISPATCH_WORKFLOWS == []


# --- the fence -----------------------------------------------------------------

def test_a_scratch_chela_dir_refuses_a_workspace_outside_it(tmp_path):
    # Exactly the production shape: CHELA_DIR is scratch (conftest gives every test one),
    # workspace.root is the REAL install's worktree tree.
    wf = workflow.load_workflow(
        _workflow(tmp_path, workflow.default_chela_dir() / "worktrees" / "chelamux")
    )
    reason = workflow.workspace_escape(wf)
    assert reason is not None
    assert "OUTSIDE" in reason and str(config.CHELA_DIR) in reason


def test_the_default_chela_dir_is_not_fenced(tmp_path, monkeypatch):
    # The real install must be entirely unaffected — it owns ~/.chela/worktrees.
    monkeypatch.setattr(config, "CHELA_DIR", workflow.default_chela_dir())
    wf = workflow.load_workflow(
        _workflow(tmp_path, workflow.default_chela_dir() / "worktrees" / "chelamux")
    )
    assert workflow.workspace_escape(wf) is None


def test_a_workspace_inside_the_scratch_chela_dir_is_allowed(tmp_path):
    wf = workflow.load_workflow(_workflow(tmp_path, Path(config.CHELA_DIR) / "worktrees"))
    assert workflow.workspace_escape(wf) is None


@pytest.mark.parametrize("root", [
    "~/.chela/worktrees/default",                 # what a workflow with NO root falls back to
    "/tmp/somewhere-else",                        # anywhere at all outside CHELA_DIR
])
def test_every_root_outside_the_scratch_dir_is_refused(tmp_path, root):
    wf = workflow.load_workflow(_workflow(tmp_path, root))
    assert workflow.workspace_escape(wf) is not None


# --- and the dispatcher OBEYS it (the fence is worthless if tick ignores it) ----

def test_tick_refuses_and_does_nothing(tmp_path, monkeypatch, caplog):
    """⛔ The refusal must land BEFORE the tick touches anything.

    Not just before the spawn: a tick that got as far as reconciling would `git fetch`
    the real repo, refresh PRs, and STRIKE MERGED TASKS off the real tracker — with a runs
    DB that knows nothing, in a state dir that owns nothing.
    """
    wf_path = _workflow(tmp_path, workflow.default_chela_dir() / "worktrees" / "chelamux")

    # If the fence lets the tick through, these blow up rather than quietly doing the real
    # thing: the tick cannot read a task, cannot get a source, cannot spawn.
    def boom(*a, **kw):
        raise AssertionError("tick did REAL WORK under a scratch CHELA_DIR")

    monkeypatch.setattr(dispatcher, "get_source", boom)
    monkeypatch.setattr(dispatcher, "_spawn", boom)
    monkeypatch.setattr(dispatcher, "_db", boom)
    dispatcher._escaped.discard(str(wf_path))

    summary = dispatcher.tick(wf_path)

    assert summary["blocked"] is True
    assert summary["dispatched"] == 0
    # `refused`, not merely `blocked`: a BLOCKED (unparseable) workflow keeps reconciling
    # on its last-good config, and the daemon says so. A refused one does nothing at all —
    # and must not claim otherwise (chela/main.py branches on this).
    assert summary["refused"] is True
    assert "refusing to dispatch" in (summary["error"] or "")
    # Round 7 (PR #334): chela/main.py:317 and :1140 read these four keys UNCONDITIONALLY
    # off every tick's summary, refused or not — `_refused()`'s own docstring calls this
    # "same shape every caller already reads". Dropping any one of them (e.g.
    # `reconciled_closed`, which CMX-265 added) would KeyError the daemon loop on the very
    # next refused tick, not merely fail a comparison silently. Read through the real
    # dispatcher._escaped fence path above, not a hand-built dict.
    for key in ("dispatched", "reconciled_done", "reconciled_closed", "reconciled_failed"):
        assert key in summary, f"a refused tick's summary is missing {key!r} — main.py reads it unconditionally"
    # LOUD: this is the whole point. A refusal nobody can see is the silence that hid the
    # bug for hours.
    assert any(r.levelname == "ERROR" and "Dispatch REFUSED" in r.getMessage()
               for r in caplog.records), caplog.text


def test_tick_runs_normally_when_the_workspace_is_inside_chela_dir(tmp_path, monkeypatch):
    # The fence must not block a legitimately-confined daemon — otherwise "green" only ever
    # means "the fence refuses everything".
    wf_path = _workflow(tmp_path, Path(config.CHELA_DIR) / "worktrees")
    spawned: list[str] = []
    monkeypatch.setattr(dispatcher, "_spawn",
                        lambda wf, task, attempt, conn: spawned.append(task.id) or True)

    summary = dispatcher.tick(wf_path)

    assert summary["blocked"] is False
    assert summary["open"] == 1
    assert summary["dispatched"] == 1 and len(spawned) == 1
