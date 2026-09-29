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
    # …but the row still COUNTS it: the visible guard by name, the held-out one by number.
    assert run["judge_detail"] == (
        "the colourblind glyph cue: SURVIVED; 1 held-out guard(s): SURVIVED"
    )
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
    # The run row's judge_detail for a held-out-ONLY block must still give a reason.
    assert judge._blocked_detail(report) == "1 held-out guard(s): SURVIVED"


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
    # Two separate renders — the section heading AND the table cell — pinned separately.
    assert "### 🎲 flaky — excluded from blocking" in body
    assert f"**{judge.SURVIVED}** 🎲 flaky (re-run: {judge.KILLED})" in body


def test_a_KILLED_then_SURVIVED_flip_is_flaky_too_and_the_report_is_NOT_clean(tmp_path):
    """A flip is flaky in BOTH directions. A guard KILLED on its first run and SURVIVED on
    its re-run does not hold — its first-pass KILLED would otherwise pass as clean."""
    # glyph: KILLED, then SURVIVED on its re-run. No other experiment, so nothing else blocks.
    report = _flaky_run(tmp_path, [_glyph()], [1, 0])
    (o,) = report.outcomes
    assert o.verdict == judge.KILLED and o.rerun_verdict == judge.SURVIVED
    assert o.flaky
    assert report.flaky == [o]
    assert report.consistency == {"sampled": 1, "flipped": 1, "flip_rate": 1.0}
    assert report.blocking == []
    assert report.state == judge.J_CANNOT_VERIFY           # ⛔ not J_CLEAN
    assert "FLAKY" in report.cannot_verify and "the colourblind glyph cue" in report.cannot_verify
    assert f"**{judge.KILLED}** 🎲 flaky (re-run: {judge.SURVIVED})" in judge.comment_body(
        report, None, TEST_CMD)


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


def test_an_INVALID_rerun_is_NOT_a_flip_and_the_stable_survivor_still_BLOCKS(tmp_path):
    """A re-run whose suite could not run (timeout, collection crash) proved NOTHING — it is
    not the other fact. Treating SURVIVED→INVALID as a flip would launder a real survivor
    into ``flaky`` and unblock it, which is exactly the weakening the brief forbids."""
    root = _project(tmp_path / "repo", guard_test=REAL_GUARD_TEST)
    timed_out = judge.SuiteResult(ok=False, exit_code=-1, passed=0, failed=0, errors=0,
                                  tail="", detail="timed out")
    runs = iter([_suite(0), _suite(0), timed_out])    # baseline, first pass, the re-run
    with patch.object(judge, "provision_suite_env", return_value=""), \
         patch.object(judge, "run_suite", side_effect=lambda *a, **k: next(runs)):
        report = judge.run_experiments(root, TEST_CMD, {"experiments": [_glyph()]},
                                       timeout=120, consistency_sample=1)
    (o,) = report.outcomes
    assert o.verdict == judge.SURVIVED and o.rerun_verdict == judge.INVALID
    assert not o.flaky
    assert report.flaky == []
    assert report.consistency == {"sampled": 1, "flipped": 0, "flip_rate": 0.0}
    assert [b.experiment.guard for b in report.blocking] == ["the colourblind glyph cue"]
    assert report.state == judge.J_BLOCKED


def test_a_consistency_rerun_that_cannot_RESTORE_its_file_is_CANNOT_VERIFY(tmp_path):
    """The re-run mutates the worktree a second time; if it can't put the file back, every
    finding — including the stable survivor measured before it — is about code nobody wrote.
    Without the contamination return, this report would come out BLOCKED instead."""
    root = _project(tmp_path / "repo", guard_test=REAL_GUARD_TEST)
    calls = {"n": 0}

    def scripted_suite(*a, **k):
        calls["n"] += 1
        return _suite(0)                    # baseline green; SURVIVED both passes (stable)

    real_write = Path.write_text

    def write_text(self, data, *a, **k):
        # Only the re-run's RESTORE fails: suite calls are baseline(1), first pass(2),
        # re-run(3) — the restore after call 3 writes the original back.
        if calls["n"] >= 3 and self.name == "guard.py" and GLYPH_BEFORE in data:
            raise OSError("disk full (scripted)")
        return real_write(self, data, *a, **k)

    with patch.object(judge, "provision_suite_env", return_value=""), \
         patch.object(judge, "run_suite", side_effect=scripted_suite), \
         patch.object(Path, "write_text", write_text):
        report = judge.run_experiments(root, TEST_CMD, {"experiments": [_glyph()]},
                                       timeout=120, consistency_sample=1)
    assert calls["n"] == 3                  # the re-run really happened
    assert "could NOT be restored" in report.cannot_verify
    assert "disk full (scripted)" in report.cannot_verify
    assert report.state == judge.J_CANNOT_VERIFY


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


