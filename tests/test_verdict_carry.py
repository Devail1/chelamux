"""⚖️🔀 CMX-289 — a clean verdict survives a head move that touches none of the PR's files.

Seen 2026-10-20: a PR clean on one head took a merge of ``origin/dev`` that changed none of
its files; the re-judge drew a fresh experiment sample, found a new survivor, spent the last
rework round and went to a human. These pin:

* the "touches no PR path" check on a REAL repo — base-only merge ⇒ empty; a PR file changed,
  base changing a PR file, or a PR file reverted to base ⇒ that path; git that cannot say ⇒
  ``None`` (never base-only);
* the trigger CARRIES a clean verdict (re-stamps ``judge_sha``, records why, spawns nothing)
  and ``chela merge`` then accepts it; every other shape is judged as before;
* a re-judge re-runs the previous round's experiments first, so a line already judged is
  re-measured, never re-sampled.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from chela import contract, dispatcher, event_log, judge, verdictcarry
from chela.dispatcher import CI_PASSING, CIStatus
from tests.test_judge import (
    FAKE_GUARD_TEST, REAL_GUARD_TEST, _exp, _judge_run, _run_row, _tick, _wf,
    _workflow_repo,
)

JUDGED, HEAD = "judged000001", "head00000002"


@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    monkeypatch.setattr(judge, "judge_logs_dir", lambda: tmp_path / "judge-logs")


def _git(repo: Path, *argv: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *argv], check=True, capture_output=True,
                          text=True).stdout.strip()


def _commit(repo: Path, msg: str, **files: str) -> str:
    for name, text in files.items():
        (repo / name).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", msg)
    return _git(repo, "rev-parse", "HEAD")


A = "a = {}\n\n\n\n\nq = {}\n"


@pytest.fixture
def repo(tmp_path) -> Path:
    """``dev`` with a.py + b.py; the PR branch ``pr`` changes a.py and is judged there."""
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "dev")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "t")
    _commit(r, "base", **{"a.py": A.format(1, 1), "b.py": "b = 1\n", "c.py": "c = 1\n"})
    _git(r, "checkout", "-q", "-b", "pr")
    _commit(r, "the PR", **{"a.py": A.format(2, 1)})
    return r


def _advance_dev(repo: Path, **files: str) -> None:
    """Move ``dev`` (and ``origin/dev``) on by one commit, then merge it into ``pr``."""
    _git(repo, "checkout", "-q", "dev")
    sha = _commit(repo, "dev moved", **files)
    _git(repo, "update-ref", "refs/remotes/origin/dev", sha)
    _git(repo, "checkout", "-q", "pr")
    _git(repo, "merge", "-q", "--no-edit", "dev")


def test_a_base_only_merge_touches_no_pr_path(repo):
    judged = _git(repo, "rev-parse", "HEAD")
    _advance_dev(repo, **{"b.py": "b = 2\n"})
    head = _git(repo, "rev-parse", "HEAD")
    assert head != judged
    assert verdictcarry.moved_pr_paths(repo, "dev", judged, head) == set()


def test_a_pr_file_changed_on_the_branch_is_named(repo):
    _git(repo, "update-ref", "refs/remotes/origin/dev", "dev")
    judged = _git(repo, "rev-parse", "HEAD")
    head = _commit(repo, "rework", **{"a.py": A.format(3, 1), "c.py": "c = 9\n"})
    assert verdictcarry.moved_pr_paths(repo, "dev", judged, head) == {"a.py", "c.py"}


def test_a_base_commit_that_changes_a_pr_file_is_named(repo):
    judged = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "dev")
    sha = _commit(repo, "dev edits a.py too", **{"a.py": A.format(1, 2), "b.py": "b = 2\n"})
    _git(repo, "update-ref", "refs/remotes/origin/dev", sha)
    _git(repo, "checkout", "-q", "pr")
    _git(repo, "merge", "-q", "--no-edit", "dev")
    head = _git(repo, "rev-parse", "HEAD")
    assert verdictcarry.moved_pr_paths(repo, "dev", judged, head) == {"a.py"}


def test_a_pr_file_reverted_to_base_still_counts_as_moved(repo):
    """It leaves the PR's diff at the new head, so only the JUDGED head's diff names it."""
    _git(repo, "update-ref", "refs/remotes/origin/dev", "dev")
    judged = _git(repo, "rev-parse", "HEAD")
    head = _commit(repo, "revert a.py", **{"a.py": A.format(1, 1)})
    assert verdictcarry.moved_pr_paths(repo, "dev", judged, head) == {"a.py"}


@pytest.mark.parametrize("which", ["judged", "head", "base"])
def test_git_that_cannot_say_is_never_base_only(repo, which):
    _git(repo, "update-ref", "refs/remotes/origin/dev", "dev")
    judged, head, base = _git(repo, "rev-parse", "HEAD"), "HEAD", "dev"
    if which == "judged":
        judged = "0" * 40
    elif which == "head":
        head = "f" * 40
    else:
        base = "nosuchbranch"
    assert verdictcarry.moved_pr_paths(repo, base, judged, head) is None


def test_a_git_that_will_not_run_is_unknown(repo, monkeypatch):
    real = subprocess.run

    def boom(cmd, *a, **k):
        if "diff" in cmd and "--name-only" in cmd and "..." not in cmd[-1]:
            raise subprocess.TimeoutExpired(cmd, 60)
        return real(cmd, *a, **k)

    _git(repo, "update-ref", "refs/remotes/origin/dev", "dev")
    monkeypatch.setattr(verdictcarry.subprocess, "run", boom)
    assert verdictcarry.moved_pr_paths(repo, "dev", "HEAD~1", "HEAD") is None


def test_a_head_pair_git_cannot_diff_is_unknown_even_when_both_pr_diffs_read(repo):
    """Both merge-base diffs read fine, but `git diff judged head` itself fails (exit 128):
    its empty stdout must never read as "no PR path moved". RED with the returncode check
    dropped — the carry would then fire on a head nobody compared."""
    with patch.object(judge, "_changed_files", return_value=["a.py"]):
        assert verdictcarry.moved_pr_paths(repo, "dev", "0" * 40, "f" * 40) is None


@pytest.mark.parametrize("unread", ["judged", "head"])
def test_one_pr_diff_git_cannot_read_is_unknown_even_when_the_head_pair_diffs(repo, unread):
    """Each side alone: the head pair diffs fine, only ONE merge-base diff is unknown. RED
    with that side's `is None` clause dropped (an unknown file set is never base-only)."""
    _git(repo, "update-ref", "refs/remotes/origin/dev", "dev")
    judged = _git(repo, "rev-parse", "HEAD~1")
    head = _git(repo, "rev-parse", "HEAD")
    bad = judged if unread == "judged" else head

    def changed(_repo, _base, sha):
        return None if sha == bad else ["a.py"]

    with patch.object(judge, "_changed_files", side_effect=changed):
        assert verdictcarry.moved_pr_paths(repo, "dev", judged, head) is None


