"""⚖️ THE JUDGE — and the judge IS A GUARD, so these tests corrupt it and watch it go red.

The judge's blocking verdicts must be FACTS. Every test below is one way a verdict could be
an opinion wearing a fact's clothes, and each one was a real, hand-made mistake on 2026-07-14:

* the mutation that NEVER APPLIED (a `sed` delimiter collided with a `|`): the file was
  unchanged, the suite stayed green, and a naive judge reads that as "the guard is broken" —
  ⛔ it BLOCKS A GOOD PR. Here it must be INVALID.
* the mutation that BROKE THE PARSE (an `if (…) {` deleted, braces unbalanced): the suite
  went red for the wrong reason, and a naive judge reads that as "the guard fired" — ⛔ it
  WAVES A BAD PR THROUGH. Here it must be INVALID, never KILLED.
* a baseline that was never green, a worktree that was not clean, an agent that proposed
  nothing: ⛔ unknown is never a pass — and never a fail either.

The mutation experiments here are REAL: a real git repo, a real production module, a real
pytest guard, a real `python -m pytest` subprocess. The one thing that is stubbed is
`gh` — GitHub is not this module's to own.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from chela import dispatcher, judge

TEST_CMD = f'"{sys.executable}" -m pytest -q'

# The production code under judgment. Its cue is a GLYPH plus a hue — hue alone is the
# deuteranomaly failure mode, which is exactly the guard a reviewer corrupted on PR #85.
GUARD_PY = '''\
def chip(state):
    """The chip's cue: a glyph AND a hue. Hue alone is invisible to a red-weak eye."""
    glyph = "*" if state == "on" else "-"
    hue = "green" if state == "on" else "grey"
    return {"glyph": glyph, "hue": hue}
'''

# A guard that ACTUALLY GUARDS: empty the glyph and this goes red.
REAL_GUARD_TEST = '''\
from guard import chip

def test_the_cue_is_not_hue_alone():
    assert chip("on")["glyph"] != chip("off")["glyph"]

def test_the_hue_is_right():
    assert chip("on")["hue"] == "green"
'''

# The SAME feature, "tested" — and the test cannot fail. Empty the glyph and this is GREEN.
# This is the whole bug class: the feature works, the proof of it does not.
FAKE_GUARD_TEST = '''\
from guard import chip

def test_a_chip_exists():
    assert "glyph" in chip("on")

def test_the_hue_is_right():
    assert chip("on")["hue"] == "green"
