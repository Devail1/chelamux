"""⚖️🔒 CMX-389 — the backstop for what the merge-gate hook cannot see (a human in a plain
terminal, a merge on github.com, a non-Claude process): the dispatcher's reconcile flags a
chela PR that MERGED without a clean judge on its merged head and without an approved
override — once — and the inbox announces it once.

Drives a REAL `dispatcher.tick()` against a real git repo, with tmux/gh/spawn stubbed
(the same harness as `test_dispatcher_unjudged_merge.py`).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from chela import config, dispatcher, event_log, inbox, judge, worktree
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
HEAD = "c" * 40


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


@pytest.fixture
def ticking(tmp_path, monkeypatch):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "dev", str(origin)], check=True,
                   capture_output=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", str(origin), str(work)], check=True, capture_output=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        _git("config", k, v, cwd=work)
    (work / "TODO.md").write_text("- [ ] alpha\n")
    _git("add", "TODO.md", cwd=work)
    _git("commit", "-m", "seed", cwd=work)
    _git("push", "-u", "origin", "dev", cwd=work)
    (work / "WORKFLOW.md").write_text(WORKFLOW.format(root=tmp_path / ".chela" / "worktrees"))
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    monkeypatch.setattr(dispatcher, "_tmux_windows", lambda: set())
    monkeypatch.setattr(dispatcher, "_kill_window", lambda name: None)
    monkeypatch.setattr(dispatcher, "_fire_after_done", lambda wf: None)
    monkeypatch.setattr(dispatcher, "_spawn", lambda *a, **kw: False)
    monkeypatch.setattr(dispatcher, "_read_pr_status", lambda url, d: ("merged", "MERGEABLE"))
    monkeypatch.setattr(dispatcher, "_read_pr_checks",
                        lambda url, d: dispatcher.CIStatus(dispatcher.CI_PASSING, head_sha=HEAD))
    return work


def _seed(repo: Path, *, judge_state: str, judge_sha: str | None) -> str:
    wf = WorkflowDef(path=repo / "WORKFLOW.md",
                     config={"tracker": {"kind": "markdown", "path": "TODO.md"},
                             "workspace": {"root": str(config.CHELA_DIR / "worktrees"),
                                           "base_branch": "dev"}},
                     prompt_template="")
    task_id = MarkdownSource(wf).list_open_tasks()[0].id
    wt, _ = worktree.ensure_worktree(repo, task_id, "dev", "CMX", 1,
                                     repo.parent / ".chela" / "worktrees")
    with dispatcher._db() as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, window_name, "
            "worktree_path, branch_name, started_at, attempt, pr_url, pr_state, judge_state, "
            "judge_sha, pr_head_sha) VALUES (?,?,?,'awaiting_review',?,?,?,?,?,?,?,?,?,?)",
            (task_id, str(repo / "WORKFLOW.md"), "t", "@9", str(wt), "cmx-1",
             dispatcher._now(), 1, "https://github.com/o/r/pull/1", "open", judge_state,
             judge_sha, HEAD),
        )
        conn.commit()
    return task_id


def _row(task_id: str) -> dict:
    return dispatcher.resolve_run(task_id)


def _flags() -> list[dict]:
    return event_log.read(types=["orchestrator.merge_outside_gate"])["events"]


def test_reconcile_flags_an_out_of_gate_merge_exactly_once(ticking):
    """🔴 GUARD: a PR merged while the judge was BLOCKED, no override → flagged, once, even
    across further ticks. Corrupt `merged_outside_gate` to always-False → RED."""
    task_id = _seed(ticking, judge_state=judge.J_BLOCKED, judge_sha=HEAD)
    dispatcher.tick(ticking / "WORKFLOW.md")
    dispatcher.tick(ticking / "WORKFLOW.md")
    dispatcher.tick(ticking / "WORKFLOW.md")
    row = _row(task_id)
    assert row["status"] == "done"
    assert row["merged_outside_gate"] == 1
    flags = _flags()
    assert len(flags) == 1
    assert "merged outside chela's gate" in flags[0]["summary"]
    assert flags[0]["payload"]["task_id"] == task_id


def test_a_never_judged_merge_is_flagged_too(ticking):
    task_id = _seed(ticking, judge_state="", judge_sha=None)
    dispatcher.tick(ticking / "WORKFLOW.md")
    assert _row(task_id)["merged_outside_gate"] == 1
    assert _row(task_id)["judge_state"] == judge.J_UNJUDGED_MERGED     # CMX-358 still stamps


def test_a_clean_judge_on_a_STALE_head_is_flagged(ticking):
    """`judge_sha != pr_head_sha` — the judge verified an older commit than the one merged."""
    task_id = _seed(ticking, judge_state=judge.J_CLEAN, judge_sha="d" * 40)
    dispatcher.tick(ticking / "WORKFLOW.md")
    assert _row(task_id)["merged_outside_gate"] == 1


def test_a_merge_through_the_gate_is_not_flagged(ticking):
    """⭐🔴 GUARD (negative control): judge clean ON the merged head → no flag. Corrupt the
    detection to flag every merge → RED."""
    task_id = _seed(ticking, judge_state=judge.J_CLEAN, judge_sha=HEAD)
    dispatcher.tick(ticking / "WORKFLOW.md")
    assert _row(task_id)["status"] == "done"
    assert _row(task_id)["merged_outside_gate"] == 0
    assert _flags() == []


def test_an_approved_override_is_not_flagged(ticking):
    """⭐🔴 GUARD (negative control): judge blocked, but an operator-approved override for
    the merged head is on the review history → no flag. Corrupt the detection to ignore
    overrides → RED."""
    task_id = _seed(ticking, judge_state=judge.J_BLOCKED, judge_sha=HEAD)
    assert dispatcher.record_merge_override(task_id, {
        "request_id": "override-x", "approved_by": "dashboard", "head_sha": HEAD,
        "judge_state": judge.J_BLOCKED, "reason": "flaky judge", "actor": "human"})
    dispatcher.tick(ticking / "WORKFLOW.md")
    assert _row(task_id)["merged_outside_gate"] == 0
    assert _flags() == []


def test_an_override_for_a_DIFFERENT_head_does_not_cover_the_merge(ticking):
    task_id = _seed(ticking, judge_state=judge.J_BLOCKED, judge_sha=HEAD)
    dispatcher.record_merge_override(task_id, {"approved_by": "dashboard", "head_sha": "e" * 40,
                                               "reason": "r"})
    dispatcher.tick(ticking / "WORKFLOW.md")
    assert _row(task_id)["merged_outside_gate"] == 1


def test_an_override_entry_never_becomes_the_rework_verdict():
    """An override is an audit entry, not a review — the next rework prompt must still quote
    the real verdict (the same rule `retry` entries follow)."""
    import json
    run = {"review_history": json.dumps([
        {"verdict": "blocked", "body": "the real defect"},
        {"verdict": "override", "body": "merge override approved by dashboard: r"}])}
    assert dispatcher.latest_verdict(run) == "the real defect"


# --- the inbox announces it once ---------------------------------------------------------

def test_the_inbox_announces_an_out_of_gate_merge_once():
    run = {"task_id": "t1", "status": "done", "judge_state": judge.J_BLOCKED,
           "merged_outside_gate": 1, "branch_name": "cmx-9",
           "pr_url": "https://github.com/o/r/pull/9", "title": "t"}
    events, seen = inbox.run_events([run], {})
    assert [e["kind"] for e in events] == ["run_merged_outside_gate"]
    assert "cmx-9 was merged outside chela" in events[0]["summary"]
    again, _ = inbox.run_events([run], seen)
    assert again == []


def test_the_inbox_is_silent_for_a_gated_merge():
    run = {"task_id": "t1", "status": "done", "judge_state": judge.J_CLEAN,
           "merged_outside_gate": 0, "branch_name": "cmx-9", "title": "t"}
    events, _ = inbox.run_events([run], {})
    assert all(e["kind"] != "run_merged_outside_gate" for e in events)


def test_tick_registers_the_workflow_for_the_merge_gate(ticking):
    """🔴 GUARD: the PreToolUse gate decides from `$CHELA_DIR/mergegate.json` alone, so the
    dispatcher must keep it current. Delete the `mergegate.register` call from `tick` and the
    gate never learns this repo exists → RED."""
    from chela import mergegate
    dispatcher.tick(ticking / "WORKFLOW.md")
    entries = mergegate.load_registry({"CHELA_DIR": str(dispatcher.CHELA_DIR)}) or []
    mine = [e for e in entries if e["workflow"] == str(ticking / "WORKFLOW.md")]
    assert len(mine) == 1
    assert mine[0]["bases"] == ["dev"]
    assert mine[0]["repo_dir"] == str(ticking.resolve())