# --- the trigger: carry, or judge as before --------------------------------------------


def _seed(tmp_path, **over):
    wf = _wf(tmp_path)
    fields = dict(workflow_path=str(wf.path), judge_state=judge.J_CLEAN, judge_sha=JUDGED,
                  judge_detail="every guard held", pr_head_sha=HEAD)
    fields.update(over)
    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, **fields)
    return wf


def _tick_with_moved(wf, moved):
    spawns: list[str] = []
    calls: list[tuple] = []

    def fake(*a):
        calls.append(a)
        return moved

    with patch.object(verdictcarry, "moved_pr_paths", side_effect=fake), \
         patch.object(judge, "_changed_files", return_value=["chela/dispatcher.py"]):
        _tick(wf, lambda w, row, sha, conn, task=None: (spawns.append(row["task_id"]), True)[1],
              sha=HEAD)
    return spawns, calls


def test_a_base_only_head_move_carries_the_clean_verdict(tmp_path):
    """⭐ The bug. RED with the trigger's carry call removed, or with the "no PR path moved"
    test inverted: the run is re-judged from scratch."""
    wf = _seed(tmp_path)
    spawns, calls = _tick_with_moved(wf, set())

    run = dispatcher.resolve_run("abc123")
    assert calls == [(wf.path.parent, "dev", JUDGED, HEAD)]
    assert (run["judge_state"], run["judge_sha"]) == (judge.J_CLEAN, HEAD)
    assert run["judge_detail"] == (
        "carried: base-only merge (judged000001 → head00000002); every guard held")
    assert spawns == []
    carried = event_log.read(types=["judge.carried"])["events"]
    assert len(carried) == 1 and carried[0]["payload"] == {
        "task_id": "abc123", "judged_sha": JUDGED, "head_sha": HEAD,
        "reason": "carried: base-only merge"}