'''

# The mutation: the glyph cue, emptied. Minimal, live, and it parses.
GLYPH_BEFORE = '    glyph = "*" if state == "on" else "-"'
GLYPH_AFTER = '    glyph = ""'


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *args],
        check=True, capture_output=True,
    )


def _project(root: Path, guard_test: str = REAL_GUARD_TEST, source: str = GUARD_PY) -> Path:
    """A real git repo with a real production module and a real pytest suite."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "guard.py").write_text(source)
    (root / "test_guard.py").write_text(guard_test)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "the feature and its proof")
    return root


def _exp(**over) -> dict:
    exp = {"guard": "the colourblind glyph cue", "kind": "mutation", "file": "guard.py",
           "before": GLYPH_BEFORE, "after": GLYPH_AFTER}
    exp.update(over)
    return exp


def _run(root: Path, *experiments, notes=None) -> judge.Report:
    return judge.run_experiments(
        root, TEST_CMD, {"experiments": list(experiments), "notes": notes or []}, timeout=120,
    )


# --- (a) THE FACT: a guard that survives corruption. This, and only this, blocks. --------

def test_a_guard_that_cannot_fail_is_a_fact_and_it_BLOCKS(tmp_path):
    root = _project(tmp_path / "repo", guard_test=FAKE_GUARD_TEST)

    report = _run(root, _exp())

    assert [o.verdict for o in report.outcomes] == [judge.SURVIVED]
    assert len(report.blocking) == 1
    assert report.state == judge.J_BLOCKED
    assert report.baseline.green                     # …and the suite really was green first
    o = report.outcomes[0]
    assert o.mutated.exit_code == 0                  # it STILL passed with the cue gone
    # The file is back exactly as the PR ships it — the next experiment's baseline is this.
    assert (root / "guard.py").read_text() == GUARD_PY


def test_a_guard_that_does_its_job_is_KILLED_and_blocks_NOTHING(tmp_path):
    root = _project(tmp_path / "repo", guard_test=REAL_GUARD_TEST)

    report = _run(root, _exp())

    assert [o.verdict for o in report.outcomes] == [judge.KILLED]
    assert report.blocking == []
    assert report.state == judge.J_CLEAN
    assert report.outcomes[0].mutated.exit_code != 0
    assert report.outcomes[0].mutated.failed >= 1
    assert (root / "guard.py").read_text() == GUARD_PY


# --- (b) THE MUTATION THAT NEVER APPLIED — the one that BLOCKS A GOOD PR -----------------

def test_a_mutation_whose_anchor_is_not_in_the_file_is_INVALID_not_a_block(tmp_path):
    """⛔ The `sed`-delimiter trap. The file is UNCHANGED, so of course the suite is green.

    A judge that skipped this check would send a PR back — one whose guard is perfect —
    because of a bug in its own mutation. That is the exact class of defect the judge exists
    to catch, committed by the judge itself.
    """
    root = _project(tmp_path / "repo", guard_test=REAL_GUARD_TEST)

    report = _run(root, _exp(before='    glyph = "*" if state == "ON" else "-"'))  # never matches

    assert [o.verdict for o in report.outcomes] == [judge.INVALID]
    assert report.blocking == []                     # ⛔ THE GOOD PR IS NOT SENT BACK
    assert "never applied" in report.outcomes[0].reason.lower()
    assert (root / "guard.py").read_text() == GUARD_PY   # and nothing was touched


def test_an_ambiguous_anchor_is_INVALID_not_a_block(tmp_path):
    """Two matches: which one moved? An edit you cannot point at is not evidence."""
    root = _project(tmp_path / "repo",
                    source=GUARD_PY + '\n\ndef spare(state):\n    hue = "green"\n    return hue\n')

    report = _run(root, _exp(before='    hue = "green" if state == "on" else "grey"',
                             after='    hue = ""'))
    # The anchor above IS unique; the point of this test is the duplicate one below.
    assert report.outcomes[0].verdict in (judge.KILLED, judge.SURVIVED, judge.INVALID)

    report = _run(root, _exp(before='    hue = "green"', after='    hue = ""'))
    assert [o.verdict for o in report.outcomes] == [judge.INVALID]
    assert report.blocking == []
    assert "occurs 2 times" in report.outcomes[0].reason


def test_a_mutation_that_changes_nothing_is_INVALID(tmp_path):
    root = _project(tmp_path / "repo", guard_test=FAKE_GUARD_TEST)

    report = _run(root, _exp(after=GLYPH_BEFORE))    # before == after

    assert [o.verdict for o in report.outcomes] == [judge.INVALID]
    assert report.blocking == []                     # ⛔ a no-op cannot block a green suite


def test_a_file_outside_the_judge_worktree_is_INVALID(tmp_path):
    root = _project(tmp_path / "repo", guard_test=FAKE_GUARD_TEST)

    report = _run(root, _exp(file="../../etc/hosts"))

    assert [o.verdict for o in report.outcomes] == [judge.INVALID]
    assert "outside the judge worktree" in report.outcomes[0].reason


# --- (c) THE MUTATION THAT BROKE THE PARSE — the one that WAVES A BAD PR THROUGH ---------

def test_a_mutation_that_breaks_the_parse_is_INVALID_never_KILLED(tmp_path):
    """⛔ Red for the WRONG reason. The suite fails because the file no longer LOADS.

    A naive judge reads that red as "the guard fired → the PR is fine" and clears a PR whose
    proof it never actually tested. INVALID is the honest answer: this experiment measured
    nothing.
    """
    root = _project(tmp_path / "repo", guard_test=FAKE_GUARD_TEST)   # the guard is FAKE

    report = _run(root, _exp(before="def chip(state):", after="def chip(state:"))

    o = report.outcomes[0]
    assert o.verdict == judge.INVALID               # ⛔ not KILLED — it proves nothing
    assert "does not parse" in o.reason
    assert report.blocking == []
    assert (root / "guard.py").read_text() == GUARD_PY


def test_a_mutation_that_takes_the_suite_DOWN_is_INVALID_not_a_guard_firing(tmp_path):
    """It parses — and it still isn't evidence: the module stops importing, so the suite
    stops RUNNING. A collapsed test count is not a guard doing its job."""
    root = _project(tmp_path / "repo", guard_test=FAKE_GUARD_TEST)

    report = _run(root, _exp(before="def chip(state):",
                             after="import no_such_module_anywhere\n\ndef chip(state):"))

    o = report.outcomes[0]
    assert o.verdict == judge.INVALID
    assert o.mutated.exit_code != 0                 # it IS red…
    assert "no longer LOADS" in o.reason or "still RAN" in o.reason   # …for the wrong reason
    assert report.blocking == []


# --- (d) UNKNOWN IS NEVER A PASS — AND NEVER A FAIL EITHER -------------------------------

def test_a_baseline_that_is_not_green_verifies_NOTHING(tmp_path):
    """⛔ The load-bearing one. Every SURVIVED means "the suite passed under the mutation".

    Against a suite that never passes — or one that passes for free because it never ran —
    every mutation survives and EVERY PR is blocked. So a red baseline blocks nothing at all.
    """
    root = _project(tmp_path / "repo",
                    guard_test="def test_broken():\n    assert False\n")

    report = _run(root, _exp())

    assert report.cannot_verify
    assert "NOT GREEN" in report.cannot_verify
    assert report.blocking == []
    assert report.state == judge.J_CANNOT_VERIFY
    assert report.outcomes == []                    # it never even started


def test_a_red_baseline_says_WHY_it_was_red(tmp_path):
    """⛔ CMX-80 — the unknown that hid the bug. The judge was inert for three weeks (jsdom
    was never installed in its worktree, so the baseline was red on every PR), and the only
    thing it ever said was "exited 1". An exit code names no cause and nobody can act on it.
    The suite's own words go in the run's `judge_detail` AND in the PR comment."""
    root = _project(tmp_path / "repo",
                    guard_test="def test_the_env_is_missing():\n"
                               "    raise AssertionError('jsdom is not installed')\n")

    report = _run(root, _exp())

    assert report.state == judge.J_CANNOT_VERIFY
    assert "1 failed" in report.cannot_verify          # the suite's summary line, not "exit 1"
    body = judge.comment_body(report, None, TEST_CMD)
    assert "jsdom is not installed" in body            # verbatim, from the suite's own output
    assert "CANNOT VERIFY" in body and "NOT an approval" in body


def test_a_dirty_worktree_verifies_NOTHING(tmp_path):
    root = _project(tmp_path / "repo", guard_test=FAKE_GUARD_TEST)
    (root / "guard.py").write_text(GUARD_PY + "\n# someone else was here\n")

    report = _run(root, _exp())

    assert "not clean" in report.cannot_verify
    assert report.blocking == []


def test_the_judges_own_build_artifacts_do_not_make_the_next_run_cannot_verify(tmp_path):
    """The baseline suite leaves `__pycache__` behind, and `before_run` leaves a `.venv`.

    ⛔ Neither is an edit to the artifact under test. A dirty-check that counted untracked
    files would report CANNOT VERIFY on every PR after the first — a judge that silently
    stops judging, which is the failure mode this whole feature exists to end.
    """
    root = _project(tmp_path / "repo", guard_test=FAKE_GUARD_TEST)
    assert _run(root, _exp()).state == judge.J_BLOCKED
    assert (root / "__pycache__").exists() or (root / ".pytest_cache").exists()
    (root / ".venv").mkdir(exist_ok=True)           # as `hooks.before_run` would leave it

    report = _run(root, _exp())                     # …and it judges exactly as before

    assert not report.cannot_verify
    assert report.state == judge.J_BLOCKED


def test_a_file_that_cannot_be_RESTORED_takes_the_whole_report_down(tmp_path):
    """⛔ Found by the judge, ON ITS OWN PR. It mutated `Report.blocking`'s cannot-verify
    short-circuit away and the suite stayed GREEN — the guard was unfalsifiable, because
    nothing could produce a report that was cannot-verify AND had findings in it.

    This is that path, and it is a real one: a mutation that cannot be reverted leaves the
    worktree carrying code nobody wrote, so every later measurement is about a phantom. The
    findings already in hand are thrown away with it — they were taken before the
    contamination, and "probably still right" is not what a blocking verdict is made of.
    """
    root = _project(tmp_path / "repo", guard_test=FAKE_GUARD_TEST)
    real_write = Path.write_text
    calls = {"n": 0}

    def flaky_write(self, data, *a, **k):
        calls["n"] += 1
        if calls["n"] == 2:                          # the RESTORE of the first experiment
            return real_write(self, data + "\n# contamination\n", *a, **k)
        return real_write(self, data, *a, **k)

    with patch.object(Path, "write_text", flaky_write):
        report = _run(root, _exp(), _exp(guard="a second guard, never measured"))

    assert report.outcomes and report.outcomes[0].verdict == judge.SURVIVED
    assert report.blocking == []                     # ⛔ a SURVIVED finding, and it BLOCKS NOTHING
    assert report.state == judge.J_CANNOT_VERIFY
    assert "could NOT be restored" in report.cannot_verify
    assert len(report.outcomes) == 1                 # it stopped rather than measure a phantom


def test_a_cannot_verify_report_blocks_nothing_whatever_its_findings_say(tmp_path):
    """The invariant above, pinned directly: the choke point is `Report.blocking`."""
    survived = judge.Outcome(
        judge.Experiment(guard="g", file="f.py", before="a", after="b"),
        judge.SURVIVED, "it survived",
    )
    assert judge.Report(outcomes=[survived]).blocking == [survived]
    assert judge.Report(outcomes=[survived], cannot_verify="the baseline was red").blocking == []


def test_zero_experiments_is_CANNOT_VERIFY_not_a_clean_bill_of_health(tmp_path):
    root = _project(tmp_path / "repo")

    report = _run(root)

    assert report.state == judge.J_CANNOT_VERIFY
    assert "NO experiments" in report.cannot_verify
    assert report.blocking == []


def test_experiments_past_the_cap_are_said_out_loud(tmp_path):
    root = _project(tmp_path / "repo", guard_test=FAKE_GUARD_TEST)

    report = judge.run_experiments(
        root, TEST_CMD,
        {"experiments": [_exp() for _ in range(judge.MAX_EXPERIMENTS + 3)]}, timeout=120,
    )

    assert report.dropped == 3                      # ⛔ never silently truncated
    assert f"{report.dropped} further experiment" in judge.comment_body(report, None, TEST_CMD)


# --- (e) JUDGMENT MAY NOT BLOCK ---------------------------------------------------------

def test_notes_are_posted_and_can_never_block(tmp_path):
    """Style, taste, "I'd have done it differently" — a comment, never a rework round.

    The judge is allowed to be USELESS. It is not allowed to be WRONG.
    """
    root = _project(tmp_path / "repo", guard_test=REAL_GUARD_TEST)   # the guard is real

    report = _run(root, _exp(), notes=[
        {"title": "naming", "body": "I'd have called it `cue`"},
        {"title": "design", "body": "this should be a dataclass"},
    ])

    assert report.blocking == []                    # ⛔ two findings, zero blocks
    assert report.state == judge.J_CLEAN
    body = judge.comment_body(report, "https://github.com/o/r/pull/9", TEST_CMD)
    assert "I'd have called it `cue`" in body
    assert "block nothing" in body


# --- (f) the mechanics, pinned one level down -------------------------------------------

def test_apply_mutation_reads_the_file_back_from_disk(tmp_path):
    f = tmp_path / "x.py"
    f.write_text("a = 1\n")

    ok, reason, original = judge.apply_mutation(f, "a = 1", "a = 2")
    assert ok and original == "a = 1\n"
    assert f.read_text() == "a = 2\n"

    ok, reason, _ = judge.apply_mutation(f, "nope", "a = 3")
    assert not ok and "never applied" in reason.lower()


def test_parse_check_catches_a_broken_python_file(tmp_path):
    f = tmp_path / "x.py"
    f.write_text("def f(:\n")
    ok, detail = judge.parse_check(f)
    assert not ok and "does not parse" in detail

    f.write_text("def f():\n    pass\n")
    assert judge.parse_check(f)[0]


def test_counts_read_pytest_and_node(tmp_path):
    assert judge._counts("3 failed, 1108 passed, 2 errors in 41.02s") == (1108, 3, 2)
    assert judge._counts("# pass 15\n# fail 2\n") == (15, 2, 0)


# --- (g) THE CARRIER: a block goes back the ONE way, and the judge NEVER merges ----------

@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")


def _workflow_repo(tmp_path: Path, task_id: str, guard_test: str) -> Path:
    """A repo with a WORKFLOW.md, plus the judge's throwaway worktree already checked out."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "WORKFLOW.md").write_text(
        "---\n"
        "project_key: TEST\n"
        "tracker:\n  kind: markdown\n  path: TODO.md\n"
        f"workspace:\n  root: {tmp_path / '.chela' / 'wts'}\n  base_branch: dev\n"
        f"judge:\n  test_cmd: {json.dumps(TEST_CMD)}\n  suite_timeout_seconds: 120\n"
        "---\n\ndo the thing: {{task_title}}\n"
    )
    (repo / "TODO.md").write_text("- [ ] do a thing\n")
    _project(tmp_path / ".chela" / "wts" / f"judge-{task_id}", guard_test=guard_test)
    return repo


def _run_row(conn, repo: Path, task_id="abc123", **over):
    fields = {
        "task_id": task_id, "workflow_path": str(repo / "WORKFLOW.md"), "title": "do a thing",
        "status": "awaiting_review", "window_name": "test-1", "branch_name": "test-1",
        "worktree_path": str(repo), "started_at": "2026-07-14T10:00:00+00:00", "attempt": 1,
        "task_number": 1, "pr_url": "https://github.com/o/r/pull/91", "pr_state": "open",
        "pr_checks": dispatcher.CI_PASSING, "pr_head_sha": "cafe1234", "rework_count": 0,
    }
    fields.update(over)
    conn.execute(
        f"INSERT INTO runs ({', '.join(fields)}) "
        f"VALUES ({', '.join('?' * len(fields))})", tuple(fields.values()),
    )
    conn.commit()


def _judge_run(tmp_path, guard_test, experiments, task_id="abc123"):
    """The real `chela judge run`: real repo, real mutation, real pytest, real run row."""
    repo = _workflow_repo(tmp_path, task_id, guard_test)
    with dispatcher._db() as conn:
        _run_row(conn, repo, task_id)
    exp_file = tmp_path / "experiments.json"
    exp_file.write_text(json.dumps(experiments))
    posted: list[str] = []
    with patch.object(dispatcher, "_post_pr_comment",
                      side_effect=lambda url, d, body: (posted.append(body), (True, ""))[1]):
        result = judge.judge_run(task_id, exp_file, cleanup=False)
    return result, dispatcher.resolve_run(task_id), posted


def test_a_surviving_guard_sends_the_run_back_through_request_changes(tmp_path):
    """⛔ The ONE carrier — CMX-68's `request_changes`, the same one the CI gate and a human
    use. No second path back into the loop, so the rework cap, the verdict history, the PR
    comment and the re-spawn into the ORIGINAL worktree all keep working, unchanged."""
    result, run, posted = _judge_run(
        tmp_path, FAKE_GUARD_TEST, {"experiments": [_exp()]},
    )

    assert result["state"] == judge.J_BLOCKED
    assert run["status"] == "changes_requested"     # the carrier turned
    assert run["judge_state"] == judge.J_BLOCKED
    verdict = dispatcher.latest_verdict(run)
    assert "SURVIVED DELIBERATE CORRUPTION" in verdict
    assert "the colourblind glyph cue" in verdict
    assert 'glyph = ""' in verdict                  # the exact corruption, in the verdict
    assert posted and "SURVIVED" in posted[0]       # …and on the PR

    # ⛔ A judge round IS a rework round: the dispatcher spends it on the re-spawn, so the
    # judge cannot judge its own rework forever — CHELA_MAX_REWORKS bounds the whole loop.
    assert (run["rework_count"] or 0) == 0          # not spent until the rework SPAWNS
    assert dispatcher.reviews_of(run)[-1]["verdict"] == "changes_requested"


def test_a_clean_run_is_LEFT_ALONE_the_judge_never_merges_and_never_approves(tmp_path):
    result, run, posted = _judge_run(tmp_path, REAL_GUARD_TEST, {"experiments": [_exp()]})

    assert result["state"] == judge.J_CLEAN
    assert run["status"] == "awaiting_review"       # ⛔ exactly where the orchestrator left it
    assert run["judge_state"] == judge.J_CLEAN
    assert not dispatcher.reviews_of(run)           # no verdict was written at all
    assert posted and "every guard held" in posted[0]
    assert "not an approval" in posted[0]


def test_a_cannot_verify_run_is_left_alone_AND_says_so(tmp_path):
    result, run, posted = _judge_run(tmp_path, REAL_GUARD_TEST, {"experiments": []})

    assert result["state"] == judge.J_CANNOT_VERIFY
    assert run["status"] == "awaiting_review"       # neither blocked nor cleared
    assert run["judge_state"] == judge.J_CANNOT_VERIFY
    assert "CANNOT VERIFY" in posted[0]
    assert "NOT an approval" in posted[0]


def test_the_judge_never_shells_out_to_a_merge(tmp_path):
    """A belt-and-braces guard on the one thing the judge must never do."""
    calls: list[list] = []
    real = subprocess.run

    def spy(cmd, *a, **k):
        calls.append(cmd if isinstance(cmd, list) else [str(cmd)])
        return real(cmd, *a, **k)

    with patch.object(judge.subprocess, "run", side_effect=spy):
        _judge_run(tmp_path, FAKE_GUARD_TEST, {"experiments": [_exp()]})

    flat = [" ".join(str(p) for p in c) for c in calls]
    assert not [c for c in flat if "merge" in c or "pr review" in c]


# --- (h) the trigger: one judge per PR HEAD, and never on a red or unsettled one ---------

def _wf(tmp_path, **cfg):
    from chela.workflow import WorkflowDef

    (tmp_path / "TODO.md").write_text("- [ ] do a thing\n")
    return WorkflowDef(
        path=tmp_path / "WORKFLOW.md",
        config={"project_key": "TEST", "tracker": {"kind": "markdown", "path": "TODO.md"},
                "workspace": {"root": str(tmp_path / ".chela" / "wts"), "base_branch": "dev"},
                "judge": {"test_cmd": "pytest"}, **cfg},
        prompt_template="fresh: {{task_title}}",
    )


def _tick(wf, spawned, checks=dispatcher.CI_PASSING, sha="cafe1234", windows=()):
    from chela.workflow import WorkflowStatus

    class R:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, *a, **k):
        r = R()
        if isinstance(cmd, list) and cmd[:2] == ["tmux", "list-windows"]:
            r.stdout = "".join(f"{w}\n" for w in windows)
        if isinstance(cmd, list) and cmd[:3] == ["gh", "pr", "view"] and "statusCheckRollup,headRefOid" in cmd:
            r.stdout = json.dumps({"headRefOid": sha, "statusCheckRollup": [
                {"__typename": "CheckRun", "name": "t", "status": "COMPLETED",
                 "conclusion": "SUCCESS" if checks == dispatcher.CI_PASSING else "FAILURE",
                 "workflowName": "CI", "detailsUrl": "https://github.com/o/r/actions/runs/1/job/2"}
                if checks != dispatcher.CI_PENDING else
                {"__typename": "CheckRun", "name": "t", "status": "IN_PROGRESS"}]})
        elif isinstance(cmd, list) and cmd[:3] == ["gh", "pr", "view"]:
            r.stdout = json.dumps({"state": "OPEN", "mergeable": "MERGEABLE"})
        return r

    with patch.object(dispatcher, "load_workflow_cached",
                      return_value=WorkflowStatus(path=wf.path, workflow=wf, error=None)), \
         patch.object(dispatcher, "get_source", return_value=_EmptySource()), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher.subprocess, "run", side_effect=fake_run), \
         patch.object(dispatcher, "_spawn_judge", side_effect=spawned), \
         patch.object(dispatcher, "remove_worktree", return_value=True), \
         patch.object(dispatcher, "_failing_log_tail", return_value=""), \
         patch.object(dispatcher, "_respawn_rework", return_value=False):
        return dispatcher.tick(wf.path)


class _EmptySource:
    def list_open_tasks(self):
        from chela.sources import Task
        return [Task(id="abc123", title="do a thing", file="TODO.md", line_number=1,
                     raw="- [ ] do a thing")]


def test_the_judge_fires_ONCE_per_head_sha(tmp_path):
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, workflow_path=str(wf.path), pr_head_sha=None)

    spawns: list[str] = []

    def spawn(w, row, sha, conn):
        spawns.append(sha)
        conn.execute("UPDATE runs SET judge_sha=?, judge_state=? WHERE task_id=?",
                     (sha, judge.J_RUNNING, row["task_id"]))
        conn.commit()
        return True

    # The judge's window stays alive across the ticks (a real running judge has one) so the
    # watchdog does not reap it to cannot_verify — this test is about NOT re-spawning onto a
    # judge that is still working, not about the CMX-81 retry.
    win = (judge.judge_window_name("test-1"),)
    assert _tick(wf, spawn, windows=win)["judged"] == 1   # the sha was read from GitHub this tick
    assert _tick(wf, spawn, windows=win)["judged"] == 0   # ⛔ and never judged twice
    assert spawns == ["cafe1234"]

    # A rework pushes a NEW commit: that IS a new judgement — and it spends a rework round,
    # which is what bounds the loop.
    with dispatcher._db() as conn:
        conn.execute("UPDATE runs SET judge_state=NULL WHERE task_id='abc123'")
        conn.commit()
    assert _tick(wf, spawn, sha="f00dbabe", windows=win)["judged"] == 1
    assert spawns == ["cafe1234", "f00dbabe"]


def test_a_cannot_verify_is_a_bounded_RETRY_not_a_permanent_retirement(tmp_path, monkeypatch):
    """⚖️ CMX-81. An unknown (a flake, a gh timeout, a worktree that would not check out) must
    cost a RETRY, never retire the commit from judgment for good.

    The old `judge_sha == pr_head_sha` guard let a SINGLE transient cannot_verify keep the
    judge from ever re-running on that commit — it then merged UNJUDGED, silently defeating
    the judge on any flake. Now the SAME head is re-judged up to `judge_max_unknown_retries`
    while it keeps coming back cannot_verify, and only then settles for a human.
    """
    monkeypatch.setenv("CHELA_JUDGE_MAX_UNKNOWN_RETRIES", "2")
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, workflow_path=str(wf.path), pr_head_sha=None)

    spawns: list[str] = []

    def spawn(w, row, sha, conn):
        # Mirror _spawn_judge's per-commit counter, then simulate the judge finishing with a
        # flake — the exact row state the real cycle leaves behind (verified independently in
        # test_spawn_judge_resets_the_unknown_count_on_a_new_sha_and_bumps_it_on_a_retry).
        spawns.append(sha)
        same = row["judge_sha"] == sha
        prior = (row["judge_cannot_verify_tries"] or 0) if same else 0
        tries = prior + 1 if (same and row["judge_state"] == judge.J_CANNOT_VERIFY) else prior
        conn.execute("UPDATE runs SET judge_sha=?, judge_cannot_verify_tries=? WHERE task_id=?",
                     (sha, tries, row["task_id"]))
        conn.commit()
        dispatcher.set_judge_state(row["task_id"], judge.J_CANNOT_VERIFY, "a flake")
        return True

    # initial + exactly 2 retries = 3 judgements on the same sha; the extra ticks do nothing.
    for _ in range(5):
        _tick(wf, spawn)
    assert spawns == ["cafe1234"] * 3
    run = dispatcher.resolve_run("abc123")
    assert run["judge_state"] == judge.J_CANNOT_VERIFY
    assert run["status"] == "awaiting_review"       # ⛔ never merged, never blocked


def test_only_cannot_verify_re_fires_a_real_verdict_and_a_spent_budget_do_not(tmp_path, monkeypatch):
    """The retry is for UNKNOWNS only. `clean`/`blocked` are verdicts and are terminal; a
    cannot_verify past `judge_max_unknown_retries` settles for a human rather than spinning."""
    monkeypatch.setenv("CHELA_JUDGE_MAX_UNKNOWN_RETRIES", "2")
    wf = _wf(tmp_path)
    fired: list[str] = []

    def spawn(w, row, sha, conn):
        fired.append(row["task_id"])
        return True

    for state, tries, expect_fire in [
        (judge.J_CLEAN, 0, False),          # a real verdict — the judge is done here
        (judge.J_BLOCKED, 0, False),        # a real verdict — it went back through rework
        (judge.J_CANNOT_VERIFY, 0, True),   # unknown, budget untouched → retry
        (judge.J_CANNOT_VERIFY, 1, True),   # unknown, one retry left → retry
        (judge.J_CANNOT_VERIFY, 2, False),  # budget spent → a human's now
    ]:
        fired.clear()
        with dispatcher._db() as conn:
            conn.execute("DELETE FROM runs")
            conn.commit()
            _run_row(conn, tmp_path, workflow_path=str(wf.path), pr_head_sha=None,
                     judge_sha="cafe1234", judge_state=state, judge_cannot_verify_tries=tries)
        _tick(wf, spawn)
        assert bool(fired) is expect_fire, (state, tries)


def test_spawn_judge_resets_the_unknown_count_on_a_new_sha_and_bumps_it_on_a_retry(tmp_path):
    """The counter is per-COMMIT and `_spawn_judge` is its only writer: 0 on a fresh head,
    +1 each time it re-launches on the same head that last came back cannot_verify."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, workflow_path=str(wf.path))

    def _spawn(sha):
        with dispatcher._db() as conn:
            row = conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()
            with patch.object(dispatcher, "detached_worktree", return_value=(None, True)), \
                 patch.object(dispatcher, "render_prompt", return_value="x"), \
                 patch.object(dispatcher, "_judge_vars", return_value={}), \
                 patch.object(dispatcher, "_launch_agent", return_value=None):
                assert dispatcher._spawn_judge(wf, row, sha, conn) is True

    def _state():
        r = dispatcher.resolve_run("abc123")
        return r["judge_sha"], r["judge_state"], r["judge_cannot_verify_tries"]

    _spawn("cafe1234")
    assert _state() == ("cafe1234", judge.J_RUNNING, 0)    # first judgement on this commit

    # a flake: the judge came back cannot_verify. Re-launching the SAME sha is retry #1.
    dispatcher.set_judge_state("abc123", judge.J_CANNOT_VERIFY, "flake")
    _spawn("cafe1234")
    assert _state() == ("cafe1234", judge.J_RUNNING, 1)

    dispatcher.set_judge_state("abc123", judge.J_CANNOT_VERIFY, "flake")
    _spawn("cafe1234")
    assert _state() == ("cafe1234", judge.J_RUNNING, 2)

    # a rework pushes a NEW commit → a fresh judgement, and the count resets to 0.
    _spawn("f00dbabe")
    assert _state() == ("f00dbabe", judge.J_RUNNING, 0)


def test_a_red_pr_is_not_judged_it_is_already_going_back(tmp_path):
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, workflow_path=str(wf.path), pr_head_sha=None)

    summary = _tick(wf, lambda *a: True, checks=dispatcher.CI_FAILING)

    assert summary["judged"] == 0                   # the CI gate owns this one
    assert summary["ci_failed"] == 1


def test_an_unsettled_pr_is_not_judged(tmp_path):
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, workflow_path=str(wf.path), pr_head_sha=None)

    assert _tick(wf, lambda *a: True, checks=dispatcher.CI_PENDING)["judged"] == 0


def test_the_judge_is_off_without_a_test_cmd(tmp_path):
    """⛔ No suite ⇒ no facts ⇒ nothing that may block. A judge with only opinions is off."""
    wf = _wf(tmp_path, judge={})
    assert not judge.judge_enabled(wf)
    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, workflow_path=str(wf.path), pr_head_sha=None)

    assert _tick(wf, lambda *a: True)["judged"] == 0


def test_a_judge_that_dies_is_CANNOT_VERIFY_never_a_pass(tmp_path):
    """Its window is gone and it published nothing. ⛔ That is not "it found nothing"."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, workflow_path=str(wf.path),
                 judge_state=judge.J_RUNNING, judge_sha="cafe1234",
                 judge_started_at=dispatcher._now())   # young: it did not time out, it DIED

    summary = _tick(wf, lambda *a: True, windows=())   # no judge-test-1 window alive

    assert summary["judge_lost"] == 1
    run = dispatcher.resolve_run("abc123")
    assert run["judge_state"] == judge.J_CANNOT_VERIFY
    assert run["status"] == "awaiting_review"       # ⛔ not blocked, not approved
    assert "disappeared" in run["judge_detail"]


def test_a_judge_that_is_still_working_is_left_alone(tmp_path):
    wf = _wf(tmp_path)
    from chela.dispatcher import _now
    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, workflow_path=str(wf.path),
                 judge_state=judge.J_RUNNING, judge_sha="cafe1234", judge_started_at=_now())

    summary = _tick(wf, lambda *a: True, windows=("judge-test-1",))

    assert summary["judge_lost"] == 0
    assert dispatcher.resolve_run("abc123")["judge_state"] == judge.J_RUNNING
    assert summary["judged"] == 0                   # …and only one judge at a time