def test_the_judge_prompt_held_out_floor_follows_HELD_OUT_MIN_EXPERIMENTS(
    tmp_path, monkeypatch,
):
    """The "at least 1 once you propose N or more" floor the judge reads must be the SAME
    constant ``held_out_quota`` enforces — pinned at a NON-default value so neither a literal
    ``0`` nor a literal ``3`` at the ``_judge_vars`` call site can pass (DEFEAT_SHAPES #2)."""
    monkeypatch.setattr(judge, "HELD_OUT_MIN_EXPERIMENTS", 7)
    repo = _workflow_repo(tmp_path, "ho-floor", REAL_GUARD_TEST)
    with dispatcher._db() as conn:
        _run_row(conn, repo, "ho-floor")
    wf = workflow.load_workflow(repo / "WORKFLOW.md")
    row = dispatcher.resolve_run("ho-floor")
    jv = dispatcher._judge_vars(wf, row, judge.judge_worktree_path(wf, "ho-floor"), "cafe")
    assert jv["held_out_min"] == 7
    rendered = dispatcher.render_prompt(dispatcher.JUDGE_PROMPT, jv)
    assert "at least 1 once you propose\n     7 or more" in rendered


def _set_judge_knobs(repo: Path, **knobs) -> None:
    """Add ``judge.<knob>: <value>`` lines to the fixture WORKFLOW.md's judge block.

    Round 1's two knob guards were parked on the defaults (DEFEAT_SHAPES #2): with no knob
    set, ``judge_held_out_fraction(wf)`` IS ``HELD_OUT_FRACTION`` and
    ``judge_consistency_sample(wf)`` IS ``CONSISTENCY_SAMPLE``, so hardcoding the constant at
    the call site was invisible. Every value below is deliberately NOT the default.
    """
    md = repo / "WORKFLOW.md"
    extra = "".join(f"  {k}: {json.dumps(v)}\n" for k, v in knobs.items())
    md.write_text(md.read_text().replace("  suite_timeout_seconds: 120\n",
                                         "  suite_timeout_seconds: 120\n" + extra, 1))


def test_the_judge_prompt_held_out_pct_follows_a_NON_default_knob(tmp_path):
    """judge.held_out_fraction: 0.55 ⇒ the rendered prompt asks for 55%, not the 30% default."""
    assert judge.HELD_OUT_FRACTION != 0.55
    repo = _workflow_repo(tmp_path, "ho-knob", REAL_GUARD_TEST)
    _set_judge_knobs(repo, held_out_fraction=0.55)
    with dispatcher._db() as conn:
        _run_row(conn, repo, "ho-knob")
    wf = workflow.load_workflow(repo / "WORKFLOW.md")
    row = dispatcher.resolve_run("ho-knob")
    jv = dispatcher._judge_vars(wf, row, judge.judge_worktree_path(wf, "ho-knob"), "cafe")
    assert jv["held_out_pct"] == 55
    rendered = dispatcher.render_prompt(dispatcher.JUDGE_PROMPT, jv)
    assert "about\n     55% of your" in rendered
    assert "30% of your" not in rendered


def test_judge_suite_config_carries_NON_default_held_out_and_consistency_knobs(tmp_path):
    """The per-workflow knobs reach the ONE config `run_judge` reads — not the constants."""
    assert judge.CONSISTENCY_SAMPLE != 5 and judge.HELD_OUT_FRACTION != 0.55
    repo = _workflow_repo(tmp_path, "ho-cfg", REAL_GUARD_TEST)
    _set_judge_knobs(repo, held_out_fraction=0.55, consistency_sample=5)
    cfg = judge.judge_suite_config(workflow.load_workflow(repo / "WORKFLOW.md"))
    assert cfg.consistency_sample == 5
    assert cfg.held_out_fraction == 0.55


