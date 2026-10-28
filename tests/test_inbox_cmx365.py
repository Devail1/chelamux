"""CMX-365 — the "self-check still working" notice follows the run's own budget.

10-28: every hand-in sent ``run_selfcheck_long`` ("running for 1h00m, ceiling 1h00m") because
the ceiling was a fixed 3600 s, while WORKFLOW.md budgets the self-check and the sweep at
2400 s each on top of a 2400 s suite timeout. Locked in here:

  * unset ``CHELA_SELFCHECK_ALERT_S`` ⇒ ceiling = 1.5 × (larger of
    ``judge.self_check_budget_seconds`` / ``judge.sweep_budget_seconds`` +
    ``judge.suite_timeout_seconds``), read from the run's OWN worktree WORKFLOW.md;
  * no readable WORKFLOW.md ⇒ 5400 s;
  * ``CHELA_SELFCHECK_ALERT_S`` set ⇒ it wins.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from chela import config, inbox

REPO = Path(__file__).resolve().parent.parent
NOW = 1_000_000.0


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CHELA_INBOX_FILE", str(tmp_path / "inbox.json"))
    monkeypatch.setenv("CHELA_EVENTS_FILE", str(tmp_path / "events.jsonl"))
    monkeypatch.delenv("CHELA_SELFCHECK_ALERT_S", raising=False)
    monkeypatch.setenv("CHELA_STUCK_AGENT_ALERT_S", "1200")
    monkeypatch.setattr(inbox.transcripts, "transcript_for_cwd", lambda cwd: None)
    monkeypatch.setattr(inbox, "_git_last_commit", lambda w: None)
    monkeypatch.setattr(inbox, "_selfcheck_scan", lambda: [])


def _workflow(wt: Path, judge: str) -> Path:
    wt.mkdir(exist_ok=True)
    (wt / "WORKFLOW.md").write_text(f"---\nproject_key: CMX\njudge:\n{judge}---\nbody\n")
    return wt


def _budgets(wt: Path, *, check=2400, sweep=2400, suite=2400) -> Path:
    return _workflow(wt, f"  self_check_budget_seconds: {check}\n"
                         f"  sweep_budget_seconds: {sweep}\n"
                         f"  suite_timeout_seconds: {suite}\n")


def _run(wt: Path) -> dict:
    return {"task_id": "T9", "title": "fix the parser", "status": "running",
            "branch_name": "cmx-9", "pr_url": "https://github.com/o/r/pull/9",
            "pr_state": "open", "worktree_path": str(wt)}


def _tick(monkeypatch, wt: Path, running_s: float, store=None, now=NOW) -> list[dict]:
    """One real probe of ``wt`` (self-check started ``running_s`` ago) fed to stuck_events."""
    sc = {"what": "task-finished self-check", "pid": 4242, "since": NOW - running_s}
    monkeypatch.setattr(inbox, "_selfcheck_for", lambda w, procs: sc)
    run = _run(wt)
    probes = inbox.stuck_probes([run])
    return inbox.stuck_events([run], store if store is not None else inbox._empty(),
                              probes, now)


# --- 1. the derived ceiling -----------------------------------------------------------------

def test_a_65_minute_self_check_under_a_2400s_budget_sends_no_notice(tmp_path, monkeypatch):
    """🔴 GUARD (negative control): the old fixed 3600 s fires here; the derived 7200 does not."""
    wt = _budgets(tmp_path / "wt")
    assert _tick(monkeypatch, wt, 65 * 60) == []


def test_the_repo_s_own_workflow_lets_a_65_minute_self_check_run_quietly(tmp_path, monkeypatch):
    wt = tmp_path / "wt"
    wt.mkdir()
    shutil.copy(REPO / "WORKFLOW.md", wt / "WORKFLOW.md")
    assert _tick(monkeypatch, wt, 65 * 60) == []


def test_past_the_derived_ceiling_it_is_exactly_one_notice(tmp_path, monkeypatch):
    """🔴 GUARD: (2400 + 2400) × 1.5 = 7200 s — one second short is silent, at it is ONE
    notice naming a 2h ceiling, and the next tick of the same self-check adds none."""
    wt = _budgets(tmp_path / "wt")
    assert _tick(monkeypatch, wt, 7199) == []
    store = inbox._empty()
    [event] = _tick(monkeypatch, wt, 7200, store)
    assert event["kind"] == "run_selfcheck_long"
    assert "ceiling 2h00m" in event["summary"]
    assert _tick(monkeypatch, wt, 7200 + 600, store, now=NOW + 600) == []


@pytest.mark.parametrize("check,sweep,suite,budget", [
    (2400, 2400, 2400, 4800.0),
    (2400, 900, 600, 3000.0),       # the self-check budget is the larger
    (900, 3000, 600, 3600.0),       # the sweep budget is the larger
])
def test_the_budget_is_the_larger_of_self_check_and_sweep_plus_the_suite(
        tmp_path, check, sweep, suite, budget):
    assert inbox.selfcheck_budget_s(_budgets(tmp_path / "wt", check=check, sweep=sweep,
                                             suite=suite)) == budget


def test_unset_budgets_take_the_judge_s_defaults(tmp_path):
    """900 s self-check/sweep default + 900 s suite-timeout default."""
    assert inbox.selfcheck_budget_s(_workflow(tmp_path / "wt", "  test_cmd: pytest\n")) == 1800.0


# --- 2. unknown budget: the 5400 s fallback, never a guess -----------------------------------

@pytest.mark.parametrize("make", ["missing", "unparseable"])
def test_an_unreadable_workflow_is_unknown_and_falls_back_to_90_minutes(
        tmp_path, monkeypatch, make):
    wt = tmp_path / "wt"
    wt.mkdir()
    if make == "unparseable":
        (wt / "WORKFLOW.md").write_text("---\njudge: [unterminated\n")
    assert inbox.selfcheck_budget_s(wt) is None
    assert _tick(monkeypatch, wt, 5399) == []
    [event] = _tick(monkeypatch, wt, 5400)
    assert "ceiling 1h30m" in event["summary"]


def test_no_self_check_running_reads_no_workflow(tmp_path, monkeypatch):
    wt = _budgets(tmp_path / "wt")
    monkeypatch.setattr(inbox, "_selfcheck_for", lambda w, procs: None)
    assert inbox.stuck_probes([_run(wt)])["T9"]["selfcheck_budget"] is None


# --- 3. precedence: env > derived budget > fallback ------------------------------------------

@pytest.mark.parametrize("env,budget,want", [
    ("3600", 4800.0, 3600),         # the explicit override wins over the derived ceiling
    ("3600", None, 3600),           # ...and over the fallback
    (None, 4800.0, 7200),           # 1.5 × the run's budget
    (None, 1000.0, 1500),
    (None, None, 5400),             # unknown
    (None, 0.0, 5400),              # a non-positive budget is unknown too
    ("", 4800.0, 7200),             # an empty variable is unset
])
def test_selfcheck_alert_precedence(monkeypatch, env, budget, want):
    if env is None:
        monkeypatch.delenv("CHELA_SELFCHECK_ALERT_S", raising=False)
    else:
        monkeypatch.setenv("CHELA_SELFCHECK_ALERT_S", env)
    assert config.selfcheck_alert_s(budget) == want


def test_the_env_override_still_fires_at_one_hour_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("CHELA_SELFCHECK_ALERT_S", "3600")
    wt = _budgets(tmp_path / "wt")
    [event] = _tick(monkeypatch, wt, 65 * 60)
    assert "ceiling 1h00m" in event["summary"]