@pytest.mark.parametrize("moved", [{"chela/x.py"}, None], ids=["pr-path-moved", "unknown"])
def test_a_head_move_that_touches_a_pr_path_is_judged_again(tmp_path, moved):
    wf = _seed(tmp_path)
    spawns, _ = _tick_with_moved(wf, moved)
    run = dispatcher.resolve_run("abc123")
    assert spawns == ["abc123"]
    assert run["judge_sha"] == JUDGED and run["judge_detail"] == "every guard held"


@pytest.mark.parametrize("over", [
    {"judge_state": judge.J_BLOCKED},
    {"judge_state": judge.J_CANNOT_VERIFY},
    {"judge_state": judge.J_PROSE_ONLY},
    {"judge_sha": HEAD},
    {"judge_sha": None},
    {"pr_head_sha": None},
], ids=["blocked", "cannot-verify", "prose-only", "same-head", "never-judged", "no-head"])
def test_only_a_clean_verdict_on_an_older_head_is_ever_carried(tmp_path, over):
    wf = _seed(tmp_path, **over)
    with dispatcher._db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()
        with patch.object(verdictcarry, "moved_pr_paths", return_value=set()) as probe:
            assert dispatcher._carry_base_only_verdict(wf, row, conn) is False
    probe.assert_not_called()
    assert dispatcher.resolve_run("abc123")["judge_sha"] == over.get("judge_sha", JUDGED)


def test_a_row_that_moved_under_the_carry_is_left_alone(tmp_path):
    """Compare-and-swap: a verdict re-stamped (or replaced) since the row was read wins."""
    wf = _seed(tmp_path)
    with dispatcher._db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()
        conn.execute("UPDATE runs SET judge_sha='other0000003' WHERE task_id='abc123'")
        conn.commit()
        with patch.object(verdictcarry, "moved_pr_paths", return_value=set()):
            assert dispatcher._carry_base_only_verdict(wf, row, conn) is False
    assert dispatcher.resolve_run("abc123")["judge_sha"] == "other0000003"


@pytest.mark.parametrize("state", [judge.J_BLOCKED, judge.J_CANNOT_VERIFY, judge.J_RUNNING])
def test_a_verdict_replaced_after_the_read_is_never_overwritten_by_the_carry(tmp_path, state):
    """CMX-338: the row is read clean, then a concurrent writer (a new verdict, a send-back,
    a re-judge launch) replaces `judge_state` on the SAME judged sha and head — so only the
    CAS on `judge_state` can tell. The carry writes nothing and the new verdict stands."""
    wf = _seed(tmp_path)
    with dispatcher._db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()
        assert row["judge_state"] == judge.J_CLEAN
        dispatcher.set_judge_state("abc123", state, "a newer verdict")
        with patch.object(verdictcarry, "moved_pr_paths", return_value=set()):
            assert dispatcher._carry_base_only_verdict(wf, row, conn) is False
    run = dispatcher.resolve_run("abc123")
    assert (run["judge_state"], run["judge_sha"], run["judge_detail"]) == (
        state, JUDGED, "a newer verdict")
    assert event_log.read(types=["judge.carried"])["events"] == []