def test_judge_run_honours_a_NON_default_consistency_sample_end_to_end(tmp_path):
    """judge.consistency_sample: 1 ⇒ a real `chela judge run` re-runs ONE survivor, not the
    default two — the knob, not CONSISTENCY_SAMPLE, reaches `run_experiments`."""
    assert judge.CONSISTENCY_SAMPLE != 1
    task_id = "ho-fwd"
    repo = _workflow_repo(tmp_path, task_id, FAKE_GUARD_TEST)
    _set_judge_knobs(repo, consistency_sample=1)
    wt = judge.judge_worktree_path(workflow.load_workflow(repo / "WORKFLOW.md"), task_id)
    _add_sentinel_module(wt)
    with dispatcher._db() as conn:
        _run_row(conn, repo, task_id)
    exp_file = tmp_path / "experiments.json"
    exp_file.write_text(json.dumps({"experiments": [_glyph(), _sentinel()]}))
    with patch.object(dispatcher, "_post_pr_comment", return_value=(True, "")):
        result = judge.judge_run(task_id, exp_file, cleanup=False)
    assert result["state"] == judge.J_BLOCKED
    assert result["consistency"]["sampled"] == 1


@pytest.mark.parametrize("raw,want", [
    (0.55, 0.55), (0, 0.0), (1, 1.0), (1.7, 1.0), (-0.2, 0.0), ("lots", judge.HELD_OUT_FRACTION),
])
def test_judge_held_out_fraction_parses_and_clamps(raw, want):
    class _Wf:
        def get(self, *keys, default=None):
            return raw if keys == ("judge", "held_out_fraction") else default
    assert judge.judge_held_out_fraction(_Wf()) == want


@pytest.mark.parametrize("raw,want", [
    (5, 5), (0, 0), (-3, 0), ("7", 7), ("many", judge.CONSISTENCY_SAMPLE),
])
def test_judge_consistency_sample_parses_and_clamps(raw, want):
    class _Wf:
        def get(self, *keys, default=None):
            return raw if keys == ("judge", "consistency_sample") else default
    assert judge.judge_consistency_sample(_Wf()) == want


def test_the_private_held_out_record_is_owner_only(tmp_path):
    """The store is 0o600 in a 0o700 directory — nobody but the operator's user reads it."""
    report = judge.Report(outcomes=[
        judge.Outcome(judge.Experiment(**{k: v for k, v in _sentinel().items()
                                          if k != "held_out"}, held_out=True),
                      judge.SURVIVED, "survived"),
    ])
    path = judge.record_private("ho-mode", report, {"experiments": [_sentinel()]})
    assert path is not None
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700


# --- round 2: the three `judge_run` call-site wirings nothing pinned ----------------------

def _spy_run_experiments(calls: list[dict], n_killed: int = 0):
    """Stand-in for `run_experiments` that records the kwargs `judge_run` handed it and
    returns ``n_killed`` KILLED outcomes — the WIRING is under test here, not the battery."""
    def spy(worktree, test_cmd, raw, **kw):
        calls.append(kw)
        exp = judge.Experiment(**{k: v for k, v in _glyph().items()})
        return judge.Report(outcomes=[judge.Outcome(exp, judge.KILLED, "killed")
                                      for _ in range(n_killed)])
    return spy


@pytest.mark.parametrize("reprovision", [False, True], ids=["worktree-present", "reprovisioned"])
def test_judge_run_passes_a_NON_default_consistency_sample_on_BOTH_worktree_paths(
    tmp_path, reprovision,
):
    """`judge_run` calls `run_experiments` from TWO branches — the worktree already on disk,
    and one it just rebuilt with `_reprovision_worktree`. Round 2's survivor hardcoded
    ``consistency_sample=0`` on the rebuilt branch only: the end-to-end knob test above never
    reaches it (its worktree always exists). Parametrised so each branch is read back."""
    task_id = f"ho-rp-{int(reprovision)}"
    repo = _workflow_repo(tmp_path, task_id, FAKE_GUARD_TEST)
    _set_judge_knobs(repo, consistency_sample=5)
    assert judge.CONSISTENCY_SAMPLE != 5
    wt = judge.judge_worktree_path(workflow.load_workflow(repo / "WORKFLOW.md"), task_id)
    if reprovision:
        import shutil
        shutil.rmtree(wt)
    with dispatcher._db() as conn:
        _run_row(conn, repo, task_id)
    exp_file = tmp_path / "experiments.json"
    exp_file.write_text(json.dumps({"experiments": [_glyph()]}))
    calls: list[dict] = []
    reprov_calls: list[tuple] = []

    def fake_reprovision(*a):
        reprov_calls.append(a)
        return ""                                  # "" = rebuilt fine, go ahead

    with patch.object(judge, "run_experiments", side_effect=_spy_run_experiments(calls)), \
         patch.object(judge, "_reprovision_worktree", side_effect=fake_reprovision), \
         patch.object(dispatcher, "_post_pr_comment", return_value=(True, "")):
        judge.judge_run(task_id, exp_file, cleanup=False)
    assert bool(reprov_calls) is reprovision       # the branch under test really ran
    assert len(calls) == 1
    assert calls[0]["consistency_sample"] == 5


