"""⚖️🙈🎲 CMX-395 — the judge's train/test split and its run-the-grader-twice check.

PR #529 (CMX-377) went 7 judge rounds: every verdict listed the exact surviving mutations,
every rework patched THOSE mutations, and the next round found new ones — the rework loop
was optimising against its own test set. These tests pin the two fixes:

* **HELD-OUT.** An experiment the judge tags ``"held_out": true`` runs like any other and a
  held-out survivor BLOCKS on its own — but its guard name, file and diff never reach the PR
  comment, the review history, the run row or the rework prompt. Only a COUNT does.
* **CONSISTENCY.** A sampled experiment is run twice; an outcome that flips KILLED↔SURVIVED
  is ``flaky``, named in the comment, and excluded from blocking.

The held-out leak test is end to end: a real `chela judge run` (real repo, real mutations,
real pytest), then the REAL rework prompt rendered from the run row it wrote.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from chela import config, dispatcher, judge, workflow
from chela import main as chela_main
from tests.test_judge import (
    FAKE_GUARD_TEST,
    GLYPH_AFTER,
    GLYPH_BEFORE,
    REAL_GUARD_TEST,
    TEST_CMD,
    _git,
    _project,
    _run_row,
    _workflow_repo,
)

# The held-out experiment's every public-facing token. None of these may appear anywhere
# the coding agent can read.
SENTINEL_GUARD = "HOLDOUT-SENTINEL-GUARD must stay private"
SENTINEL_FILE = "sentinel_mod.py"
SENTINEL_BEFORE = '    return "sentinel-value-7"'
SENTINEL_AFTER = '    return "sentinel-value-0"'
SENTINEL_TOKENS = ("HOLDOUT-SENTINEL", SENTINEL_FILE, "sentinel-value-7", "sentinel-value-0")


@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    # ⛔ The private held-out record lands under CHELA_DIR — a temp one, never ~/.chela.
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path / "chela-dir")


def _glyph(**over) -> dict:
    exp = {"guard": "the colourblind glyph cue", "kind": "mutation", "file": "guard.py",
           "before": GLYPH_BEFORE, "after": GLYPH_AFTER}
    exp.update(over)
    return exp


def _sentinel(**over) -> dict:
    exp = {"guard": SENTINEL_GUARD, "kind": "mutation", "file": SENTINEL_FILE,
           "before": SENTINEL_BEFORE, "after": SENTINEL_AFTER, "held_out": True}
    exp.update(over)
    return exp


def _add_sentinel_module(wt: Path) -> None:
    """An UNTESTED module in the judged worktree — so a mutation to it always survives."""
    (wt / SENTINEL_FILE).write_text(f"def value():\n{SENTINEL_BEFORE}\n")
    _git(wt, "add", SENTINEL_FILE)
    _git(wt, "commit", "-qm", "an untested module")


def _assert_no_leak(text: str, where: str) -> None:
    for token in SENTINEL_TOKENS:
        assert token not in text, f"held-out token {token!r} leaked into {where}"


# --- ⭐ THE LEAK GUARD, end to end ----------------------------------------------------------

def test_a_held_out_survivor_BLOCKS_but_never_reaches_the_PR_the_row_or_the_rework_prompt(
    tmp_path,
):
    task_id = "ho1"
    repo = _workflow_repo(tmp_path, task_id, FAKE_GUARD_TEST)
    wt = judge.judge_worktree_path(workflow.load_workflow(repo / "WORKFLOW.md"), task_id)
    _add_sentinel_module(wt)
    with dispatcher._db() as conn:
        _run_row(conn, repo, task_id)
    exp_file = tmp_path / "experiments.json"
    exp_file.write_text(json.dumps({"experiments": [_glyph(), _sentinel()]}))
    posted: list[str] = []
    with patch.object(dispatcher, "_post_pr_comment",
                      side_effect=lambda url, d, body: (posted.append(body), (True, ""))[1]):
        result = judge.judge_run(task_id, exp_file, cleanup=False)

    run = dispatcher.resolve_run(task_id)
    assert result["state"] == judge.J_BLOCKED
    assert result["blocking"] == 2                        # the held-out one blocks too
    assert result["held_out"] == {"total": 1, "survived": 1}
    # 🎲 the workflow's default consistency sample reached `judge_run` — both survivors re-ran.
    assert result["consistency"] == {"sampled": 2, "flipped": 0, "flip_rate": 0.0}
    assert run["status"] == "changes_requested"

    # The PR comment: the visible survivor in full, the held-out one as a COUNT only.
    assert len(posted) == 1
    assert "the colourblind glyph cue" in posted[0]
    assert "1 held-out guard(s) also survived" in posted[0]
    assert "strengthen the guards in general, not the listed cases" in posted[0]
    _assert_no_leak(posted[0], "the PR comment")

    # The review history (what the rework reads back) and the run row.
    _assert_no_leak(json.dumps(dispatcher.reviews_of(run)), "the review history")
    _assert_no_leak(str(run.get("judge_detail") or ""), "the run row's judge_detail")
    assert dispatcher.latest_required_mutations(run) == [
        {k: v for k, v in _glyph().items()}
    ]

    # The REAL rework prompt, rendered from that row exactly as `_respawn_rework` renders it.
    wf = workflow.load_workflow(repo / "WORKFLOW.md")
    prompt = dispatcher.render_prompt(dispatcher.REWORK_PROMPT, dispatcher._rework_vars(
        wf, run, run["worktree_path"], dispatcher.latest_verdict(run), 1,
        dispatcher.latest_required_mutations(run), None,
    ))
    assert "the colourblind glyph cue" in prompt           # the probe CAN see visible content
    assert "held-out guard(s) also survived" in prompt
    _assert_no_leak(prompt, "the rework prompt")

    # The CLI's own printout of the result names visible outcomes only.
    _assert_no_leak(json.dumps(result["outcomes"]), "the judge result's outcomes")

    # …and the operator CAN read it, privately, under CHELA_DIR.
    store = judge.heldout_store_path(task_id)
    assert store.is_relative_to(config.CHELA_DIR)
    rec = judge.load_private(task_id)[-1]
    held = rec["held_out"]["outcomes"]
    assert [o["guard"] for o in held] == [SENTINEL_GUARD]
    assert held[0]["before"] == SENTINEL_BEFORE and held[0]["after"] == SENTINEL_AFTER
    assert rec["visible"] == {"total": 1, "survived": 1}


def test_judge_show_prints_held_out_details_only_when_asked(tmp_path, capsys):
    task_id = "ho-show"
    report = judge.Report(outcomes=[
        judge.Outcome(judge.Experiment(**{k: v for k, v in _sentinel().items()
                                          if k != "held_out"}, held_out=True),
                      judge.SURVIVED, "survived"),
    ])
    judge.record_private(task_id, report, {"experiments": [_sentinel()]})
    args = argparse.Namespace(run=task_id, held_out=False)
    chela_main.cmd_judge_show(args)
    plain = capsys.readouterr().out
    assert "held-out survival rate: 1/1" in plain
    _assert_no_leak(plain, "`chela judge show` without --held-out")

    chela_main.cmd_judge_show(argparse.Namespace(run=task_id, held_out=True))
    full = capsys.readouterr().out
    assert SENTINEL_GUARD[:40] in full and SENTINEL_BEFORE.strip() in full


# --- a held-out survivor ALONE blocks ------------------------------------------------------

def test_a_held_out_survivor_ALONE_blocks_and_the_block_body_names_nothing(tmp_path):
    root = _project(tmp_path / "repo", guard_test=REAL_GUARD_TEST)
    _add_sentinel_module(root)
    report = judge.run_experiments(
        root, TEST_CMD, {"experiments": [_glyph(), _sentinel()]}, timeout=120,
    )
    assert [o.verdict for o in report.outcomes] == [judge.KILLED, judge.SURVIVED]
    assert report.visible_blocking == []
    assert len(report.held_out_blocking) == 1
    assert report.state == judge.J_BLOCKED            # ⛔ the count alone blocks

    body = judge.block_body(report, "https://github.com/o/r/pull/1", TEST_CMD)
    assert "1 held-out guard(s) also survived" in body
    _assert_no_leak(body, "the block body")


def test_the_clean_comment_counts_held_out_experiments_without_naming_them(tmp_path):
    root = _project(tmp_path / "repo", guard_test=REAL_GUARD_TEST)
    # A held-out experiment that is KILLED — the comment table would otherwise list it.
    held = _glyph(guard="HOLDOUT-SENTINEL hue", held_out=True,
                  before='    hue = "green" if state == "on" else "grey"',
                  after='    hue = "grey"')
    report = judge.run_experiments(root, TEST_CMD, {"experiments": [_glyph(), held]},
                                   timeout=120)
    assert report.state == judge.J_CLEAN
    body = judge.comment_body(report, None, TEST_CMD)
    assert "🙈 1 held-out experiment(s) also ran" in body
    assert "the colourblind glyph cue" in body
    assert "HOLDOUT-SENTINEL" not in body and 'hue = "grey"' not in body


# --- ⭐ THE CASE THAT MUST BE ACCEPTED ------------------------------------------------------

def test_no_survivors_in_either_set_is_CLEAN_even_after_the_consistency_rerun(tmp_path):
    root = _project(tmp_path / "repo", guard_test=REAL_GUARD_TEST)
    held = _glyph(guard="the hue", held_out=True,
                  before='    hue = "green" if state == "on" else "grey"',
                  after='    hue = "grey"')
    report = judge.run_experiments(
        root, TEST_CMD, {"experiments": [_glyph(), held]}, timeout=120, consistency_sample=2,
    )
    assert [o.verdict for o in report.outcomes] == [judge.KILLED, judge.KILLED]
    assert report.consistency == {"sampled": 2, "flipped": 0, "flip_rate": 0.0}
    assert report.flaky == []
    assert report.state == judge.J_CLEAN
    assert report.cannot_verify == ""


# --- 🎲 CONSISTENCY: a flip is flaky, named, and blocks nothing ---------------------------

def _suite(exit_code: int) -> judge.SuiteResult:
    return judge.SuiteResult(ok=True, exit_code=exit_code, passed=2 - exit_code,
                             failed=exit_code, errors=0, tail="")


def _flaky_run(tmp_path, experiments, mutated_exits, sample=2):
    """``run_experiments`` against a real file, with the SUITE scripted: green baseline,
    then one exit code per mutated run, in order (first pass, then the re-runs)."""
    root = _project(tmp_path / "repo", guard_test=REAL_GUARD_TEST)
    _add_sentinel_module(root)
    exits = iter([0, *mutated_exits])
    with patch.object(judge, "provision_suite_env", return_value=""), \
         patch.object(judge, "run_suite", side_effect=lambda *a, **k: _suite(next(exits))):
        return judge.run_experiments(root, TEST_CMD, {"experiments": experiments},
                                     timeout=120, consistency_sample=sample)


def test_a_survivor_that_FLIPS_on_its_rerun_is_flaky_and_does_not_block(tmp_path):
    # glyph: SURVIVED, then KILLED on its re-run.
    report = _flaky_run(tmp_path, [_glyph()], [0, 1])
    (o,) = report.outcomes
    assert o.verdict == judge.SURVIVED and o.rerun_verdict == judge.KILLED
    assert o.flaky
    assert report.blocking == []
    assert report.consistency == {"sampled": 1, "flipped": 1, "flip_rate": 1.0}
    # ⛔ Not clean either: a flip is an unknown, the operator decides.
    assert report.state == judge.J_CANNOT_VERIFY
    assert "FLAKY" in report.cannot_verify and "the colourblind glyph cue" in report.cannot_verify
    body = judge.comment_body(report, None, TEST_CMD)
    assert "🎲 flaky" in body


def test_a_flaky_experiment_is_excluded_but_a_STABLE_survivor_still_blocks(tmp_path):
    # glyph: SURVIVED then SURVIVED (stable). sentinel (visible here): SURVIVED then KILLED.
    report = _flaky_run(tmp_path, [_glyph(), _sentinel(held_out=False)], [0, 0, 0, 1])
    assert [o.flaky for o in report.outcomes] == [False, True]
    assert [o.experiment.guard for o in report.blocking] == ["the colourblind glyph cue"]
    assert report.state == judge.J_BLOCKED
    body = judge.block_body(report, None, TEST_CMD)
    assert "🎲 flaky" in body and SENTINEL_GUARD in body


def test_the_consistency_sample_rechecks_SURVIVORS_first(tmp_path):
    """A survivor is what a flip would wrongly turn into a rework round — so with a sample of
    one, the survivor is re-run, not the earlier KILLED experiment."""
    # glyph: KILLED; sentinel (visible): SURVIVED; the one re-run: KILLED.
    report = _flaky_run(tmp_path, [_glyph(), _sentinel(held_out=False)], [1, 0, 1], sample=1)
    assert [o.rerun_verdict for o in report.outcomes] == ["", judge.KILLED]
    assert report.outcomes[1].flaky


def test_a_held_out_flaky_experiment_is_counted_never_named(tmp_path):
    report = _flaky_run(tmp_path, [_sentinel()], [0, 1])
    assert report.outcomes[0].flaky
    assert report.state == judge.J_CANNOT_VERIFY
    _assert_no_leak(report.cannot_verify, "the flaky cannot-verify reason")
    _assert_no_leak(judge.comment_body(report, None, TEST_CMD), "the flaky comment")
    assert "1 held-out experiment(s)" in report.cannot_verify


def test_the_required_mutation_set_refuses_a_held_out_entry_even_if_one_was_stored():
    run = {"review_history": json.dumps([{"round": 1, "verdict": "changes_requested",
                                          "body": "x", "mutations": [_glyph(), _sentinel()]}])}
    assert dispatcher.latest_required_mutations(run) == [_glyph()]


# --- quota + metrics ------------------------------------------------------------------------

def test_held_out_quota_is_30_percent_with_a_floor_of_one_from_three():
    assert [judge.held_out_quota(n) for n in (0, 1, 2, 3, 4, 7, 10, 12)] == \
        [0, 0, 0, 1, 1, 2, 3, 4]
    # The floor is its own rule, not a side effect of 30%: a 10% knob still holds one out.
    assert judge.held_out_quota(3, 0.1) == 1
    assert judge.held_out_quota(2, 0.9) == 0


def test_metrics_count_rounds_to_clean_and_skip_stale_rounds():
    recs = [
        {"state": "blocked", "visible": {"total": 3, "survived": 2},
         "held_out": {"total": 1, "survived": 1}, "consistency": {"sampled": 2, "flipped": 1}},
        {"state": "clean", "stale": True, "visible": {"total": 9, "survived": 0}},
        {"state": "blocked", "visible": {"total": 3, "survived": 0},
         "held_out": {"total": 1, "survived": 1}, "consistency": {"sampled": 2, "flipped": 0}},
        {"state": "clean", "visible": {"total": 3, "survived": 0},
         "held_out": {"total": 1, "survived": 0}, "consistency": {"sampled": 2, "flipped": 0}},
    ]
    m = judge.private_metrics(recs)
    assert m["rounds"] == 3
    assert m["rounds_to_clean"] == 3
    assert m["visible"] == (2, 9)
    assert m["held_out"] == (2, 3)
    assert m["flips"] == (1, 6)


def test_the_judge_prompt_asks_for_held_out_tags(tmp_path):
    """The real `_judge_vars` → JUDGE_PROMPT render — the quota comes from the knob."""
    repo = _workflow_repo(tmp_path, "ho-prompt", REAL_GUARD_TEST)
    with dispatcher._db() as conn:
        _run_row(conn, repo, "ho-prompt")
    wf = workflow.load_workflow(repo / "WORKFLOW.md")
    row = dispatcher.resolve_run("ho-prompt")
    rendered = dispatcher.render_prompt(
        dispatcher.JUDGE_PROMPT,
        dispatcher._judge_vars(wf, row, judge.judge_worktree_path(wf, "ho-prompt"), "cafe"),
    )
    assert '"held_out": true' in rendered and "about\n     30% of your" in rendered