def test_chela_merge_accepts_a_carried_verdict(tmp_path):
    """The carried sha is what `contract.merge`'s `judge_sha == head` check reads."""
    (tmp_path / "WORKFLOW.md").write_text("# wf\n")
    wf = _seed(tmp_path)
    with dispatcher._db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()
        with patch.object(verdictcarry, "moved_pr_paths", return_value=set()):
            assert dispatcher._carry_base_only_verdict(wf, row, conn) is True
    with patch.object(contract, "_read_pr_files", return_value=["chela/x.py"]), \
         patch.object(contract, "_read_pr_base", return_value="dev"), \
         patch.object(dispatcher, "_read_pr_checks",
                      return_value=CIStatus(CI_PASSING, head_sha=HEAD)), \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")), \
         patch.object(contract, "_squash_merge",
                      return_value={"ok": True, "merge_commit_sha": "x"}) as squash, \
         patch.dict("os.environ", {}, clear=False) as env:
        env.pop("CHELA_ACTOR", None)
        result = contract.merge("abc123")
    assert result["ok"] is True, result
    squash.assert_called_once()


# --- a re-judge re-runs the prior round's experiments ------------------------------------

HUE = {"guard": "the hue", "kind": "mutation", "file": "guard.py",
       "before": '    hue = "green" if state == "on" else "grey"', "after": '    hue = "grey"'}


def test_a_rejudge_reruns_the_prior_rounds_experiments_first(tmp_path):
    """⭐ Round 1 measured the glyph cue (KILLED). The rework then weakened its test, and
    round 2's judge proposed only a NEW experiment. Re-sampling would read clean; re-running
    the prior set catches the regression. RED with `with_prior` dropped from `judge_run`."""
    result, run, _ = _judge_run(tmp_path, REAL_GUARD_TEST, {"experiments": [_exp()]})
    assert result["state"] == judge.J_CLEAN
    rec = verdictcarry.load("abc123", run["pr_url"])
    assert [e["guard"] for e in rec["experiments"]] == ["the colourblind glyph cue"]

    wt = tmp_path / ".chela" / "wts" / "judge-abc123"
    (wt / "test_guard.py").write_text(FAKE_GUARD_TEST)
    subprocess.run(["git", "-C", str(wt), "-c", "user.name=t", "-c", "user.email=t@example.com",
                    "commit", "-qam", "weaken"], check=True)
    exp_file = tmp_path / "round2.json"
    exp_file.write_text(json.dumps({"experiments": [HUE]}))
    with patch.object(dispatcher, "_post_pr_comment", return_value=(True, "")):
        result = judge.judge_run("abc123", exp_file, cleanup=False)
    assert result["state"] == judge.J_BLOCKED
    assert [(o["guard"], o["verdict"]) for o in result["outcomes"]] == [
        ("the colourblind glyph cue", judge.SURVIVED), ("the hue", judge.KILLED)]


def test_a_held_out_experiment_rerun_from_the_prior_round_stays_held_out(tmp_path):
    """⭐ A held-out experiment re-run on round 2 must still be held out: reported as held
    out, and its guard never named in the PR comment. RED if the record drops the flag."""
    result, run, _ = _judge_run(tmp_path, REAL_GUARD_TEST,
                                {"experiments": [_exp(held_out=True)]})
    assert result["state"] == judge.J_CLEAN
    rec = verdictcarry.load("abc123", run["pr_url"])
    assert [e["held_out"] for e in rec["experiments"]] == [True]

    wt = tmp_path / ".chela" / "wts" / "judge-abc123"
    (wt / "test_guard.py").write_text(FAKE_GUARD_TEST)    # it now SURVIVES: the PR comment
    subprocess.run(["git", "-C", str(wt), "-c", "user.name=t", "-c", "user.email=t@example.com",
                    "commit", "-qam", "weaken"], check=True)
    exp_file = tmp_path / "round2.json"
    exp_file.write_text(json.dumps({"experiments": [HUE]}))
    posted: list[str] = []
    with patch.object(dispatcher, "_post_pr_comment",
                      side_effect=lambda url, d, body: (posted.append(body), (True, ""))[1]):
        result = judge.judge_run("abc123", exp_file, cleanup=False)
    assert result["state"] == judge.J_BLOCKED
    assert [o["guard"] for o in result["outcomes"]] == ["the hue"]   # visible ones only
    assert result["held_out"] == {"total": 1, "survived": 1}
    assert posted and all("the colourblind glyph cue" not in body for body in posted)