def test_a_STALE_head_round_is_recorded_stale_privately_and_metrics_skip_it(tmp_path):
    """The verdict is for `oldsha…`, the PR's live head is `newsha…` ⇒ the private record
    says ``stale: true``, so `chela judge show` does not count it as a round. Corrupt the
    call site to ``stale=False`` and the stale round inflates rounds-to-clean."""
    task_id = "ho-stale"
    repo = _workflow_repo(tmp_path, task_id, FAKE_GUARD_TEST)
    with dispatcher._db() as conn:
        _run_row(conn, repo, task_id, judge_sha="oldsha000001", pr_head_sha="newsha000002")
    exp_file = tmp_path / "experiments.json"
    exp_file.write_text(json.dumps({"experiments": [_glyph()]}))
    with patch.object(dispatcher, "pr_live_head_sha", return_value="newsha000002"), \
         patch.object(dispatcher, "_post_pr_comment", return_value=(True, "")):
        result = judge.judge_run(task_id, exp_file, cleanup=False)
    assert result["state"] == judge.J_STALE_HEAD
    rec = judge.load_private(task_id)[-1]
    assert rec["stale"] is True and rec["sha"] == "oldsha000001"
    assert judge.private_metrics([rec])["rounds"] == 0


def test_a_FRESH_head_round_is_recorded_NOT_stale(tmp_path):
    """Control for the test above: the same round on a head that did not move is a real round
    — without this, ``stale=True`` hardcoded at the call site would pass."""
    task_id = "ho-fresh"
    repo = _workflow_repo(tmp_path, task_id, FAKE_GUARD_TEST)
    with dispatcher._db() as conn:
        _run_row(conn, repo, task_id, judge_sha="oldsha000001", pr_head_sha="oldsha000001")
    exp_file = tmp_path / "experiments.json"
    exp_file.write_text(json.dumps({"experiments": [_glyph()]}))
    with patch.object(dispatcher, "pr_live_head_sha", return_value="oldsha000001"), \
         patch.object(dispatcher, "_post_pr_comment", return_value=(True, "")):
        result = judge.judge_run(task_id, exp_file, cleanup=False)
    assert result["state"] != judge.J_STALE_HEAD
    rec = judge.load_private(task_id)[-1]
    assert rec["stale"] is False
    assert judge.private_metrics([rec])["rounds"] == 1


def test_judge_run_records_the_held_out_quota_from_a_NON_default_fraction(tmp_path):
    """judge.held_out_fraction: 0.55 over 3 experiments ⇒ quota round(1.65) = 2, where the
    30% default gives round(0.9) = 1. The quota in the private record must follow the knob,
    not ``HELD_OUT_FRACTION`` — the number the operator reads to see the judge under-tagged."""
    assert judge.held_out_quota(3, judge.HELD_OUT_FRACTION) == 1
    assert judge.held_out_quota(3, 0.55) == 2
    task_id = "ho-quota"
    repo = _workflow_repo(tmp_path, task_id, FAKE_GUARD_TEST)
    _set_judge_knobs(repo, held_out_fraction=0.55)
    with dispatcher._db() as conn:
        _run_row(conn, repo, task_id)
    exp_file = tmp_path / "experiments.json"
    exp_file.write_text(json.dumps({"experiments": [_glyph()] * 3}))
    calls: list[dict] = []
    with patch.object(judge, "run_experiments",
                      side_effect=_spy_run_experiments(calls, n_killed=3)), \
         patch.object(dispatcher, "_post_pr_comment", return_value=(True, "")):
        judge.judge_run(task_id, exp_file, cleanup=False)
    rec = judge.load_private(task_id)[-1]
    assert rec["held_out"]["quota"] == 2


# --- round 5: four wirings nothing pinned ---------------------------------------------------

