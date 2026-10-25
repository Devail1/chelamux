"""CMX-312: a run waiting in review on CI is not a judge that never started.

No judge round starts on any tier before CI is green, and a PROSE or STANDARD head never gets
one at all — the daemon stamps its no-judge verdict once CI is green. Seen 10-22 (twice): a
standard-tier PR waiting on a slow `test (3.13)` got "NO judge round has started … is the
queue held or the daemon down? Or review it yourself". Locked in here:

  * CI not green ⇒ the review-wait notice names CI (running / re-running a flake / red /
    unreadable), on every tier, and never asks about a judge on a tier that skips it;
  * a STANDARD/PROSE head is never "queued for the judge", and its stall names the no-judge
    verdict, not a judge round;
  * the stall clock restarts from the last tick CI was the wait;
  * the head's tier is read from its own files, an unreadable one never skips the judge.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from chela import inbox, judge
from tests.test_inbox_cmx86 import _isolated, _queued, _run  # noqa: F401 — autouse fixture

WF = "/repo/WORKFLOW.md"
T0 = 1_000_000.0
HEAD = "abc1234def"
JUDGE_WORDS = ("judge round has started", "Or review it yourself", "queued for the judge")


@pytest.fixture(autouse=True)
def _knobs(monkeypatch):
    monkeypatch.setenv("CHELA_JUDGE_NOT_STARTED_ALERT_S", "1200")
    monkeypatch.setenv("CHELA_JUDGE_MAX_CONCURRENT", "1")
    inbox._TIER_CACHE.clear()


def _waiting(**over):
    return _run("awaiting_review", **{"pr_head_sha": HEAD, "judge_state": "",
                                      "workflow_path": WF, **over})


def _judging(task="CMX-138"):
    return {"task_id": task, "status": "awaiting_review", "pr_state": "open",
            "pr_head_sha": "zzz", "judge_state": judge.J_RUNNING,
            "judge_started_at": "2026-10-16T10:05:00+00:00", "workflow_path": WF}


def _events(runs, store, now, tier=None):
    return inbox.judge_wait_events(runs, store, now, tiers={"T9": tier} if tier else None)


# --- 1. CI is the wait: the notice says CI ------------------------------------------------

@pytest.mark.parametrize("tier", ["standard", "prose"])
def test_a_no_judge_tier_waiting_on_ci_names_ci_never_a_judge(tier):
    store, runs = inbox._empty(), [_waiting(pr_checks="pending")]
    assert _events(runs, store, T0, tier) == []
    assert _events(runs, store, T0 + 1199, tier) == []
    fired = _events(runs, store, T0 + 1201, tier)
    assert [e["kind"] for e in fired] == ["run_judge_not_started"]
    text = fired[0]["summary"]
    assert "in review on CI — CI is still running on abc1234 — " in text
    assert f"{tier} tier, no judge round: it is merge-ready once CI is green" in text
    assert not any(w in text for w in JUDGE_WORDS), text
    assert "hold-status" not in text
    payload = fired[0]["payload"]
    assert (payload["waiting_on"], payload["risk_tier"], payload["pr_checks"]) == (
        "ci", tier, "pending")
    assert _events(runs, store, T0 + 9000, tier) == []           # said once


@pytest.mark.parametrize("tier", ["standard", "prose"])
def test_a_no_judge_tier_on_ci_is_never_queued_for_the_judge(tier):
    """Every judge slot is taken — it still says CI at the stall bound, not a queue at 3x."""
    store, runs = inbox._empty(), [_waiting(pr_checks="pending"), _judging()]
    _events(runs, store, T0, tier)
    fired = _events(runs, store, T0 + 1201, tier)
    assert len(fired) == 1
    assert fired[0]["payload"]["queued_behind"] == []
    assert "on CI" in fired[0]["summary"]
    assert _events(runs, store, T0 + 3601, tier) == []


@pytest.mark.parametrize("over, says", [
    ({"pr_checks": "pending"}, "CI is still running"),
    ({"pr_checks": "failing", "ci_rerun_sha": HEAD, "ci_rerun_state": "pending"},
     "CI is re-running a flake"),
    ({"pr_checks": "failing", "ci_rerun_sha": HEAD, "ci_rerun_state": "started"},
     "CI is re-running a flake"),
    ({"pr_checks": "unknown"}, "CI could not be read"),
])
def test_what_ci_is_doing_is_named(over, says):
    store, runs = inbox._empty(), [_waiting(**over)]
    _events(runs, store, T0, "standard")
    (event,) = _events(runs, store, T0 + 1201, "standard")
    assert f"— {says} on abc1234 — " in event["summary"]


@pytest.mark.parametrize("over", [
    {"pr_checks": "passing"}, {"pr_checks": "none"}, {},
    {"pr_checks": "failing", "ci_known_flake_sha": HEAD},       # the judge goes ahead on it
])
def test_ci_that_lets_a_judge_start_is_not_the_wait(over):
    store, runs = inbox._empty(), [_waiting(**over)]
    _events(runs, store, T0)
    (event,) = _events(runs, store, T0 + 1201)
    assert event["payload"]["waiting_on"] == "judge"
    assert "NO judge round has started" in event["summary"]


@pytest.mark.parametrize("over", [
    {"pr_checks": "failing"},
    {"pr_checks": "failing", "ci_known_flake_sha": "old"},      # a known flake of another head
    {"pr_checks": "failing", "ci_rerun_sha": "other", "ci_rerun_state": "pending"},
    {"pr_checks": "failing", "ci_rerun_sha": HEAD, "ci_rerun_state": "green"},
])
def test_a_red_ci_is_the_wait_but_left_to_its_own_ci_red_notice(over):
    """No review-wait notice of any kind — `run_ci_red` already named the job — and the stall
    clock stays held while it is red."""
    store, run = inbox._empty(), _waiting(**over)
    for t in (T0, T0 + 1201, T0 + 9000):
        assert _events([run], store, t, "core") == []
    green = _waiting(pr_checks="passing")
    assert _events([green], store, T0 + 9000 + 1199, "core") == []
    assert len(_events([green], store, T0 + 9000 + 1201, "core")) == 1


def test_the_ci_wait_is_measured_from_when_ci_became_the_wait():
    """Green (and judged-tier-queued) for 25m, then a CI re-run on the same head: that is a
    fresh CI wait, not one that has lasted 25m."""
    store = inbox._empty()
    green = [_waiting(pr_checks="passing"), _judging()]
    pending = [_waiting(pr_checks="pending"), _judging()]
    _events(green, store, T0, "core")
    assert _events(green, store, T0 + 1500, "core") == []         # queued, not yet said
    assert _events(pending, store, T0 + 1500, "core") == []
    assert _events(pending, store, T0 + 1500 + 1199, "core") == []
    (event,) = _events(pending, store, T0 + 1500 + 1201, "core")
    assert event["payload"]["waiting_on"] == "ci"
    assert "has waited 20m in review on CI" in event["summary"]


def test_a_ci_wait_that_ends_and_comes_back_is_measured_afresh():
    store = inbox._empty()
    pending, green = [_waiting(pr_checks="pending"), _judging()], [_waiting(), _judging()]
    _events(pending, store, T0, "core")
    _events(green, store, T0 + 1000, "core")
    assert _events(pending, store, T0 + 1100, "core") == []
    assert _events(pending, store, T0 + 1100 + 1199, "core") == []
    assert len(_events(pending, store, T0 + 1100 + 1201, "core")) == 1


def test_a_judge_stall_already_said_is_not_followed_by_a_ci_notice():
    store = inbox._empty()
    green, pending = _waiting(pr_checks="passing"), _waiting(pr_checks="pending")
    _events([green], store, T0, "core")
    assert len(_events([green], store, T0 + 1201, "core")) == 1
    assert _events([pending], store, T0 + 1300, "core") == []
    assert _events([pending], store, T0 + 9000, "core") == []


def test_the_no_judge_tiers_are_the_dispatchers():
    from chela import dispatcher
    assert set(inbox.NO_JUDGE_TIERS) == set(dispatcher._NO_JUDGE_VERDICTS) == {"prose", "standard"}


@pytest.mark.parametrize("tier, after", [
    ("core", "its judge round starts once CI is green"),
    ("human", "its judge round starts once CI is green"),
    (None, "nothing else is waited on until CI is green"),
])
def test_a_judged_or_unknown_tier_on_ci_says_ci_too(tier, after):
    store, runs = inbox._empty(), [_waiting(pr_checks="pending"), _judging()]
    _events(runs, store, T0, tier)
    (event,) = _events(runs, store, T0 + 1201, tier)
    assert after in event["summary"]
    assert "NO judge round" not in event["summary"]
    assert event["payload"]["queued_behind"] == []


# --- 2. after CI: the stall clock, and what a no-judge stall says ---------------------------

def test_the_stall_clock_restarts_when_ci_goes_green():
    store = inbox._empty()
    pending, green = _waiting(pr_checks="pending"), _waiting(pr_checks="passing")
    _events([pending], store, T0, "core")
    assert len(_events([pending], store, T0 + 1201, "core")) == 1      # the CI notice
    assert _events([pending], store, T0 + 1500, "core") == []          # last tick on CI
    assert _events([green], store, T0 + 1500 + 1199, "core") == []
    (event,) = _events([green], store, T0 + 1500 + 1201, "core")
    assert event["payload"]["waiting_on"] == "judge"
    assert _events([green], store, T0 + 99_999, "core") == []


@pytest.mark.parametrize("tier", ["standard", "prose"])
def test_a_no_judge_tier_green_with_no_verdict_names_the_verdict_not_a_judge(tier):
    store, runs = inbox._empty(), [_waiting(pr_checks="passing"), _judging()]
    _events(runs, store, T0, tier)
    (event,) = _events(runs, store, T0 + 1201, tier)
    text = event["summary"]
    assert "with CI green and no merge-ready verdict on its head" in text
    assert f"{tier} tier, no judge round: the daemon stamps its verdict" in text
    assert "chela dispatch --hold-status" in text
    assert not any(w in text for w in JUDGE_WORDS), text
    assert event["payload"]["waiting_on"] == "verdict"
    assert event["payload"]["queued_behind"] == []


def test_a_ci_notice_said_once_stays_said_across_an_aborted_round():
    store, run = inbox._empty(), _waiting(pr_checks="pending")
    _events([run], store, T0, "standard")
    assert len(_events([run], store, T0 + 1201, "standard")) == 1
    assert _events([{**run, "judge_state": judge.J_RUNNING}], store, T0 + 1300) == []
    assert _events([run], store, T0 + 1400, "standard") == []
    assert _events([run], store, T0 + 9000, "standard") == []


# --- 3. delivery re-check -----------------------------------------------------------------

@pytest.mark.parametrize("waiting_on, checks, stale", [
    ("ci", "passing", "CI is no longer the wait"),
    ("ci", "pending", None),
    ("judge", "passing", None),
    ("judge", "pending", "CI is the wait again (CI is still running)"),
    ("queue", "pending", "CI is the wait again (CI is still running)"),
    ("verdict", "unknown", "CI is the wait again (CI could not be read)"),
    ("verdict", "passing", None),
])
def test_a_queued_ci_notice_rots_when_ci_is_no_longer_the_wait(waiting_on, checks, stale):
    run = _waiting(pr_checks=checks)
    payload = {"waiting_on": waiting_on, "pr_head_sha": HEAD}
    assert inbox._cmx86_stale("run_judge_not_started", payload, run) == stale


# --- 4. the head's tier --------------------------------------------------------------------

def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
                           *args], check=True, capture_output=True, text=True).stdout.strip()


def _repo(tmp_path: Path, files: dict[str, str]) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "dev")
    (repo / "WORKFLOW.md").write_text("---\nproject_key: TEST\nworkspace:\n"
                                      "  base_branch: dev\n---\nbody\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "update-ref", "refs/remotes/origin/dev", "HEAD")
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "pr")
    return repo, _git(repo, "rev-parse", "HEAD")


@pytest.mark.parametrize("files, tier", [
    ({"chela/dashboard/frontend/src/x.ts": "x\n"}, "standard"),
    ({"docs/x.md": "x\n"}, "prose"),
    ({"chela/dispatcher.py": "x\n"}, "core"),
    ({"plugin/x.js": "x\n"}, "human"),
])
def test_review_tiers_reads_the_heads_own_files(tmp_path, files, tier):
    repo, sha = _repo(tmp_path, files)
    run = _waiting(pr_head_sha=sha, workflow_path=str(repo / "WORKFLOW.md"))
    assert inbox.review_tiers([run]) == {"T9": tier}


def test_review_tiers_caches_a_read_head_and_never_an_unread_one(tmp_path, monkeypatch):
    repo, sha = _repo(tmp_path, {"docs/x.md": "x\n"})
    wf = str(repo / "WORKFLOW.md")
    calls = []
    real = judge._changed_files
    monkeypatch.setattr(judge, "_changed_files",
                        lambda *a: calls.append(a) or real(*a))
    run = _waiting(pr_head_sha=sha, workflow_path=wf)
    assert inbox.review_tiers([run]) == inbox.review_tiers([run]) == {"T9": "prose"}
    assert len(calls) == 1
    missing = _waiting(pr_head_sha="f" * 40, workflow_path=wf)     # not in the repo
    assert inbox.review_tiers([missing]) == inbox.review_tiers([missing]) == {}
    assert len(calls) == 3


def test_review_tiers_rereads_a_head_when_its_workflow_changes(tmp_path):
    repo, sha = _repo(tmp_path, {"chela/feature.py": "x\n"})
    wf = repo / "WORKFLOW.md"
    run = _waiting(pr_head_sha=sha, workflow_path=str(wf))
    assert inbox.review_tiers([run]) == {"T9": "standard"}
    wf.write_text("---\nproject_key: TEST\nworkspace:\n  base_branch: dev\nmerge:\n"
                  "  core:\n    - chela/feature.py\n---\nbody\n")
    import os
    st = wf.stat()
    os.utime(wf, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    assert inbox.review_tiers([run]) == {"T9": "core"}


def test_review_tiers_survives_an_unreadable_workflow(tmp_path):
    run = _waiting(workflow_path=str(tmp_path / "nope" / "WORKFLOW.md"))
    assert inbox.review_tiers([run]) == {}


@pytest.mark.parametrize("over", [
    {"status": "running"}, {"pr_state": "merged"}, {"pr_state": "closed"},
    {"judge_state": judge.J_RUNNING}, {"pr_head_sha": None}, {"workflow_path": None},
])
def test_review_tiers_reads_only_runs_the_notice_can_speak_about(tmp_path, monkeypatch, over):
    repo, sha = _repo(tmp_path, {"docs/x.md": "x\n"})
    run = {**_waiting(pr_head_sha=sha, workflow_path=str(repo / "WORKFLOW.md")), **over}
    monkeypatch.setattr(judge, "_changed_files", lambda *a: pytest.fail("read a tier"))
    assert inbox.review_tiers([run]) == {}


def test_tick_hands_the_tier_to_the_review_wait_notice(monkeypatch):
    monkeypatch.setenv("CHELA_JUDGE_NOT_STARTED_ALERT_S", "0")
    monkeypatch.setattr(inbox, "review_tiers", lambda runs: {"T9": "standard"})
    inbox.tick({}, runs=[_waiting(pr_checks="passing")])
    (event,) = _queued("run_judge_not_started")
    assert event["payload"]["risk_tier"] == "standard"
    assert "no merge-ready verdict" in event["summary"]