def test_with_prior_puts_the_prior_set_first_and_drops_verbatim_repeats():
    prior = {"experiments": [_exp(), HUE]}
    raw, n = verdictcarry.with_prior({"experiments": [HUE], "notes": [1]}, prior, 5)
    assert n == 1
    assert raw == {"experiments": [_exp(), HUE], "notes": [1]}
    assert verdictcarry.with_prior({"experiments": [HUE]}, {}, 5) == ({"experiments": [HUE]}, 0)
    # a repeat is the SAME file, before AND after — any one differing is a new experiment
    for other in (dict(HUE, file="other.py"), dict(HUE, after='    hue = "red"'),
                  dict(HUE, before='    hue = "red"')):
        assert verdictcarry.with_prior({"experiments": [other]}, prior, 5) == (
            {"experiments": [_exp(), HUE, other]}, 2), other


def test_a_prior_round_of_another_pr_is_not_this_prs(tmp_path):
    report = judge.Report(outcomes=[judge.Outcome(judge.Experiment(**_exp()), judge.KILLED, "")])
    verdictcarry.record("abc123", "https://github.com/o/r/pull/1", JUDGED, report)
    assert verdictcarry.load("abc123", "https://github.com/o/r/pull/1")["sha"] == JUDGED
    assert verdictcarry.load("abc123", "https://github.com/o/r/pull/2") == {}
    assert verdictcarry.load("abc123", None) == {}


@pytest.mark.parametrize("url", [None, ""], ids=["none", "empty"])
def test_a_run_with_no_pr_url_never_reads_a_prior_round(url):
    """A record written with no PR url matches no PR — not even another url-less one."""
    report = judge.Report(outcomes=[judge.Outcome(judge.Experiment(**_exp()), judge.KILLED, "")])
    verdictcarry.record("abc123", None, JUDGED, report)
    assert verdictcarry.store_path("abc123").exists()
    assert verdictcarry.load("abc123", url) == {}


def test_a_stale_head_round_records_no_prior_set(tmp_path):
    """A round judged on a head the PR no longer has is not the prior round of the live one."""
    repo = _workflow_repo(tmp_path, "abc123", FAKE_GUARD_TEST)
    with dispatcher._db() as conn:
        _run_row(conn, repo, "abc123", judge_sha="oldsha000001", pr_head_sha="newsha000002")
    exp_file = tmp_path / "experiments.json"
    exp_file.write_text(json.dumps({"experiments": [_exp()]}))
    with patch.object(dispatcher, "_post_pr_comment", return_value=(True, "")):
        result = judge.judge_run("abc123", exp_file, cleanup=False)
    assert result["state"] == judge.J_STALE_HEAD
    assert not verdictcarry.store_path("abc123").exists()


def test_the_record_names_the_sha_actually_judged_not_the_rows_head(tmp_path):
    """The live PR head still equals the judged sha (not stale) while the row's
    `pr_head_sha` lags: the record keeps the sha the battery ran on."""
    repo = _workflow_repo(tmp_path, "abc123", REAL_GUARD_TEST)
    with dispatcher._db() as conn:
        _run_row(conn, repo, "abc123", judge_sha="oldsha000001", pr_head_sha="newsha000002")
    exp_file = tmp_path / "experiments.json"
    exp_file.write_text(json.dumps({"experiments": [_exp()]}))
    with patch.object(dispatcher, "_post_pr_comment", return_value=(True, "")), \
         patch.object(dispatcher, "pr_live_head_sha", return_value="oldsha000001"):
        result = judge.judge_run("abc123", exp_file, cleanup=False)
    assert result["state"] == judge.J_CLEAN
    run = dispatcher.resolve_run("abc123")
    assert verdictcarry.load("abc123", run["pr_url"])["sha"] == "oldsha000001"


def test_with_no_prior_round_the_cap_is_unchanged(tmp_path, monkeypatch):
    """The cap only grows by the prior set's size — a first round runs exactly `cap`."""
    from chela import config

    monkeypatch.setattr(config, "judge_max_experiments", lambda risk: 1)
    result, _, _ = _judge_run(tmp_path, REAL_GUARD_TEST, {"experiments": [_exp(), HUE]})
    assert [o["guard"] for o in result["outcomes"]] == ["the colourblind glyph cue"]