def test_an_INVALID_first_pass_does_not_consume_a_consistency_rerun_slot(tmp_path):
    """Only a FACT (KILLED/SURVIVED) is worth re-running. An experiment whose mutation did not
    apply is INVALID on the first pass — sampling it would count a re-run that proved nothing,
    so ``sampled`` must be 1 here (the glyph survivor), not 2."""
    report = _flaky_run(
        tmp_path, [_glyph(before="    this line is not in guard.py"), _glyph()], [0, 0],
        sample=2,
    )
    assert [o.verdict for o in report.outcomes] == [judge.INVALID, judge.SURVIVED]
    assert report.outcomes[0].rerun_verdict == ""
    assert report.consistency == {"sampled": 1, "flipped": 0, "flip_rate": 0.0}


def test_metrics_ignore_a_STALE_rounds_consistency_flips():
    """A stale round is not a round — its flips/samples must not reach the flip rate either."""
    recs = [
        {"state": "blocked", "consistency": {"sampled": 2, "flipped": 1}},
        {"state": "blocked", "stale": True, "consistency": {"sampled": 5, "flipped": 3}},
    ]
    assert judge.private_metrics(recs)["flips"] == (1, 2)


def test_the_private_record_keeps_a_BLOCKED_state_and_reports_no_rounds_to_clean(tmp_path):
    report = judge.Report(outcomes=[
        judge.Outcome(judge.Experiment(**_glyph()), judge.SURVIVED, "survived"),
    ])
    assert report.state == judge.J_BLOCKED
    judge.record_private("ho-blocked", report, {"experiments": [_glyph()]})
    recs = judge.load_private("ho-blocked")
    assert recs[-1]["state"] == judge.J_BLOCKED
    assert judge.private_metrics(recs)["rounds_to_clean"] is None


@pytest.mark.parametrize("task_id", ["../../escape", "a/b", "/abs/path"])
def test_the_private_record_stays_inside_judge_heldout_whatever_the_task_id(tmp_path, task_id):
    report = judge.Report(outcomes=[])
    path = judge.record_private(task_id, report, {"experiments": []})
    assert path is not None
    root = (config.CHELA_DIR / "judge-heldout").resolve()
    assert path.resolve().parent == root
    assert judge.load_private(task_id)             # reads back from the same sanitised path


def test_a_MALFORMED_held_out_experiment_stays_held_out_and_unnamed(tmp_path):
    root = _project(tmp_path / "repo", guard_test=REAL_GUARD_TEST)
    broken = _sentinel()
    del broken["after"]
    report = judge.run_experiments(root, TEST_CMD, {"experiments": [_glyph(), broken]},
                                   timeout=120)
    assert report.outcomes[1].verdict == judge.INVALID
    assert report.outcomes[1].held_out
    assert all(not o.held_out for o in report.visible_outcomes)
    _assert_no_leak(judge.comment_body(report, None, TEST_CMD), "the comment (malformed)")
    _assert_no_leak(json.dumps([o.as_dict() for o in report.visible_outcomes]),
                    "the visible outcomes (malformed)")


# --- rework 6 (orchestrator): record_private never raises -------------------------------

def _one_survivor_report():
    return judge.Report(outcomes=[
        judge.Outcome(judge.Experiment(**_glyph()), judge.SURVIVED, "survived"),
    ])


def test_record_private_swallows_a_WRITE_failure_and_returns_none(tmp_path, monkeypatch, caplog):
    """🔴 GUARD (judge on 28dca1b): a private record that cannot be written must not take the
    verdict down with it. Point the store under a regular FILE so mkdir raises an OSError."""
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    monkeypatch.setattr(judge, "heldout_store_path", lambda task_id: blocker / "sub" / "x.jsonl")
    with caplog.at_level("WARNING"):
        out = judge.record_private("ho-write-fail", _one_survivor_report(), {"experiments": []})
    assert out is None
    assert "could not record the private held-out round" in caplog.text


def test_record_private_swallows_a_BUILD_failure_and_returns_none(monkeypatch, caplog):
    """The same contract when assembling the record fails, not just the write."""
    def boom(*a, **k):
        raise ValueError("synthetic build failure")
    monkeypatch.setattr(judge, "held_out_quota", boom)
    with caplog.at_level("WARNING"):
        out = judge.record_private("ho-build-fail", _one_survivor_report(), {"experiments": []})
    assert out is None
    assert "synthetic build failure" in caplog.text