def test_a_round_that_measured_nothing_keeps_the_last_real_one():
    url = "https://github.com/o/r/pull/1"
    keep = judge.Report(outcomes=[judge.Outcome(judge.Experiment(**_exp()), judge.SURVIVED, "")])
    verdictcarry.record("abc123", url, JUDGED, keep)
    invalid = judge.Report(outcomes=[judge.Outcome(judge.Experiment(**HUE), judge.INVALID, "")])
    verdictcarry.record("abc123", url, HEAD, invalid)
    rec = verdictcarry.load("abc123", url)
    assert rec["sha"] == JUDGED
    assert rec["experiments"] == [dict(_exp(), held_out=False, survived=True)]


def test_the_judge_prompt_names_the_prior_round_only_for_a_new_head():
    prior = {"sha": JUDGED, "experiments": [_exp(), HUE]}
    note = verdictcarry.prompt_note(prior, HEAD)
    assert "judged before, on `judged000001`" in note
    assert "re-runs that round's 2 experiment(s)" in note
    assert f"`git diff {JUDGED} HEAD`" in note
    assert verdictcarry.prompt_note(prior, JUDGED) == ""
    assert verdictcarry.prompt_note({}, HEAD) == ""
    assert verdictcarry.prompt_note({"sha": JUDGED, "experiments": []}, HEAD) == ""
    assert verdictcarry.prompt_note({"sha": "", "experiments": [_exp()]}, HEAD) == ""
    rendered = dispatcher.render_prompt(dispatcher.JUDGE_PROMPT, dict(
        dispatcher.judge_prompt_vars(
            wf=None, risk="normal", task_id="t", task_title="", task_body="", branch_name="b",
            base_branch="dev", workspace_path="/w", repo_path="/r", project_key="T",
            task_number=1, pr_url="u", head_sha=HEAD, experiments_path="/e", judge_cmd="c",
            judge_log="l", test_cmd="t", diff_cmd="d", pr_view_cmd="p", prior_round=note)))
    assert "judged before, on `judged000001`" in rendered


def test_the_live_judge_prompt_carries_the_recorded_prior_round(tmp_path):
    """The wiring: `_judge_vars` (what `_spawn_judge` renders) reads THIS run's record."""
    wf = _seed(tmp_path)
    report = judge.Report(outcomes=[judge.Outcome(judge.Experiment(**_exp()), judge.KILLED, "")])
    verdictcarry.record("abc123", "https://github.com/o/r/pull/91", JUDGED, report)
    with dispatcher._db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()
    note = dispatcher._judge_vars(wf, row, tmp_path, HEAD)["prior_round"]
    assert "judged before, on `judged000001`" in note and "1 experiment(s)" in note
    assert dispatcher._judge_vars(wf, row, tmp_path, JUDGED)["prior_round"] == ""


def test_repeated_carries_keep_one_prefix_and_the_original_detail():
    once = verdictcarry.carried_detail(JUDGED, HEAD, "every guard held")
    twice = verdictcarry.carried_detail(HEAD, "third0000003", once)
    assert twice == "carried: base-only merge (head00000002 → third0000003); every guard held"
    bare = verdictcarry.carried_detail(HEAD, "third0000003", verdictcarry.CARRIED)
    assert bare == "carried: base-only merge (head00000002 → third0000003); "


def test_a_git_error_in_the_carry_is_judged_never_raised(tmp_path):
    wf = _seed(tmp_path)
    with dispatcher._db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()
        with patch.object(verdictcarry, "moved_pr_paths", side_effect=FileNotFoundError("git")):
            assert dispatcher._carry_base_only_verdict(wf, row, conn) is False
    assert dispatcher.resolve_run("abc123")["judge_sha"] == JUDGED


def test_a_head_that_moved_again_under_the_carry_is_not_stamped(tmp_path):
    """The paths were compared for THIS head; a newer one must be compared afresh."""
    wf = _seed(tmp_path)
    with dispatcher._db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()
        conn.execute("UPDATE runs SET pr_head_sha='newer0000003' WHERE task_id='abc123'")
        conn.commit()
        with patch.object(verdictcarry, "moved_pr_paths", return_value=set()):
            assert dispatcher._carry_base_only_verdict(wf, row, conn) is False
    assert dispatcher.resolve_run("abc123")["judge_sha"] == JUDGED


def test_the_prior_set_never_crowds_the_new_experiments_out_of_the_cap(tmp_path, monkeypatch):
    from chela import config

    monkeypatch.setattr(config, "judge_max_experiments", lambda risk: 1)
    _judge_run(tmp_path, REAL_GUARD_TEST, {"experiments": [_exp()]})
    exp_file = tmp_path / "round2.json"
    exp_file.write_text(json.dumps({"experiments": [HUE]}))
    with patch.object(dispatcher, "_post_pr_comment", return_value=(True, "")):
        result = judge.judge_run("abc123", exp_file, cleanup=False)
    assert [o["guard"] for o in result["outcomes"]] == ["the colourblind glyph cue", "the hue"]


RET = {"guard": "the hue's value", "kind": "mutation", "file": "guard.py",
       "before": '    return {"glyph": glyph, "hue": hue}',
       "after": '    return {"glyph": glyph, "hue": "grey"}'}


def test_a_round_runs_at_most_twice_the_cap_and_new_experiments_always_run(tmp_path,
                                                                          monkeypatch):
    """CMX-338 — THE BOUND: a round runs at most cap new + cap re-run = 2 × cap experiments.
    Round 2 records 2 (= 2 × cap); round 3 re-runs only a cap's worth of them, so its new
    experiment still runs. RED if the prior set is not limited (round 3 re-runs both and
    drops the new one) or the cap is not grown by what was added."""
    from chela import config

    monkeypatch.setattr(config, "judge_max_experiments", lambda risk: 1)
    _judge_run(tmp_path, REAL_GUARD_TEST, {"experiments": [_exp()]})
    seen = []
    for n, exp in ((2, HUE), (3, RET)):
        exp_file = tmp_path / f"round{n}.json"
        exp_file.write_text(json.dumps({"experiments": [exp]}))
        with patch.object(dispatcher, "_post_pr_comment", return_value=(True, "")):
            result = judge.judge_run("abc123", exp_file, cleanup=False)
        seen.append([o["guard"] for o in result["outcomes"]])
    assert seen == [["the colourblind glyph cue", "the hue"],
                    ["the colourblind glyph cue", "the hue's value"]]
    assert len(verdictcarry.load("abc123", dispatcher.resolve_run("abc123")["pr_url"])
               ["experiments"]) == 2


def test_the_limited_prior_set_keeps_survivors_first():
    killed, survived = dict(_exp(), survived=False), dict(HUE, survived=True)
    prior = {"experiments": [killed, survived, dict(RET)]}
    assert verdictcarry.with_prior({"experiments": []}, prior, 1) == (
        {"experiments": [HUE]}, 1)
    assert verdictcarry.with_prior({"experiments": []}, prior, 3) == (
        {"experiments": [HUE, _exp(), RET]}, 3)
    assert verdictcarry.with_prior({"experiments": []}, prior, 0) == ({"experiments": []}, 0)


def test_the_record_marks_which_experiments_survived():
    url = "https://github.com/o/r/pull/1"
    report = judge.Report(outcomes=[
        judge.Outcome(judge.Experiment(**_exp()), judge.KILLED, ""),
        judge.Outcome(judge.Experiment(**HUE), judge.SURVIVED, "")])
    verdictcarry.record("abc123", url, JUDGED, report)
    assert [e["survived"] for e in verdictcarry.load("abc123", url)["experiments"]] == [
        False, True]


@pytest.mark.parametrize("text", ['{"pr_url": "u", "experiments": "x"}', '["u"]', "{not json"])
def test_a_malformed_record_is_no_prior_round(text):
    path = verdictcarry.store_path("abc123")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    assert verdictcarry.load("abc123", "u") == {}
