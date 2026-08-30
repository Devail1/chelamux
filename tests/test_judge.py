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

⚠️ NOT GUARDED: the WORDING of WORKFLOW.md step 6's and judge.block_body's step 3's
self-check mandate is not machine-verified. A substring assertion cannot distinguish a
mandate ("you must pass one of these flags") from its negation ("neither flag is
required") or an arbitrary paraphrase of either — CMX-258 rework rounds 1-16 closed this
axis for presence, then mandate, then pairing, then negation, at both sites, and each
fix caught one wording and the next round found another (same class CMX-257 retired for
CSS values). The BEHAVIOUR those docs describe — that `task-finished` reads and enforces
`--self-check-experiments`/`--no-new-guards`, and that step 6's flag must point at THE
SAME experiments file step 3 wrote — IS guarded below, by the tests that exercise the
flags themselves rather than the prose that describes them.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from chela import dispatcher, event_log, hold, judge, worktree

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
    # ⛔ CMX-218 rework round: the message is the deliverable here too — a mismatch after a
    # write that did NOT raise must say what was actually observed (sizes), not just that
    # restoration failed, or the next reader re-derives by hand what this line already knew.
    assert "did not raise" in report.cannot_verify
    assert "afterward got" in report.cannot_verify
    assert len(report.outcomes) == 1                 # it stopped rather than measure a phantom


def test_a_restore_write_that_RAISES_names_what_it_raised(tmp_path):
    """⛔ CMX-218 rework round. The OTHER way a restore can fail — the write itself raises
    (permissions, a vanished parent dir, disk full) — must be told apart from a silent
    mismatch: the exception text IS the cause, and it is thrown away if not carried into the
    report."""
    root = _project(tmp_path / "repo", guard_test=FAKE_GUARD_TEST)
    real_write = Path.write_text
    calls = {"n": 0}

    def flaky_write(self, data, *a, **k):
        calls["n"] += 1
        if calls["n"] == 2:                          # the RESTORE of the first experiment
            raise OSError("no space left on device")
        return real_write(self, data, *a, **k)

    with patch.object(Path, "write_text", flaky_write):
        report = _run(root, _exp(), _exp(guard="a second guard, never measured"))

    assert report.state == judge.J_CANNOT_VERIFY
    assert "could NOT be restored" in report.cannot_verify
    assert "no space left on device" in report.cannot_verify


def test_a_cannot_verify_report_blocks_nothing_whatever_its_findings_say(tmp_path):
    """The invariant above, pinned directly: the choke point is `Report.blocking`."""
    survived = judge.Outcome(
        judge.Experiment(guard="g", file="f.py", before="a", after="b"),
        judge.SURVIVED, "it survived",
    )
    assert judge.Report(outcomes=[survived]).blocking == [survived]
    assert judge.Report(outcomes=[survived], cannot_verify="the baseline was red").blocking == []


def test_block_body_step_3_binds_the_self_check_flag_to_the_same_experiments_file():
    """⛔ CMX-258 rework round 12 (judge finding 1, WIRING): pins the BINDING half of
    ``block_body`` step 3 — that the experiments file it names is THE SAME one the judge just
    proved a guard survived corruption in, not any freshly-written file that happens to come
    back clean. Softening the parenthetical from "(the same experiments file `chela judge
    self-check` uses)" to "(any experiments file)" tells a blocked rework agent it may write
    a NEW experiments file for the round it was blocked on — the gate would then re-verify
    guards the agent chose after the fact instead of the ones the judge just proved were
    decoration, on the exact round that matters most. Same shape as
    ``test_workflow_md_step_6_binds_the_self_check_flag_to_the_same_experiments_file`` below,
    pinned at ``block_body``'s own call site instead of ``WORKFLOW.md``'s. (The wording of
    step 3's mandate itself is NOT machine-verified — see the module docstring.)"""
    survived = judge.Outcome(
        judge.Experiment(guard="g", file="f.py", before="a", after="b"),
        judge.SURVIVED, "it survived",
        baseline=judge.SuiteResult(True, 0, 1, 0, 0, ""),
        mutated=judge.SuiteResult(True, 0, 1, 0, 0, ""),
    )
    report = judge.Report(outcomes=[survived],
                           baseline=judge.SuiteResult(True, 0, 1, 0, 0, ""))

    body = judge.block_body(report, "https://x/1", TEST_CMD)

    assert "the same experiments file `chela judge self-check` uses" in body


def test_block_body_points_the_rework_agent_at_the_defeat_shapes_catalog():
    """CMX-272: a SURVIVED verdict is exactly the moment a new defeat shape was just measured
    — the judge's own throwaway checkout is deleted the instant it finishes and can never
    commit ``docs/DEFEAT_SHAPES.md`` itself, so ``block_body`` is the only place that can hand
    the catalog off to the agent that DOES have a branch to put an entry on."""
    survived = judge.Outcome(
        judge.Experiment(guard="g", file="f.py", before="a", after="b"),
        judge.SURVIVED, "it survived",
        baseline=judge.SuiteResult(True, 0, 1, 0, 0, ""),
        mutated=judge.SuiteResult(True, 0, 1, 0, 0, ""),
    )
    report = judge.Report(outcomes=[survived],
                           baseline=judge.SuiteResult(True, 0, 1, 0, 0, ""))

    body = judge.block_body(report, "https://x/1", TEST_CMD)

    assert "docs/DEFEAT_SHAPES.md" in body
    assert "docs/defeat_shapes/" in body
    assert "ONE NEW FILE" in body
    # CMX-284 rework round 3: item 4 mentions `docs/defeat_shapes/` twice — once in a
    # decorative "(see ... for the catalog itself)" parenthetical and once in the load-bearing
    # "add ONE NEW FILE to ..." directive. The three substring checks above are all satisfied
    # by the parenthetical alone, so pointing the DIRECTIVE back at the monolith (the exact
    # append this PR's whole catalog split exists to forbid) still passes them. Pin the
    # directive and its target adjacent, not just present anywhere in the body.
    assert "add ONE NEW FILE to `docs/defeat_shapes/`" in body
    # The decorative parenthetical is the OTHER of the two mentions — pin it too, so reverting
    # it alone (leaving the directive above untouched) still goes red instead of hiding behind
    # the directive's own coverage.
    assert "(see `docs/defeat_shapes/` for the catalog itself)" in body


def test_rework_prompt_points_at_the_defeat_shapes_catalog(tmp_path):
    """CMX-272: the retry-brief a reworking agent wakes up to must point at
    ``docs/DEFEAT_SHAPES.md`` — otherwise the catalog only ever reaches an agent that
    happens to already be reading this test file, exactly the reach problem the catalog
    exists to close.

    Seen to go red: revert the live spawn site's `wf.get(...) or REWORK_PROMPT` fallback
    (`_renudge_prompt`, the same expression `_respawn_rework` renders from) to
    `wf.get(...) or ""` — `dispatcher.REWORK_PROMPT` itself stays byte-identical, so a test
    that only imports the constant can't see the wiring break. Rendering through
    `_renudge_prompt` exercises the actual expression the spawn path evaluates.
    """
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, workflow_path=str(wf.path), rework_count=1,
                 review_history=json.dumps([{"round": 1, "at": "t", "body": "fix the thing"}]))
        row = conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()
    prompt = dispatcher._renudge_prompt(wf, row, None)
    assert prompt is not None
    assert "docs/DEFEAT_SHAPES.md" in prompt
    assert "docs/defeat_shapes/" in prompt
    assert "NEW FILE to `docs/defeat_shapes/`" in prompt


def test_judge_prompt_points_at_the_defeat_shapes_catalog(tmp_path):
    """CMX-272: the judge agent should reach for an already-catalogued shape before spending
    a mutation rediscovering one from scratch.

    Seen to go red: revert the live spawn site's `wf.get(...) or JUDGE_PROMPT` fallback
    (`_spawn_judge`) to `wf.get(...) or ""` — `dispatcher.JUDGE_PROMPT` itself stays
    byte-identical, so a test that only imports the constant can't see the wiring break.
    Capturing the prompt `_spawn_judge` actually hands to `_launch_agent` exercises the real
    expression.
    """
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, workflow_path=str(wf.path))
        row = conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()
        captured = {}
        with patch.object(dispatcher, "detached_worktree", return_value=(None, True)), \
             patch.object(dispatcher, "_refresh_judge_worktree", return_value=None), \
             patch.object(dispatcher, "_judge_vars", return_value={}), \
             patch.object(dispatcher, "_launch_agent",
                           side_effect=lambda *a, **kw: captured.__setitem__("prompt", a[4])):
            assert dispatcher._spawn_judge(wf, row, "cafe1234", conn) is True
    assert "docs/DEFEAT_SHAPES.md" in captured["prompt"]
    # CMX-284 rework round 3: the pre-PR wording already satisfied the assert above on its
    # own — this PR's actual production change was appending "and browse
    # `docs/defeat_shapes/`" to send the judge into the new catalog location. Pin that half
    # too, or a revert to the pre-split wording (judge told to skim a now-empty pointer file)
    # passes silently.
    assert "docs/defeat_shapes/" in captured["prompt"]


def test_defeat_shapes_catalog_documents_every_seeded_shape():
    """CMX-272: pins the 6 shapes the catalog was seeded with (all hit live on 2026-08-13) —
    a doc edit that drops one silently shrinks institutional knowledge back down without
    anyone noticing.

    CMX-284: the catalog moved from one file with a numbered section per shape to one FILE
    per shape under `docs/defeat_shapes/` — every concurrent rework used to append its new
    section to the same shared tail, and two reworks in flight at once collided on the same
    lines every time. Reading "the catalog" now means reading every file in that directory,
    concatenated in filename order, rather than one file's text.

    Seen to go red: gutting a section's BODY down to a stub (e.g. `_TBD._`) while leaving its
    heading byte-identical — a heading-only presence check can't see this, because the
    heading itself survives untouched. Splitting each file into its per-section bodies and
    requiring each of the four labelled fields inside its own section catches it.
    """
    root = Path(__file__).resolve().parent.parent
    shapes_dir = root / "docs" / "defeat_shapes"
    files = sorted(shapes_dir.glob("*.md"))
    assert files, f"no shape files found under {shapes_dir}"
    sections = []
    for f in files:
        text = f.read_text()
        parts = re.split(r"^## \d+\. ", text, flags=re.MULTILINE)[1:]
        sections.extend(parts)

    headings = (
        "Presence/substring assertion defeated by dead-coding",
        "Fixture parked on a default value",
        "Positive-case-only mount (never mounts the OFF state)",
        "Compound mutation proves the pair, not either half",
        "Asserting a source constant instead of the rendered value",
        "Coverage resting on a coincidence in production data",
    )
    # ⛔ CMX-272's original spelling was `len(sections) == len(headings)`, which pinned the
    # catalog at EXACTLY six sections — directly contradicting the feature it guards. The
    # catalog's own "How this catalog grows" contract tells a reworking agent to add a new
    # file as part of the same fix; under an equality check the FIRST agent to obey that
    # instruction reddens CI. Found the hard way: this PR added shapes 7 and 8 and broke it.
    # The real invariant is that the seeded shapes never SHRINK away, so assert a floor and
    # let the catalog grow.
    assert len(sections) >= len(headings), (
        f"the catalog shrank: expected at least {len(headings)} numbered defeat-shape "
        f"sections, found {len(sections)}"
    )
    # Each file's own "Each entry:" spec (see DEFEAT_SHAPES.md's "How this catalog grows")
    # names exactly these three required fields — "Found:" is present on most entries but
    # not mandated by the spec, so it is not required here.
    REQUIRED_FIELDS = ("**Assertion form:**", "**Mutation that defeats it:**",
                       "**Guard form that survives:**")
    # The seeded six must still be present, in order, at the head of the catalog (filename
    # order — 01-..., 02-..., ...).
    for heading, section in zip(headings, sections):
        assert section.startswith(heading), f"missing defeat shape: {heading}"
    # ⭐ Every section — including ones added after seeding — must carry the spec's fields.
    # This is the half that makes growth SAFE rather than merely allowed: a new entry that
    # is a heading with no body is exactly the stub this test was written to catch.
    for section in sections:
        title = section.splitlines()[0] if section.strip() else "<empty section>"
        for field in REQUIRED_FIELDS:
            assert field in section, f"{title!r} is missing its {field} field"


def test_defeat_shapes_index_carries_no_numbered_sections_of_its_own():
    """CMX-284: `docs/DEFEAT_SHAPES.md` is a static pointer at `docs/defeat_shapes/`, not a
    hand-maintained index — that split is the whole fix for the collision this PR closes
    (every concurrent rework used to append a new numbered section to this file's tail, and
    two reworks in flight at once collided on the same lines every time, needing a hand
    renumber). If a numbered `## N. ` section ever creeps back into this file, growth is
    once again editing a shared file instead of adding a new one, and the collision comes
    back.

    Seen to go red: pasting a new shape's `## 21. ...` section directly into
    `DEFEAT_SHAPES.md` instead of `docs/defeat_shapes/21-....md` — the file that was supposed
    to stay untouched by growth gets a numbered section again.
    """
    root = Path(__file__).resolve().parent.parent
    text = (root / "docs" / "DEFEAT_SHAPES.md").read_text()
    assert not re.search(r"^## \d+\. ", text, flags=re.MULTILINE), (
        "docs/DEFEAT_SHAPES.md picked up a numbered defeat-shape section — new shapes belong "
        "in their own file under docs/defeat_shapes/, not appended here"
    )
    assert "docs/defeat_shapes/" in text
    # CMX-284 rework round 3: "docs/defeat_shapes/" also appears in plain prose elsewhere in
    # this file (the "How this catalog grows" section), so the substring check above passes
    # even if the file's ONE markdown link — the whole navigational payload of a file that is
    # otherwise just a pointer — is pointed at a target that doesn't exist. Resolve the link
    # target on disk and require it to actually be the catalog directory.
    m = re.search(r"\[`docs/defeat_shapes/`\]\(([^)]+)\)", text)
    assert m, "index is missing its markdown link to the catalog directory"
    target = (root / "docs" / m.group(1)).resolve()
    assert target == (root / "docs" / "defeat_shapes").resolve(), (
        f"index's catalog link points at {m.group(1)!r}, which does not resolve to "
        "docs/defeat_shapes/"
    )


def test_defeat_shapes_file_headings_are_well_formed_and_match_their_filename():
    """CMX-284 rework round 1: a heading that loses its period is invisible to every tool
    reading the catalog — `re.split(r"^## \\d+\\. ", ...)` (the scan the sibling test above
    uses) silently drops a file whose heading doesn't match this exact shape, and a
    sequential-numbering assertion can't see the gap either, because the malformed heading
    was never counted as a section in the first place. Hand-resolving a merge conflict this
    way orphaned three shapes on cmx-279: `## 21. Title` became `## 21 Title` (period
    dropped), and nothing in the suite noticed.

    Seen to go red: `## 21 A shape with no period` — matches neither `^## \\d+\\. ` (the
    section scanner) nor this test's own `^## \\d+\\. ` check, so it fails LOUDLY here
    instead of silently vanishing from the catalog scan.
    """
    root = Path(__file__).resolve().parent.parent
    shapes_dir = root / "docs" / "defeat_shapes"
    files = sorted(shapes_dir.glob("*.md"))
    assert files, f"no shape files found under {shapes_dir}"
    for f in files:
        filename_num = int(f.name.split("-", 1)[0])
        first_line = f.read_text().splitlines()[0]
        m = re.match(r"^## (\d+)\. ", first_line)
        assert m, (
            f"{f.name}: heading {first_line!r} does not match '## N. Title' (missing the "
            f"period after the number makes this shape invisible to the catalog scan)"
        )
        heading_num = int(m.group(1))
        assert heading_num == filename_num, (
            f"{f.name}: filename number {filename_num} does not match heading number "
            f"{heading_num}"
        )


def test_defeat_shapes_numbers_are_unique_across_the_catalog():
    """CMX-293: CMX-284 made file names collision-proof (two concurrent reworks writing
    `21-foo.md` and `21-bar.md` merge cleanly — no shared lines) and explicitly waved off the
    *number* itself as "a readability aid, not an enforced key". That was the wrong call: the
    number is exactly what a "shape 37" cross-reference — inside this catalog
    (`\\bshapes? N\\b`, `[[N|...]]`) and in the wider test suite's "DEFEAT_SHAPES #N" comments
    — actually points at. Two files both claiming 37 merge without conflict, but "see shape
    37" no longer says which one; the file-level fix left this residue live (measured: two
    shapes both numbered 37 landed on `dev` with no CI signal).

    Seen to go red: two catalog files whose headings both open `## 37. ` — the exact
    collision CMX-284's own writeup declared harmless.
    """
    root = Path(__file__).resolve().parent.parent
    shapes_dir = root / "docs" / "defeat_shapes"
    files = sorted(shapes_dir.glob("*.md"))
    assert files, f"no shape files found under {shapes_dir}"
    numbers = []
    for f in files:
        first_line = f.read_text().splitlines()[0]
        m = re.match(r"^## (\d+)\. ", first_line)
        assert m, f"{f.name}: heading {first_line!r} does not match '## N. Title'"
        numbers.append(int(m.group(1)))
    dupes = sorted({n for n in numbers if numbers.count(n) > 1})
    assert not dupes, (
        f"duplicate defeat-shape numbers: {dupes} — two files claim the same number, making "
        "any 'shape N' cross-reference to it ambiguous; bump one of the colliding files' "
        "number (filename AND heading) to the next free one before merging"
    )


def _cmx_task_number_from_branch(branch: str) -> tuple[int | None, str]:
    """Parse a CMX task number out of a branch name like ``cmx-301`` (case-insensitive).

    Returns ``(number, "")`` when the branch encodes one, or ``(None, reason)`` — a non-empty,
    branch-naming reason — otherwise. A bare ``None`` with no reason is indistinguishable from
    "didn't check"; the reason is what lets a caller skip LOUDLY instead of silently.
    """
    m = re.fullmatch(r"cmx-(\d+)", (branch or "").strip(), flags=re.IGNORECASE)
    if not m:
        return None, f"branch {branch!r} does not match the cmx-N task-branch convention"
    return int(m.group(1)), ""


def test_cmx_task_number_from_branch_parses_or_gives_a_loud_reason():
    """CMX-301 rework round 6 (re-scoped by a human): the mechanical check below skips outright
    when it cannot derive a task number from the branch name (`dev`, a detached CI checkout, a
    release branch, ...). A skip with no reason is indistinguishable from "ran and found
    nothing" — UNKNOWN MUST NOT READ AS OK — so the parsing helper carries its own reason
    string, and this test covers that path directly (no real git repo needed) instead of
    relying on whatever branch happens to be checked out when the suite runs.

    Seen to go red: the helper silently returning `(None, "")` (or matching a non-cmx-N branch)
    for any of the branches below — either would make the caller either skip with a useless
    empty reason or, worse, treat an unrelated branch as if it owned a task number.
    """
    assert _cmx_task_number_from_branch("cmx-301") == (301, "")
    assert _cmx_task_number_from_branch("CMX-301") == (301, "")
    assert _cmx_task_number_from_branch("cmx-7") == (7, "")

    for branch in ("dev", "main", "HEAD", "release/1.2", "cmx-", "cmx-12a", ""):
        number, reason = _cmx_task_number_from_branch(branch)
        assert number is None, f"{branch!r} should not parse to a task number"
        assert reason, f"{branch!r} produced no skip reason — a silent None reads as OK"
        assert repr(branch) in reason, (
            f"{branch!r}'s skip reason {reason!r} doesn't even name the branch that failed"
        )


def test_defeat_shapes_added_files_are_numbered_by_branch_task_id():
    """CMX-301 rework round 6 (re-scoped by a human, superseding rounds 1-5): every prose guard
    tried so far shares one shape — it pins WORDING (a clause, a paragraph, a whole section
    compared with `==`) and proves only that the pinned region is unchanged. Round 5's
    whole-section `==` pin was defeated by inserting the forbidden instruction in the
    *neighbouring* section, and by appending a brand-new section after the pin's own EOF
    terminator — neither touches a byte inside the pinned span, so both mutations left the pin
    intact while reversing what the doc actually told the next agent to do. Chasing the prose
    has no bottom: whatever the next pin misses, a mutation can always be phrased to land
    outside it.

    This test drops prose entirely and checks the invariant DEFEAT_SHAPES.md's instructions are
    actually for: every file this branch adds under `docs/defeat_shapes/` must be numbered with
    THIS BRANCH's own CMX task number, not a number guessed off some file listing. No amount of
    rewording the doc can flip this — only the files actually added to the catalog can.

    UNKNOWN MUST NOT READ AS OK: this can only run when the branch name encodes a CMX task
    number, `origin/dev` is fetched, and the branch actually adds a defeat-shape file — each of
    those missing is not "nothing wrong", so each SKIPS LOUDLY with a stated reason instead of
    quietly passing (an empty loop that never asserts is a green PASSED result proving nothing
    ran; that failure mode is exactly what made rounds 1-5's prose guards decoration).

    Seen to go red: a defeat-shape file added on this branch numbered off "one past the current
    highest" file in a listing instead of this branch's own CMX task number.

    The one sanctioned exception (see "How this catalog grows"'s own text: "the number still
    has to be unique... bump your file's number... to any other free one") is a task number
    that is ALREADY claimed by a file that predates this branch on `origin/dev` — e.g. a CMX
    ticket that spans two PRs, or a genuine historical task-number collision. That is not a
    guess off a listing (a real, already-committed file settles it, not a race against a
    sibling branch's own guess), so an added file numbered off the next free slot instead is
    allowed — but ONLY when `origin/dev` already has a `{task_number}-*.md` file that predates
    this branch; anything else still has to match the task number exactly.
    """
    root = Path(__file__).resolve().parent.parent

    branch_result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=root, capture_output=True, text=True,
    )
    branch = branch_result.stdout.strip() if branch_result.returncode == 0 else ""
    task_number, reason = _cmx_task_number_from_branch(branch)
    if task_number is None:
        pytest.skip(f"cannot derive a CMX task number to check added files against: {reason}")

    have_dev = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", "origin/dev"],
        cwd=root, capture_output=True, text=True,
    )
    if have_dev.returncode != 0:
        pytest.skip("origin/dev is not available in this checkout — cannot diff added files "
                     "against it")

    diff = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=A", "origin/dev...HEAD", "--",
         "docs/defeat_shapes/"],
        cwd=root, capture_output=True, text=True,
    )
    if diff.returncode != 0:
        pytest.skip(f"git diff against origin/dev failed: {diff.stderr.strip()[:300]}")
    added = [line.strip() for line in diff.stdout.splitlines() if line.strip()]
    if not added:
        pytest.skip("this branch adds no files under docs/defeat_shapes/ relative to "
                     "origin/dev — nothing for this check to verify")

    preexisting = subprocess.run(
        ["git", "ls-tree", "--name-only", "origin/dev", "--", "docs/defeat_shapes/"],
        cwd=root, capture_output=True, text=True,
    )
    task_number_already_claimed_on_dev = any(
        Path(p).name.startswith(f"{task_number}-")
        for p in preexisting.stdout.splitlines() if p.strip()
    )

    for path in added:
        filename = Path(path).name
        m = re.match(r"^(\d+)-", filename)
        assert m, f"{filename} was added under docs/defeat_shapes/ with no leading NNN- number"
        file_number = int(m.group(1))
        if file_number != task_number:
            assert task_number_already_claimed_on_dev, (
                f"{filename} is numbered {file_number}, not this branch's own CMX task number "
                f"{task_number} (branch {branch!r}) — numbering off anything else (e.g. one "
                "past the current highest file in a listing) is a decentralized guess that "
                "collides under concurrency; see docs/DEFEAT_SHAPES.md's 'How this catalog "
                "grows'. (The sanctioned exception — task number already claimed by a "
                "pre-existing file on origin/dev — does not apply here: no such file exists.)"
            )
            continue
        # Filename and heading are asserted to agree elsewhere (see
        # test_defeat_shapes_file_headings_are_well_formed_and_match_their_filename), but that
        # test says nothing about the TASK number — check the heading directly too, so a file
        # correctly named `301-*.md` but whose own heading claims a different shape number
        # still fails here instead of only on the (separate, filename-vs-heading-only) test.
        first_line = (root / path).read_text().splitlines()[0]
        heading_m = re.match(r"^## (\d+)\. ", first_line)
        assert heading_m, f"{filename}: heading {first_line!r} does not match '## N. Title'"
        assert int(heading_m.group(1)) == task_number, (
            f"{filename}'s heading claims shape {heading_m.group(1)}, not this branch's own "
            f"CMX task number {task_number} (branch {branch!r})"
        )


def test_defeat_shapes_cross_references_resolve_to_shapes_that_exist():
    """CMX-284 rework round 1: entries cross-reference each other by number ("the render-side
    mirror of shape 13", "[[21|entry 21]]") — under the old single-file catalog, renumbering
    to resolve a merge conflict could silently orphan a reference (rename shape 13 to 14 and
    every "shape 13" mention elsewhere now points at the wrong entry, or at nothing), and
    nobody could check that mechanically because there was no enumerable set of "shapes that
    exist" independent of the numbering itself. One file per shape makes that set concrete:
    the filenames. Assert every cross-reference resolves to one of them.

    Seen to go red: a reference left behind as "shape 31" (or any number with no
    `docs/defeat_shapes/31-*.md` file) after a renumber that missed one mention.
    """
    root = Path(__file__).resolve().parent.parent
    shapes_dir = root / "docs" / "defeat_shapes"
    files = sorted(shapes_dir.glob("*.md"))
    assert files, f"no shape files found under {shapes_dir}"
    existing = {int(f.name.split("-", 1)[0]) for f in files}

    ref_pattern = re.compile(r"\bshapes? (\d+)\b|\[\[(\d+)\|")
    for f in files:
        text = f.read_text()
        for m in ref_pattern.finditer(text):
            num = int(m.group(1) or m.group(2))
            assert num in existing, (
                f"{f.name} references shape {num}, which has no "
                f"docs/defeat_shapes/{num:02d}-*.md file"
            )


def test_defeat_shapes_each_file_carries_exactly_one_numbered_section():
    """CMX-284 rework round 3: one-file-per-shape is the whole point of this PR ('a new file
    has no shared lines to collide on'), but nothing enforced it — the index test only reads
    `DEFEAT_SHAPES.md`, and the filename/heading test above reads only each file's FIRST
    line. A SECOND `## N. ` section appended to the tail of an existing shape file is
    invisible to both: the index stays untouched and the first line still matches its
    filename, so growth silently goes back to editing a shared file — the exact
    conflicting-tail collision this split exists to remove.

    Seen to go red: appending a second `## 33. ...` section to the tail of an existing shape
    file instead of creating a new one.
    """
    root = Path(__file__).resolve().parent.parent
    shapes_dir = root / "docs" / "defeat_shapes"
    files = sorted(shapes_dir.glob("*.md"))
    assert files, f"no shape files found under {shapes_dir}"
    for f in files:
        headings = re.findall(r"^## \d+\. ", f.read_text(), flags=re.MULTILINE)
        assert len(headings) == 1, (
            f"{f.name} carries {len(headings)} numbered sections — one file per shape means "
            "exactly one; a growing catalog adds a new file, not a second section here"
        )


def test_workflow_md_step_3_tells_the_agent_to_keep_the_experiments_file():
    """⛔ CMX-258 rework round 4, finding 3 (WIRING): step 3 tells the agent to KEEP the
    experiments JSON file so step 6 can consume it. If this instruction reverses (an agent
    told to delete the file instead), following the doc destroys the path before step 6
    exists — `task-finished --self-check-experiments <path>` can never run, and every run
    silently falls back to the warn-only `--no-new-guards` path. Pins step 3's half of the
    step-3-to-step-6 wiring; the sibling test below pins step 6's half."""
    root = Path(__file__).resolve().parent.parent
    text = " ".join((root / "WORKFLOW.md").read_text().split())

    assert "Keep the experiments JSON file" in text
    assert "do not delete it after step 3" in text
    assert "step 6 needs its path" in text


def test_workflow_md_step_6_binds_the_self_check_flag_to_the_same_experiments_file():
    """⛔ CMX-258 rework round 10 (judge finding 3, WIRING): the sibling test above pins step
    3's half of the step-3-to-step-6 wiring ('Keep the experiments JSON file … step 6 needs
    its path'), but step 6's own half — that `--self-check-experiments` must point at THE
    SAME file step 3 wrote, not any freshly-written one that happens to come back clean —
    was unpinned. Softening 'the SAME experiments file from step 3' to 'an experiments file'
    unbinds the gate from the guards this run actually added, which is exactly the
    prose-that-can-be-skipped failure CMX-250 exists to close. (The wording of step 6's
    mandate to pass a flag at all is NOT machine-verified — see the module docstring.)"""
    root = Path(__file__).resolve().parent.parent
    text = " ".join((root / "WORKFLOW.md").read_text().split())

    assert "the SAME experiments file from step 3" in text


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


def _workflow_repo(tmp_path: Path, task_id: str, guard_test: str, *,
                   linked: bool = False) -> Path:
    """A repo with a WORKFLOW.md, plus the judge's throwaway worktree already checked out.

    ``linked=True`` builds that worktree the way PRODUCTION does — `git worktree add
    --detach` off a real repo, so its ``.git`` is a FILE — instead of the standalone
    `git init` the default takes. CMX-320: those two differ in exactly the dimension
    `remove_worktree`'s new guard reads (a `.git` DIRECTORY means "real repository, never
    delete"), so any test that actually REAPS the worktree must use the faithful shape or
    it is asserting about a fixture artifact rather than about production. The 26 tests
    that only mutate files inside the directory are unaffected either way and keep the
    cheaper default.
    """
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
    wt = tmp_path / ".chela" / "wts" / f"judge-{task_id}"
    if linked:
        # Production's shape: the guard files live in the REPO, and the judge's worktree is
        # a linked checkout of it (`.git` is a file). See this function's docstring.
        (repo / "guard.py").write_text(GUARD_PY)
        (repo / "test_guard.py").write_text(guard_test)
        _git(repo, "init", "-q", "-b", "main")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "the feature and its proof")
        wt.parent.mkdir(parents=True, exist_ok=True)
        worktree.detached_worktree(repo, "main", wt, wt.parent)
        assert (wt / ".git").is_file(), "fixture must model a LINKED worktree"
    else:
        _project(wt, guard_test=guard_test)
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
    assert len(posted) == 1                          # ⚖️🕳️ CMX-228: exactly once — not double

    # ⛔ A judge round IS a rework round: the dispatcher spends it on the re-spawn, so the
    # judge cannot judge its own rework forever — CHELA_MAX_REWORKS bounds the whole loop.
    assert (run["rework_count"] or 0) == 0          # not spent until the rework SPAWNS
    assert dispatcher.reviews_of(run)[-1]["verdict"] == "changes_requested"


def test_a_surviving_guard_hands_the_exact_mutation_forward_as_the_REQUIRED_MUTATION_SET(tmp_path):
    """⚖️🎯 CMX-269. The prose verdict is not the only thing a SURVIVED guard produces — the
    exact ``{guard, file, before, after, kind}`` that beat it must reach `request_changes` as
    DATA too, verbatim from the judge's own `Experiment`, not reformatted from `block_body`'s
    markdown. This is what a rework brief later copies into its REQUIRED MUTATION SET
    instead of asking the agent to reconstruct it from prose.

    ⛔ Rework round 2, finding 2: submit a WIRING-kind experiment and pin ``kind`` through
    the round-trip too — ``Experiment.parse`` defaults an absent/unrecognised ``kind`` back
    to ``"mutation"``, so if ``as_dict`` ever stopped emitting it, a required WIRING
    experiment would silently come back demanding a plain mutation instead — and every
    assertion here that checks only ``file``/``before``/``after``/``guard`` would stay green."""
    result, run, posted = _judge_run(
        tmp_path, FAKE_GUARD_TEST, {"experiments": [_exp(kind="wiring")]},
    )
    assert result["state"] == judge.J_BLOCKED

    required = dispatcher.latest_required_mutations(run)
    assert len(required) == 1
    assert required[0]["file"] == "guard.py"
    assert required[0]["before"] == GLYPH_BEFORE
    assert required[0]["after"] == GLYPH_AFTER
    assert required[0]["guard"] == "the colourblind glyph cue"
    assert required[0]["kind"] == "wiring"


def test_the_REQUIRED_MUTATION_SET_carries_only_the_survivor_not_every_outcome(tmp_path):
    """⛔ Rework round 3, finding 2: `request_changes` must be handed
    ``mutations=[o.experiment.as_dict() for o in blocking]`` — the SURVIVED subset — never
    ``report.outcomes``, which is every experiment the judge ran regardless of verdict. Every
    other test in this file submits a single experiment, so ``blocking`` and
    ``report.outcomes`` are the same list and nothing tells them apart. Submit TWO: the glyph
    (survives — `FAKE_GUARD_TEST`'s glyph check is trivial) and the hue (still a real check,
    so it's KILLED). If a KILLED experiment ever leaked into the REQUIRED MUTATION SET, a
    rework brief would demand an agent re-test a mutation its own guard already defeats."""
    hue_before = '    hue = "green" if state == "on" else "grey"'
    hue_after = '    hue = "grey"'
    result, run, posted = _judge_run(
        tmp_path, FAKE_GUARD_TEST,
        {"experiments": [_exp(), _exp(guard="the hue cue", before=hue_before, after=hue_after)]},
    )
    assert result["state"] == judge.J_BLOCKED
    assert result["blocking"] == 1
    assert len(result["outcomes"]) == 2                # both ran — one SURVIVED, one KILLED

    required = dispatcher.latest_required_mutations(run)
    assert [r["guard"] for r in required] == ["the colourblind glyph cue"]


def test_the_REQUIRED_MUTATION_SET_carries_every_survivor_not_just_the_first(tmp_path):
    """⛔ Rework round 8, findings 1+2: two hops upstream of the stored review entry, both
    still written assuming a required set of ONE. `judge.judge_run` must hand
    `request_changes` every survived experiment (`blocking`, not `blocking[:1]`), and
    `request_changes` must STORE every one of them on the review entry (not
    `mutations[:1]`) — every other test in this file (including the one directly above,
    which submits two experiments but only one SURVIVES) still hands both hops a required
    set of exactly one, so `blocking` and `blocking[:1]` stay indistinguishable everywhere
    else. These two hops are the PERMANENT-loss ones: truncated here, a survivor is gone
    from the row before the brief, the enforcement scan, or the print loop ever see it —
    nothing downstream can recover it.

    Submit TWO experiments that BOTH survive `FAKE_GUARD_TEST` — the glyph cue (as always)
    and the hue's OFF-state, which the fake guard also never checks (it only asserts
    `chip("on")["hue"] == "green"`) — and assert `latest_required_mutations` names both, in
    order, straight off the real `judge_run` → `request_changes` → storage path."""
    hue_off_before = '    hue = "green" if state == "on" else "grey"'
    hue_off_after = '    hue = "green" if state == "on" else "green"'
    result, run, posted = _judge_run(
        tmp_path, FAKE_GUARD_TEST,
        {"experiments": [_exp(), _exp(guard="the hue distinguishes on from off",
                                       before=hue_off_before, after=hue_off_after)]},
    )
    assert result["state"] == judge.J_BLOCKED
    assert result["blocking"] == 2
    assert len(result["outcomes"]) == 2

    required = dispatcher.latest_required_mutations(run)
    assert [r["guard"] for r in required] == [
        "the colourblind glyph cue", "the hue distinguishes on from off",
    ]


def test_a_blocking_verdict_still_posts_to_the_PR_when_the_run_moved_first(tmp_path):
    """⚖️🕳️ CMX-228: the run moved out of `awaiting_review` (a human merged it, or CI got
    there first) WHILE the judge was mid-run. `request_changes`'s compare-and-swap correctly
    refuses to resurrect the row — but that refusal must not also swallow the ONE record
    that a guard SURVIVED corruption. Before this fix the comment posted from INSIDE
    `request_changes`, past its own status check and CAS, so this exact race silently
    dropped it — while a clean verdict (no such gate) always posted. Inverted severity.

    ⚖️🧊 CMX-239: the run ROW's `judge_state` must not repeat that same inversion one layer
    down. It used to record `J_CANNOT_VERIFY` here — downgrading a CONFIRMED finding (a
    guard SURVIVED corruption) to the same shrug-tier state as a launch failure. It must
    record `J_BLOCKED_RACE`: unambiguous, and never confusable with an ordinary blocked run
    that later moved on (see `inbox.py`'s `J_BLOCKED_RACE` handling)."""
    task_id = "abc123"
    repo = _workflow_repo(tmp_path, task_id, FAKE_GUARD_TEST)
    with dispatcher._db() as conn:
        _run_row(conn, repo, task_id, status="done")     # moved out from under the judge
    exp_file = tmp_path / "experiments.json"
    exp_file.write_text(json.dumps({"experiments": [_exp()]}))
    posted: list[str] = []
    with patch.object(dispatcher, "_post_pr_comment",
                      side_effect=lambda url, d, body: (posted.append(body), (True, ""))[1]):
        result = judge.judge_run(task_id, exp_file, cleanup=False)

    assert result["state"] == judge.J_BLOCKED_RACE       # NOT cannot_verify — a CONFIRMED find
    run = dispatcher.resolve_run(task_id)
    assert run["status"] == "done"                        # untouched — no resurrection
    assert run["judge_state"] == judge.J_BLOCKED_RACE
    assert posted and "SURVIVED" in posted[0]             # …but the finding still reached the PR
    assert "colourblind glyph cue" in posted[0]


def test_a_blocking_verdict_for_a_head_that_no_longer_exists_does_not_spend_a_rework_round(tmp_path):
    """⚖️⏱️ CMX-246: this judge was launched to judge `oldsha000001`, but by the time its
    mutation battery finishes (minutes later), a newer commit has landed on the PR —
    `pr_head_sha` now reads `newsha000002`. The once-per-sha trigger already re-spawns a
    fresh judge for the new head on its own; THIS verdict is about a commit that no longer
    exists as the PR's live head. It must not spend a round of `CHELA_MAX_REWORKS` for a
    finding the newer commit may have already fixed — at the cap, that would escalate the
    run to `needs_human` for work that is already done.

    Corrupt `stale_head` to `False` (the mismatch is never detected) and this goes red: the
    stale verdict spends a round through `request_changes` exactly as an ordinary blocking
    verdict would."""
    task_id = "abc123"
    repo = _workflow_repo(tmp_path, task_id, FAKE_GUARD_TEST)
    with dispatcher._db() as conn:
        _run_row(conn, repo, task_id, judge_sha="oldsha000001", pr_head_sha="newsha000002")
    exp_file = tmp_path / "experiments.json"
    exp_file.write_text(json.dumps({"experiments": [_exp()]}))
    posted: list[str] = []
    with patch.object(dispatcher, "_post_pr_comment",
                      side_effect=lambda url, d, body: (posted.append(body), (True, ""))[1]):
        result = judge.judge_run(task_id, exp_file, cleanup=False)

    assert result["ok"] is False
    assert result["state"] == judge.J_STALE_HEAD
    run = dispatcher.resolve_run(task_id)
    assert run["status"] == "awaiting_review"          # never moved to changes_requested
    assert (run["rework_count"] or 0) == 0              # ⛔ no round spent
    assert not dispatcher.reviews_of(run)               # request_changes was never called
    assert run["judge_sha"] == "oldsha000001"           # untouched — never overwritten
    assert posted and "SURVIVED" in posted[0]           # the finding still reached the PR
    assert "oldsha000001"[:12] in posted[0]              # ⚖️⏱️ CMX-246 Objective 2: both
    assert "newsha000002"[:12] in posted[0]              # shas, named, in the PR comment itself


def test_a_stale_verdict_announces_both_shas_on_the_PR_and_in_the_event_log(tmp_path):
    """⚖️⏱️ CMX-246 Objective 2: not charging a stale verdict fixes the rework budget, but a
    human still cannot tell a stale verdict from a live one without diffing the verdict's
    timestamp against the PR's commit history by hand — which is exactly what motivated this
    ticket (CMX-230, CMX-240 twice, CMX-243). Both the PR comment and the durable event log
    must name the judged sha AND the PR's live head, loudly, so nobody re-triages a
    superseded finding as if it were current.

    The fixture is CMX-240's real pair: the judge's verdict was for the commit landed at
    23:04:30Z; a newer commit, `32caded`, superseded it at 23:19:38Z — the actual incident
    that wrote this ticket, not a hypothetical one.

    Corrupt the notice (drop either sha from the PR comment, or from the event payload) and
    this goes red."""
    task_id = "abc123"
    judged_sha = "23h04m30scmx240"      # the commit this judge's verdict was actually for
    live_sha = "32caded"                # the real CMX-240 sha that superseded it at 23:19:38Z
    repo = _workflow_repo(tmp_path, task_id, FAKE_GUARD_TEST)
    with dispatcher._db() as conn:
        _run_row(conn, repo, task_id, judge_sha=judged_sha, pr_head_sha=live_sha)
    exp_file = tmp_path / "experiments.json"
    exp_file.write_text(json.dumps({"experiments": [_exp()]}))
    posted: list[str] = []
    with patch.object(dispatcher, "_post_pr_comment",
                      side_effect=lambda url, d, body: (posted.append(body), (True, ""))[1]):
        result = judge.judge_run(task_id, exp_file, cleanup=False)

    assert result["state"] == judge.J_STALE_HEAD
    assert posted
    assert judged_sha[:12] in posted[0]
    assert live_sha[:12] in posted[0]
    assert "SUPERSEDED" in posted[0]
    # ⚖️⏱️ CMX-246 rework round 4, finding 1: role-pinned, not just containment —
    # `test_stale_head_notice_names_the_shas_in_the_CORRECT_roles` already pins the
    # formatter's own argument order in isolation, but nothing upstream of it pinned
    # which sha the BLOCKING call site (`_stale_head_notice(verified_sha, live_head)`)
    # actually passes as which argument. Swap the two arguments at that call site and
    # both shas still land somewhere in `posted[0]` — the plain `in` checks above stay
    # green — but they land in the WRONG roles, which these next four assertions catch.
    assert f"this verdict is for `{judged_sha[:12]}`" in posted[0]
    assert f"a newer commit, `{live_sha[:12]}`," in posted[0]
    assert f"this verdict is for `{live_sha[:12]}`" not in posted[0]
    assert f"a newer commit, `{judged_sha[:12]}`," not in posted[0]

    events = event_log.read(types=["judge.stale_head"])["events"]
    assert len(events) == 1
    payload = events[0]["payload"]
    assert payload["task_id"] == task_id
    assert payload["judged_sha"] == judged_sha
    assert payload["live_head_sha"] == live_sha
    assert judged_sha[:12] in events[0]["summary"]
    assert live_sha[:12] in events[0]["summary"]
    # ⚖️⏱️ CMX-246 rework round 4, finding 3: role-pinned event summary. The payload
    # dict assertions above (`payload["judged_sha"]`/`payload["live_head_sha"]`) are
    # keyed, so they already survive a swap of the two f-string arguments feeding the
    # human-facing `summary` line — that swap is a SEPARATE piece of code and needs
    # its own pin. Swap `verified_sha`/`live_head` in the `event_log.append` f-string
    # and this goes red: the "superseded by" order flips.
    assert (f"verdict for {judged_sha[:12]} superseded by {live_sha[:12]}"
            in events[0]["summary"])
    assert (f"verdict for {live_sha[:12]} superseded by {judged_sha[:12]}"
            not in events[0]["summary"])


def test_a_blocking_verdict_for_the_CURRENT_head_still_spends_a_rework_round(tmp_path):
    """⚖️⏱️ CMX-246 BLOCKING guard: the ticket's own words are "the risk is that 'don't
    charge stale' becomes 'don't charge'". This is the ordinary case CMX-246 must not touch —
    the judge_sha this call actually tested still matches the PR's live head — pinned with
    EXPLICIT matching shas (not the defaults `_run_row` happens to supply, which would make
    this incidental coverage rather than a guard).

    Invert `stale_head` (e.g. `stale_head = not bool(...)`) and this goes red: a live,
    on-target blocking verdict would be treated as superseded and silently discarded instead
    of spending a rework round — the rework loop would stop counting entirely and a run could
    never reach `CHELA_MAX_REWORKS` or escalate."""
    task_id = "abc123"
    repo = _workflow_repo(tmp_path, task_id, FAKE_GUARD_TEST)
    with dispatcher._db() as conn:
        _run_row(conn, repo, task_id, judge_sha="samehead0001", pr_head_sha="samehead0001")
    exp_file = tmp_path / "experiments.json"
    exp_file.write_text(json.dumps({"experiments": [_exp()]}))
    posted: list[str] = []
    with patch.object(dispatcher, "_post_pr_comment",
                      side_effect=lambda url, d, body: (posted.append(body), (True, ""))[1]):
        result = judge.judge_run(task_id, exp_file, cleanup=False)

    assert result["state"] == judge.J_BLOCKED            # NOT J_STALE_HEAD
    run = dispatcher.resolve_run(task_id)
    assert run["status"] == "changes_requested"           # the carrier turned
    assert run["judge_state"] == judge.J_BLOCKED
    assert dispatcher.reviews_of(run)[-1]["verdict"] == "changes_requested"
    assert "SUPERSEDED" not in posted[0]                  # a live verdict gets no stale notice


def test_a_blocking_verdict_with_unreadable_shas_fails_CLOSED_and_still_charges(tmp_path):
    """⚖️⏱️ CMX-246 BLOCKING guard: the ticket was explicit — an UNKNOWN sha (either side
    unset, or unreadable) is not positive staleness evidence, and must charge the round
    exactly as today, mirroring `contract.merge`'s CMX-238 conservatism (refuse only on a
    KNOWN mismatch). Getting this backwards is the worst outcome available: a run whose shas
    cannot be read would silently stop being charged at all, forever.

    Here `judge_sha` and `pr_head_sha` are both unset, so `verified_sha` (the fallback chain)
    resolves to `None` — there is no answer to compare against, so this must NOT be treated
    as stale. Make unknown behave like stale (e.g. treat a falsy `verified_sha`/`live_head` as
    a match) and this goes red: the round silently stops being spent."""
    task_id = "abc123"
    repo = _workflow_repo(tmp_path, task_id, FAKE_GUARD_TEST)
    with dispatcher._db() as conn:
        _run_row(conn, repo, task_id, judge_sha=None, pr_head_sha=None)
    exp_file = tmp_path / "experiments.json"
    exp_file.write_text(json.dumps({"experiments": [_exp()]}))
    posted: list[str] = []
    with patch.object(dispatcher, "_post_pr_comment",
                      side_effect=lambda url, d, body: (posted.append(body), (True, ""))[1]):
        result = judge.judge_run(task_id, exp_file, cleanup=False)

    assert result["state"] == judge.J_BLOCKED            # NOT J_STALE_HEAD
    run = dispatcher.resolve_run(task_id)
    assert run["status"] == "changes_requested"           # the carrier turned — round charged
    assert run["judge_state"] == judge.J_BLOCKED
    assert dispatcher.reviews_of(run)[-1]["verdict"] == "changes_requested"


def test_a_clean_verdict_for_a_head_that_no_longer_exists_never_overwrites_judge_state(tmp_path):
    """The clean-path twin of the test above: a `clean` verdict about a superseded head must
    not clobber `judge_state`. A second judge spawned for the new head (the per-sha trigger)
    may already have recorded a DIFFERENT, newer verdict (here `blocked`) on this row — a
    stale `clean` landing after it would silently erase that finding, and the merge gate
    would then trust `clean` for a commit it never actually verified.

    Corrupt `stale_head` to `False` and this goes red: `set_judge_state` runs unconditionally
    and overwrites `judge_state` with the stale `clean`."""
    task_id = "abc123"
    repo = _workflow_repo(tmp_path, task_id, REAL_GUARD_TEST)
    with dispatcher._db() as conn:
        _run_row(conn, repo, task_id, judge_sha="oldsha000001", pr_head_sha="newsha000002",
                 judge_state=judge.J_BLOCKED, judge_detail="a newer judge already blocked this")
    exp_file = tmp_path / "experiments.json"
    exp_file.write_text(json.dumps({"experiments": [_exp()]}))
    with patch.object(dispatcher, "_post_pr_comment", return_value=(True, "")):
        result = judge.judge_run(task_id, exp_file, cleanup=False)

    assert result["state"] == judge.J_STALE_HEAD
    run = dispatcher.resolve_run(task_id)
    assert run["judge_state"] == judge.J_BLOCKED        # the NEWER verdict survives, untouched
    assert run["judge_sha"] == "oldsha000001"            # untouched


def test_a_stale_clean_verdicts_PR_comment_also_names_both_shas(tmp_path):
    """⚖️⏱️ CMX-246 rework, finding 2: `test_a_clean_verdict_for_a_head_that_no_longer_exists_
    never_overwrites_judge_state` above pins the DB-side half of the clean-stale path
    (`judge_state`/`judge_sha` left untouched) but never reads `posted` — so it cannot catch
    the announcement being dropped from the CLEAN branch's comment specifically. The blocking
    branch's notice is covered by `test_a_stale_verdict_announces_both_shas_on_the_PR_and_in_
    the_event_log`, which always takes the `if blocking:` arm — it cannot exercise the `else:`
    (clean) arm's own `if stale_head:` prefix at all.

    Disable the clean branch's `if stale_head:` prefix (e.g. `if False and stale_head:`) and
    this goes red: the clean verdict posts with no mention that it was superseded.

    ⚖️⏱️ CMX-246 rework round 5, finding 1: also pins the `judge.stale_head` EVENT for this
    same clean arm, not just the PR comment. The log/event announcement block above the
    `if blocking:`/`else:` split is written once and meant to cover BOTH arms — but until now
    only `test_a_stale_verdict_announces_both_shas_on_the_PR_and_in_the_event_log` (which
    always takes the `if blocking:` arm) ever read `event_log`. Gate that whole block on
    `stale_head and blocking` and this test still saw its PR comment (the clean arm's own,
    separate `if stale_head:` prefix at line ~1432 is untouched by that mutation) while `chela
    events` records nothing at all for a superseded CLEAN verdict — this goes red only with
    the event-log assertions below."""
    task_id = "abc123"
    judged_sha = "oldsha000001"
    live_sha = "newsha000002"
    repo = _workflow_repo(tmp_path, task_id, REAL_GUARD_TEST)
    with dispatcher._db() as conn:
        _run_row(conn, repo, task_id, judge_sha=judged_sha, pr_head_sha=live_sha)
    exp_file = tmp_path / "experiments.json"
    exp_file.write_text(json.dumps({"experiments": [_exp()]}))
    posted: list[str] = []
    with patch.object(dispatcher, "_post_pr_comment",
                      side_effect=lambda url, d, body: (posted.append(body), (True, ""))[1]):
        result = judge.judge_run(task_id, exp_file, cleanup=False)

    assert result["state"] == judge.J_STALE_HEAD
    assert posted
    assert judged_sha[:12] in posted[0]
    assert live_sha[:12] in posted[0]
    assert "SUPERSEDED" in posted[0]
    # ⚖️⏱️ CMX-246 rework round 4, finding 2: role-pinned, not just containment — the
    # twin of the assertions on the blocking branch's own, SEPARATE call site
    # (`_stale_head_notice(verified_sha, live_head)` under `else:`). Swap the two
    # arguments there and this test's plain `in` checks above stay green while a
    # human reading the PR is pointed at the wrong commit.
    assert f"this verdict is for `{judged_sha[:12]}`" in posted[0]
    assert f"a newer commit, `{live_sha[:12]}`," in posted[0]
    assert f"this verdict is for `{live_sha[:12]}`" not in posted[0]
    assert f"a newer commit, `{judged_sha[:12]}`," not in posted[0]

    # ⚖️⏱️ CMX-246 rework round 5, finding 1: the CLEAN arm gets a `judge.stale_head` event
    # too — this is the only test that ever spends a CLEAN, stale verdict AND reads
    # `event_log`, so it is the only guard on `if stale_head:` (vs. `if stale_head and
    # blocking:`) for the whole announcement block.
    events = event_log.read(types=["judge.stale_head"])["events"]
    assert len(events) == 1
    payload = events[0]["payload"]
    assert payload["task_id"] == task_id
    assert payload["judged_sha"] == judged_sha
    assert payload["live_head_sha"] == live_sha
    assert payload["verdict"] == judge.J_CLEAN
    assert (f"verdict for {judged_sha[:12]} superseded by {live_sha[:12]}"
            in events[0]["summary"])


def test_the_PRs_live_head_is_reread_right_before_the_verdict_is_spent_not_the_stale_in_memory_row(
    tmp_path,
):
    """⚖️⏱️ CMX-246 rework, finding 1: `run` is fetched ONCE at the top of `judge_run`, before
    the (possibly minutes-long) mutation battery runs. Every existing stale-head test sets the
    run row's `pr_head_sha` BEFORE calling `judge_run` — so `run` (captured at the very start)
    already carries the "new" head, and a mutation that swaps the live re-read
    (`dispatcher.resolve_run(task_id)`) for the stale in-memory `run` is invisible to them: both
    would read the same value.

    Here the new commit lands DURING the call — from inside `run_experiments`, standing in for
    a real commit landing on the PR while this judge's suite is still running — exactly the
    window CMX-246 exists to cover. `run` was captured before that update, so only a genuine
    live re-read observes the new head.

    Swap `live_run = dispatcher.resolve_run(task_id)` for `live_run = run` and this goes red:
    `live_head` reads the pre-update sha, matches `verified_sha`, `stale_head` is wrongly
    `False`, and the round is charged for a commit the PR no longer presents as its head."""
    task_id = "abc123"
    repo = _workflow_repo(tmp_path, task_id, FAKE_GUARD_TEST)
    with dispatcher._db() as conn:
        _run_row(conn, repo, task_id, judge_sha=None, pr_head_sha="oldsha000001")
    exp_file = tmp_path / "experiments.json"
    exp_file.write_text(json.dumps({"experiments": [_exp()]}))

    real_run_experiments = judge.run_experiments

    def _land_a_new_commit_mid_run(*a, **kw):
        with dispatcher._db() as conn:
            conn.execute(
                "UPDATE runs SET pr_head_sha=? WHERE task_id=?", ("newsha000002", task_id),
            )
            conn.commit()
        return real_run_experiments(*a, **kw)

    posted: list[str] = []
    with patch.object(judge, "run_experiments", side_effect=_land_a_new_commit_mid_run), \
         patch.object(dispatcher, "_post_pr_comment",
                      side_effect=lambda url, d, body: (posted.append(body), (True, ""))[1]):
        result = judge.judge_run(task_id, exp_file, cleanup=False)

    assert result["state"] == judge.J_STALE_HEAD
    run = dispatcher.resolve_run(task_id)
    assert run["pr_head_sha"] == "newsha000002"      # the mid-run update really landed
    assert run["status"] == "awaiting_review"         # never moved to changes_requested
    assert (run["rework_count"] or 0) == 0             # ⛔ no round spent
    assert not dispatcher.reviews_of(run)              # request_changes was never called
    assert posted and "oldsha000001"[:12] in posted[0]
    assert "newsha000002"[:12] in posted[0]


def test_a_blocking_verdict_with_an_unreadable_LIVE_head_still_charges(tmp_path):
    """⚖️⏱️ CMX-246 rework round 3, finding 1: an UNKNOWN live head is not positive staleness
    evidence — same conservatism as `test_a_blocking_verdict_with_unreadable_shas_fails_
    CLOSED_and_still_charges`, but that test leaves BOTH operands unset, so it cannot tell
    `stale_head = bool(verified_sha and live_head and verified_sha != live_head)` apart from
    `bool(verified_sha and verified_sha != live_head)` — drop the `live_head and` conjunct and
    both still evaluate `False` there, because `verified_sha` is also `None`.

    Here `verified_sha` IS known (the run's `pr_head_sha` at the top of the call) and only the
    LIVE re-read comes back unreadable — the PR's head goes missing mid-run, standing in for a
    live read that fails. Drop the `live_head and` conjunct and this goes red: `None !=
    verified_sha` is True, so a genuinely blocking verdict for a head this call cannot
    re-confirm gets silently discarded as 'stale' instead of charged."""
    task_id = "abc123"
    repo = _workflow_repo(tmp_path, task_id, FAKE_GUARD_TEST)
    with dispatcher._db() as conn:
        _run_row(conn, repo, task_id, judge_sha=None, pr_head_sha="knownsha0001")
    exp_file = tmp_path / "experiments.json"
    exp_file.write_text(json.dumps({"experiments": [_exp()]}))

    real_run_experiments = judge.run_experiments

    def _blank_the_live_head_mid_run(*a, **kw):
        with dispatcher._db() as conn:
            conn.execute("UPDATE runs SET pr_head_sha=? WHERE task_id=?", (None, task_id))
            conn.commit()
        return real_run_experiments(*a, **kw)

    posted: list[str] = []
    with patch.object(judge, "run_experiments", side_effect=_blank_the_live_head_mid_run), \
         patch.object(dispatcher, "_post_pr_comment",
                      side_effect=lambda url, d, body: (posted.append(body), (True, ""))[1]):
        result = judge.judge_run(task_id, exp_file, cleanup=False)

    assert result["state"] == judge.J_BLOCKED             # NOT J_STALE_HEAD
    run = dispatcher.resolve_run(task_id)
    assert run["status"] == "changes_requested"            # the carrier turned — round charged
    assert run["judge_state"] == judge.J_BLOCKED
    assert dispatcher.reviews_of(run)[-1]["verdict"] == "changes_requested"
    assert "SUPERSEDED" not in posted[0]


def test_a_blocking_verdict_with_an_unreadable_JUDGED_sha_still_charges(tmp_path):
    """⚖️⏱️ CMX-246 rework round 3, finding 2: the twin of the test above — an UNKNOWN judged
    sha is not positive staleness evidence either. `verified_sha` stays `None` for this whole
    call (both `judge_sha` and the run's `pr_head_sha` at call-start are unset), while the
    LIVE re-read comes back KNOWN partway through, standing in for a PR that only just
    acquired a head. Drop the `verified_sha and` conjunct (e.g. `stale_head = bool(live_head
    and verified_sha != live_head)`) and this goes red: `None != live_head` is always True, so
    a genuinely blocking verdict whose own judged sha could not be read gets silently
    discarded as 'stale' instead of charged."""
    task_id = "abc123"
    repo = _workflow_repo(tmp_path, task_id, FAKE_GUARD_TEST)
    with dispatcher._db() as conn:
        _run_row(conn, repo, task_id, judge_sha=None, pr_head_sha=None)
    exp_file = tmp_path / "experiments.json"
    exp_file.write_text(json.dumps({"experiments": [_exp()]}))

    real_run_experiments = judge.run_experiments

    def _reveal_the_live_head_mid_run(*a, **kw):
        with dispatcher._db() as conn:
            conn.execute(
                "UPDATE runs SET pr_head_sha=? WHERE task_id=?", ("nowknown0001", task_id),
            )
            conn.commit()
        return real_run_experiments(*a, **kw)

    posted: list[str] = []
    with patch.object(judge, "run_experiments", side_effect=_reveal_the_live_head_mid_run), \
         patch.object(dispatcher, "_post_pr_comment",
                      side_effect=lambda url, d, body: (posted.append(body), (True, ""))[1]):
        result = judge.judge_run(task_id, exp_file, cleanup=False)

    assert result["state"] == judge.J_BLOCKED             # NOT J_STALE_HEAD
    run = dispatcher.resolve_run(task_id)
    assert run["status"] == "changes_requested"            # the carrier turned — round charged
    assert run["judge_state"] == judge.J_BLOCKED
    assert dispatcher.reviews_of(run)[-1]["verdict"] == "changes_requested"
    assert "SUPERSEDED" not in posted[0]


def test_stale_head_notice_names_the_shas_in_the_CORRECT_roles():
    """⚖️⏱️ CMX-246 rework round 3, finding 3: `_stale_head_notice` must say the JUDGED sha
    "this verdict is for" and the LIVE head is "a newer commit" — swapped, a human is pointed
    at the wrong commit, which is the exact hand-triage this ticket exists to stop. The
    end-to-end stale-head tests above only assert both shas appear `in` the posted comment
    body, which is blind to which sha lands in which role — swap the two f-string arguments
    and every one of those still passes.

    Swap `judged_sha`/`live_head` in the two f-strings and this goes red: `judged_sha` no
    longer appears right after "this verdict is for", and `live_head` no longer appears right
    after "a newer commit,"."""
    notice = judge._stale_head_notice("judgedsha0001", "livehead0002")

    assert "this verdict is for `judgedsha000`" in notice
    assert "a newer commit, `livehead0002`," in notice
    assert "this verdict is for `livehead0002`" not in notice
    assert "a newer commit, `judgedsha000`," not in notice


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


# --- (h) THE REAP (CMX-164): the throwaway worktree is gone whatever happened -----------

def _judge_worktree_path(tmp_path: Path, task_id: str) -> Path:
    return tmp_path / ".chela" / "wts" / f"judge-{task_id}"


def test_a_judge_run_that_FINISHES_reaps_its_own_worktree(tmp_path, monkeypatch):
    """Before CMX-164, NOTHING removed the judge's throwaway worktree — the directory
    persisted after every judged PR (3 found, ~100-143 MB each, live audit 2026-07-23).
    `cleanup=True` — the judge agent's real, final call — must remove it on the ordinary
    clean-verdict path."""
    monkeypatch.setattr(dispatcher, "_kill_windows_named", lambda name: None)
    task_id = "abc123"
    repo = _workflow_repo(tmp_path, task_id, REAL_GUARD_TEST, linked=True)
    with dispatcher._db() as conn:
        _run_row(conn, repo, task_id)
    exp_file = tmp_path / "experiments.json"
    exp_file.write_text(json.dumps({"experiments": [_exp()]}))
    wt_path = _judge_worktree_path(tmp_path, task_id)
    assert wt_path.is_dir()

    with patch.object(dispatcher, "_post_pr_comment", return_value=(True, "")):
        result = judge.judge_run(task_id, exp_file, cleanup=True)

    assert result["state"] == judge.J_CLEAN
    assert not wt_path.exists()


def test_a_judge_run_that_RAISES_still_reaps_its_worktree(tmp_path, monkeypatch):
    """⛔ Drop the `finally` (make cleanup a happy-path-only call again) and this goes RED:
    an exception mid-judgment must not leak the directory forever — the worktree is reaped
    whether `judge_run` finishes OR blows up."""
    monkeypatch.setattr(dispatcher, "_kill_windows_named", lambda name: None)
    task_id = "abc123"
    repo = _workflow_repo(tmp_path, task_id, REAL_GUARD_TEST, linked=True)
    with dispatcher._db() as conn:
        _run_row(conn, repo, task_id)
    exp_file = tmp_path / "experiments.json"
    exp_file.write_text(json.dumps({"experiments": [_exp()]}))
    wt_path = _judge_worktree_path(tmp_path, task_id)
    assert wt_path.is_dir()

    def _boom(*a, **kw):
        raise RuntimeError("the suite subprocess blew up")

    monkeypatch.setattr(judge, "run_experiments", _boom)

    with pytest.raises(RuntimeError, match="blew up"):
        judge.judge_run(task_id, exp_file, cleanup=True)

    assert not wt_path.exists()


def test_a_stale_judge_never_deletes_the_worktree_a_newer_judge_now_owns(tmp_path, monkeypatch):
    """⚖️🕳️ CMX-221: `judge_worktree_path` is keyed only by `task_id` — two judge calls for
    the same task land on the identical directory and window name. If the watchdog ever
    declares a slow-but-alive judge dead and respawns a replacement while the first call is
    still mid-flight (bumping `judge_window_epoch`, the same CAS `_launch_agent` stamps on
    every spawn), the first call's `_cleanup` must NOT delete the replacement's live
    worktree, and must NOT kill its window, just because it finishes first.

    Simulated by bumping `judge_window_epoch` partway through this call's `run_experiments`
    — exactly what a real respawn does while this call is off running the suite."""
    killed = []
    monkeypatch.setattr(dispatcher, "_kill_windows_named", lambda name: killed.append(name))
    task_id = "abc123"
    repo = _workflow_repo(tmp_path, task_id, REAL_GUARD_TEST)
    with dispatcher._db() as conn:
        _run_row(conn, repo, task_id, judge_window_epoch="epoch-A")
    exp_file = tmp_path / "experiments.json"
    exp_file.write_text(json.dumps({"experiments": [_exp()]}))
    wt_path = _judge_worktree_path(tmp_path, task_id)
    assert wt_path.is_dir()

    real_run_experiments = judge.run_experiments

    def _respawn_mid_flight(*a, **kw):
        with dispatcher._db() as conn:
            conn.execute(
                "UPDATE runs SET judge_window_epoch=? WHERE task_id=?", ("epoch-B", task_id),
            )
            conn.commit()
        return real_run_experiments(*a, **kw)

    monkeypatch.setattr(judge, "run_experiments", _respawn_mid_flight)

    with patch.object(dispatcher, "_post_pr_comment", return_value=(True, "")):
        result = judge.judge_run(task_id, exp_file, cleanup=True)

    assert result["state"] == judge.J_CLEAN
    assert wt_path.is_dir()          # ⛔ NOT reaped — a newer judge owns the slot now
    assert killed == []              # ⛔ its window was not killed either


def test_a_second_judge_for_the_same_task_refuses_while_the_first_is_still_running(
    tmp_path, monkeypatch,
):
    """⚖️🕳️ CMX-221 round 2: OBJECTIVE 1 asked for EXCLUSIVE execution — a second judge for
    the same task must REFUSE to start, not just skip cleanup once it's done. The collision
    the ticket names explicitly: a dispatcher-launched judge (which stamps
    `judge_window_epoch` at spawn) is still running when an operator's bare `chela judge run`
    targets the same task — the CLI path never stamps that column, it only reads it, so both
    calls carry the identical epoch and the cleanup-only CAS above cannot see this at all.

    Simulated the same way as the epoch test above: a nested `judge_run` call, fired from
    inside the first call's `run_experiments`, stands in for the concurrent CLI invocation —
    pytest is single-threaded, so this is the only way to get one call genuinely mid-flight
    while a second one starts."""
    task_id = "abc123"
    repo = _workflow_repo(tmp_path, task_id, REAL_GUARD_TEST, linked=True)
    with dispatcher._db() as conn:
        _run_row(conn, repo, task_id, judge_window_epoch="epoch-A")  # dispatcher-launched
    exp_file = tmp_path / "experiments.json"
    exp_file.write_text(json.dumps({"experiments": [_exp()]}))
    wt_path = _judge_worktree_path(tmp_path, task_id)

    real_run_experiments = judge.run_experiments
    nested = {}

    def _cli_alongside_dispatched(*a, **kw):
        with patch.object(dispatcher, "_post_pr_comment", return_value=(True, "")):
            nested["result"] = judge.judge_run(task_id, exp_file, cleanup=True)
        assert wt_path.is_dir()          # ⛔ the refused call must not have touched it
        return real_run_experiments(*a, **kw)

    monkeypatch.setattr(judge, "run_experiments", _cli_alongside_dispatched)

    with patch.object(dispatcher, "_post_pr_comment", return_value=(True, "")):
        result = judge.judge_run(task_id, exp_file, cleanup=True)

    assert nested["result"]["ok"] is False
    assert "already running" in nested["result"]["error"]
    assert result["state"] == judge.J_CLEAN      # the FIRST call still completes normally
    assert not wt_path.exists()                  # …and reaps its own worktree as usual


def test_no_live_claim_the_judge_starts_and_claims_normally(tmp_path, monkeypatch):
    """⭐ COUNTERWEIGHT to the refusal test above — an "always refuse" bug would pass that
    test trivially. With no pre-existing claim, `judge_run` must proceed exactly as before,
    and release its own claim when it finishes so nothing leaks for the next run."""
    monkeypatch.setattr(dispatcher, "_kill_windows_named", lambda name: None)
    task_id = "abc123"
    repo = _workflow_repo(tmp_path, task_id, REAL_GUARD_TEST)
    with dispatcher._db() as conn:
        _run_row(conn, repo, task_id)
    exp_file = tmp_path / "experiments.json"
    exp_file.write_text(json.dumps({"experiments": [_exp()]}))
    wt_path = _judge_worktree_path(tmp_path, task_id)
    lock_path = wt_path.parent / f".{wt_path.name}.judgelock"
    assert not lock_path.exists()

    with patch.object(dispatcher, "_post_pr_comment", return_value=(True, "")):
        result = judge.judge_run(task_id, exp_file, cleanup=True)

    assert result["ok"] is True
    assert result["state"] == judge.J_CLEAN
    assert not lock_path.exists()   # released when the call finished — nothing leaked


def test_a_stale_judge_lock_is_taken_over_not_wedged_forever(tmp_path, monkeypatch):
    """⭐ COUNTERWEIGHT — a claim whose owner is provably gone must be TAKEN OVER, never
    treated as permanently exclusive. A judge that crashed mid-run (and so never reached its
    own release) must not wedge every future attempt at this task's judge slot forever."""
    monkeypatch.setattr(dispatcher, "_kill_windows_named", lambda name: None)
    task_id = "abc123"
    repo = _workflow_repo(tmp_path, task_id, REAL_GUARD_TEST)
    with dispatcher._db() as conn:
        _run_row(conn, repo, task_id)
    exp_file = tmp_path / "experiments.json"
    exp_file.write_text(json.dumps({"experiments": [_exp()]}))
    wt_path = _judge_worktree_path(tmp_path, task_id)
    lock_path = wt_path.parent / f".{wt_path.name}.judgelock"

    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()                      # reaped: this pid is now provably gone
    lock_path.write_text(json.dumps({"pid": dead.pid, "started": 1.0, "task_id": task_id}))

    with patch.object(dispatcher, "_post_pr_comment", return_value=(True, "")):
        result = judge.judge_run(task_id, exp_file, cleanup=True)

    assert result["ok"] is True          # ⛔ NOT wedged — the dead claim was taken over
    assert result["state"] == judge.J_CLEAN
    assert not lock_path.exists()        # this call released its OWN (new) claim when done


def test_judge_lock_owner_alive_rejects_a_recycled_pid_with_a_stale_start_time():
    """⚖️🕳️ CMX-221 round 2 mutation kill: the judge found `_judge_lock_owner_alive`'s
    `return abs(live_started - started) < 1.0` collapsible to `return True` with the whole
    suite still green — every other test only ever hands it a pid that is either the calling
    process itself (so `started` trivially matches) or one that's truly dead (so it falls
    into the `os.kill` branch, never reaching the comparison at all). Neither shape exercises
    the comparison the mutation deleted.

    This pins it directly: a REAL, currently-live process, but with a recorded `started` that
    does not match its actual `/proc` start time — the exact shape of CMX-219's pid-recycling
    bug (the old owner that claimed this pid died; a new, unrelated process now holds it). The
    pid existing is not enough — identity must be proven by start time, or the claim must be
    refused as belonging to someone else."""
    from chela import sessions

    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
    try:
        live_started = sessions.proc_started(proc.pid)
        assert live_started is not None

        recycled = {"pid": proc.pid, "started": live_started - 100.0}
        assert judge._judge_lock_owner_alive(recycled) is False   # ⛔ different owner, same pid

        genuine = {"pid": proc.pid, "started": live_started}
        assert judge._judge_lock_owner_alive(genuine) is True     # ⭐ COUNTERWEIGHT: real match
    finally:
        proc.kill()
        proc.wait()


# --- (h.5) THE RE-RUN (CMX-201): a REAPED worktree is rebuilt, not declared unverifiable ---

def _git_workflow_repo(tmp_path: Path, task_id: str, guard_test: str) -> tuple[Path, str]:
    """A real git repo that is BOTH the workflow repo and the source `detached_worktree`
    checks the judge's throwaway worktree out from. Deliberately does NOT pre-provision
    ``.chela/wts/judge-{task_id}`` — that directory must not exist yet."""
    repo = tmp_path / "repo"
    _project(repo, guard_test=guard_test)
    (repo / "WORKFLOW.md").write_text(
        "---\n"
        "project_key: TEST\n"
        "tracker:\n  kind: markdown\n  path: TODO.md\n"
        f"workspace:\n  root: {tmp_path / '.chela' / 'wts'}\n  base_branch: dev\n"
        f"judge:\n  test_cmd: {json.dumps(TEST_CMD)}\n  suite_timeout_seconds: 120\n"
        "---\n\ndo the thing: {{task_title}}\n"
    )
    (repo / "TODO.md").write_text("- [ ] do a thing\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "workflow files")
    sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    return repo, sha


def test_a_judge_run_with_no_worktree_REBUILDS_it_from_pr_head_sha(tmp_path, monkeypatch):
    """⚖️🕳️ CMX-201: a published verdict reaps its worktree (CMX-164). Before this, a PR
    that fixed exactly the guard a `blocked` verdict named had NO way back to `clean` short
    of a whole new dispatch round — `judge_run` declared the fix unverifiable because the
    directory was gone. It must instead rebuild its own throwaway checkout at the run's
    CURRENT `pr_head_sha` and actually re-adjudicate.

    ⭐ `judge_sha` is deliberately set to a DIFFERENT, real, resolvable commit — the stale sha
    the old `blocked` verdict was recorded against, from BEFORE the guard was fixed. If the
    rebuild ever preferred `judge_sha` over `pr_head_sha` it would check out the pre-fix
    commit, the FAKE guard there can't catch the mutation, and this would come back BLOCKED
    instead of CLEAN — so the wrong-sha bug is caught by the verdict itself, not just by
    inspecting the checkout."""
    monkeypatch.setattr(dispatcher, "_kill_windows_named", lambda name: None)
    task_id = "abc123"
    repo, stale_sha = _git_workflow_repo(tmp_path, task_id, FAKE_GUARD_TEST)
    (repo / "test_guard.py").write_text(REAL_GUARD_TEST)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "fix the guard the blocked verdict named")
    sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert sha != stale_sha

    wt_path = _judge_worktree_path(tmp_path, task_id)
    assert not wt_path.exists()                      # the worktree is GONE, not merely stale

    with dispatcher._db() as conn:
        _run_row(conn, repo, task_id, pr_head_sha=sha, judge_sha=stale_sha)
    exp_file = tmp_path / "experiments.json"
    exp_file.write_text(json.dumps({"experiments": [_exp()]}))

    with patch.object(dispatcher, "_post_pr_comment", return_value=(True, "")):
        result = judge.judge_run(task_id, exp_file, cleanup=False)

    assert result["state"] == judge.J_CLEAN
    assert result["cannot_verify"] == ""
    assert wt_path.is_dir()                           # it built its OWN throwaway checkout
    checked_out = subprocess.run(
        ["git", "-C", str(wt_path), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert checked_out == sha                         # CURRENT head, never the stale judge_sha
    run = dispatcher.resolve_run(task_id)
    assert run["judge_sha"] == sha                     # stamped — no longer stale


def test_a_rebuilt_worktree_gets_the_same_base_branch_catch_up_a_fresh_spawn_gets(
    tmp_path, monkeypatch,
):
    """⚖️🕳️ CMX-201/CMX-176 wiring: `_reprovision_worktree` ends by calling the SAME
    `dispatcher._refresh_judge_worktree` catch-up `_spawn_judge` runs before a FRESH judge
    ever sees the tree — so a re-run measures the PR exactly as a fresh judge would. Corrupt
    that call away (``return ""`` instead of calling it) and the rebuild silently keeps the
    PR branch's STALE tip: a fix that landed on base after the claim would be absent from the
    baseline, the exact hole CMX-176 was filed to close for a freshly spawned judge.

    ⛔ This needs a REAL `origin` remote. Every OTHER rebuild test in this file builds its
    repo with `_git_workflow_repo`, which has none — `_refresh_judge_worktree`'s own first
    line (``git fetch origin`` failing with no remote configured) short-circuits before it
    can do anything, so a real call is indistinguishable from the corrupted ``return ""`` in
    every one of those fixtures. This is why they didn't catch it.
    """
    monkeypatch.setattr(dispatcher, "_kill_windows_named", lambda name: None)
    task_id = "abc123"

    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "dev", str(origin)], check=True, capture_output=True,
    )
    repo = tmp_path / "repo"
    subprocess.run(["git", "clone", str(origin), str(repo)], check=True, capture_output=True)
    (repo / "guard.py").write_text(GUARD_PY)
    (repo / "test_guard.py").write_text(REAL_GUARD_TEST)
    (repo / "app.py").write_text("VALUE = 1\n")
    (repo / "WORKFLOW.md").write_text(
        "---\n"
        "project_key: TEST\n"
        "tracker:\n  kind: markdown\n  path: TODO.md\n"
        f"workspace:\n  root: {tmp_path / '.chela' / 'wts'}\n  base_branch: dev\n"
        f"judge:\n  test_cmd: {json.dumps(TEST_CMD)}\n  suite_timeout_seconds: 120\n"
        "---\n\ndo the thing: {{task_title}}\n"
    )
    (repo / "TODO.md").write_text("- [ ] do a thing\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "seed")
    _git(repo, "push", "-u", "origin", "dev")

    # The PR branch is cut HERE — before base moves on underneath it.
    _git(repo, "branch", "pr-1")
    sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "pr-1"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()

    # base_branch moves on after the claim — same shape as test_judge_branch_refresh.py.
    (repo / "app.py").write_text("VALUE = 2\n")
    _git(repo, "commit", "-am", "fix landed on base after the claim")
    _git(repo, "push", "origin", "dev")

    wt_path = _judge_worktree_path(tmp_path, task_id)
    assert not wt_path.exists()                      # the worktree is GONE, not merely stale

    with dispatcher._db() as conn:
        _run_row(conn, repo, task_id, pr_head_sha=sha)
    exp_file = tmp_path / "experiments.json"
    exp_file.write_text(json.dumps({"experiments": [_exp()]}))

    with patch.object(dispatcher, "_post_pr_comment", return_value=(True, "")):
        result = judge.judge_run(task_id, exp_file, cleanup=False)

    assert result["state"] == judge.J_CLEAN
    assert result["cannot_verify"] == ""
    assert wt_path.is_dir()
    # ⭐ the property this test exists for: the REBUILT worktree got base's post-claim fix,
    # the same catch-up a fresh `_spawn_judge` runs. Corrupt the call to `return ""` (skip the
    # catch-up) and this reads "VALUE = 1\n" — the PR branch's stale tip — instead.
    assert (wt_path / "app.py").read_text() == "VALUE = 2\n"
    run = dispatcher.resolve_run(task_id)
    assert run["judge_sha"] == sha


def test_a_rebuilt_worktree_still_blocks_a_guard_that_SURVIVES(tmp_path, monkeypatch):
    """The rebuild path is not a rubber stamp — a guard that still cannot fail sends the PR
    back exactly as it would from a freshly `_spawn_judge`d worktree."""
    monkeypatch.setattr(dispatcher, "_kill_windows_named", lambda name: None)
    task_id = "abc123"
    repo, sha = _git_workflow_repo(tmp_path, task_id, FAKE_GUARD_TEST)
    assert not _judge_worktree_path(tmp_path, task_id).exists()

    with dispatcher._db() as conn:
        _run_row(conn, repo, task_id, pr_head_sha=sha)
    exp_file = tmp_path / "experiments.json"
    exp_file.write_text(json.dumps({"experiments": [_exp()]}))

    with patch.object(dispatcher, "_post_pr_comment", return_value=(True, "")):
        result = judge.judge_run(task_id, exp_file, cleanup=False)

    assert result["state"] == judge.J_BLOCKED
    run = dispatcher.resolve_run(task_id)
    assert run["status"] == "changes_requested"
    assert run["judge_sha"] == sha


def test_a_missing_worktree_with_no_pr_head_sha_is_still_CANNOT_VERIFY(tmp_path, monkeypatch):
    """Nothing to rebuild FROM — this must stay an honest unknown, never a guess."""
    monkeypatch.setattr(dispatcher, "_kill_windows_named", lambda name: None)
    task_id = "abc123"
    repo, _sha = _git_workflow_repo(tmp_path, task_id, REAL_GUARD_TEST)
    assert not _judge_worktree_path(tmp_path, task_id).exists()

    with dispatcher._db() as conn:
        _run_row(conn, repo, task_id, pr_head_sha=None)
    exp_file = tmp_path / "experiments.json"
    exp_file.write_text(json.dumps({"experiments": [_exp()]}))

    with patch.object(dispatcher, "_post_pr_comment", return_value=(True, "")):
        result = judge.judge_run(task_id, exp_file, cleanup=False)

    assert result["state"] == judge.J_CANNOT_VERIFY
    assert "no pr_head_sha" in result["cannot_verify"]
    run = dispatcher.resolve_run(task_id)
    assert run["judge_sha"] is None                   # nothing was rebuilt, nothing is stamped


def test_a_missing_worktree_at_an_unresolvable_sha_is_CANNOT_VERIFY_not_a_crash(tmp_path, monkeypatch):
    """`pr_head_sha` pointing nowhere in the repo (a force-push race, a bad row) must fail
    LOUD and named — never raise out of `judge_run`, never be silently treated as clean."""
    monkeypatch.setattr(dispatcher, "_kill_windows_named", lambda name: None)
    task_id = "abc123"
    repo, _sha = _git_workflow_repo(tmp_path, task_id, REAL_GUARD_TEST)
    assert not _judge_worktree_path(tmp_path, task_id).exists()

    bogus_sha = "f" * 40
    with dispatcher._db() as conn:
        _run_row(conn, repo, task_id, pr_head_sha=bogus_sha)
    exp_file = tmp_path / "experiments.json"
    exp_file.write_text(json.dumps({"experiments": [_exp()]}))

    with patch.object(dispatcher, "_post_pr_comment", return_value=(True, "")):
        result = judge.judge_run(task_id, exp_file, cleanup=False)

    assert result["state"] == judge.J_CANNOT_VERIFY
    assert "could not be rebuilt" in result["cannot_verify"]
    run = dispatcher.resolve_run(task_id)
    assert run["judge_sha"] is None


def test_a_rebuild_that_hits_an_OSError_is_CANNOT_VERIFY_not_a_crash(tmp_path, monkeypatch):
    """`_reprovision_worktree`'s except clause was widened to `(BranchGone,
    CalledProcessError, OSError)` on review (CMX-201 PR #262, round 1: 'a reaped parent
    directory, a full disk... would escape as a crash rather than the cannot_verify this
    function exists to produce') and shipped with NO test — both the round-3 and final
    judge rounds flagged it as a production change nothing holds in place. `OSError` is not
    a subclass of `CalledProcessError`, so dropping it back out of the tuple is silent: the
    fixture never made `detached_worktree` raise anything but `CalledProcessError`
    (test_a_missing_worktree_at_an_unresolvable_sha...) or nothing at all."""
    monkeypatch.setattr(dispatcher, "_kill_windows_named", lambda name: None)
    task_id = "abc123"
    repo, sha = _git_workflow_repo(tmp_path, task_id, REAL_GUARD_TEST)
    assert not _judge_worktree_path(tmp_path, task_id).exists()

    def _boom(*a, **k):
        raise OSError("no space left on device")

    monkeypatch.setattr(dispatcher, "detached_worktree", _boom)

    with dispatcher._db() as conn:
        _run_row(conn, repo, task_id, pr_head_sha=sha)
    exp_file = tmp_path / "experiments.json"
    exp_file.write_text(json.dumps({"experiments": [_exp()]}))

    with patch.object(dispatcher, "_post_pr_comment", return_value=(True, "")):
        result = judge.judge_run(task_id, exp_file, cleanup=False)

    assert result["state"] == judge.J_CANNOT_VERIFY
    assert "could not be rebuilt" in result["cannot_verify"]
    assert "no space left on device" in result["cannot_verify"]
    run = dispatcher.resolve_run(task_id)
    assert run["judge_sha"] is None                   # nothing was rebuilt, nothing is stamped
    assert not _judge_worktree_path(tmp_path, task_id).exists()


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


def _tick(wf, spawned, checks=dispatcher.CI_PASSING, sha="cafe1234", windows=(),
          open_ids=("abc123",)):
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
         patch.object(dispatcher, "get_source", return_value=_EmptySource(open_ids)), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher.subprocess, "run", side_effect=fake_run), \
         patch.object(dispatcher, "_spawn_judge", side_effect=spawned), \
         patch.object(dispatcher, "remove_worktree", return_value=True), \
         patch.object(dispatcher, "_failing_log_tail", return_value=""), \
         patch.object(dispatcher, "_respawn_rework", return_value=False):
        return dispatcher.tick(wf.path)


class _EmptySource:
    def __init__(self, open_ids=("abc123",)):
        self._open_ids = open_ids

    def list_open_tasks(self):
        from chela.sources import Task
        return [Task(id=task_id, title="do a thing", file="TODO.md", line_number=1,
                     raw="- [ ] do a thing") for task_id in self._open_ids]


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

    ⚖️🧊 CMX-253: "settles for a human" must be a REAL transition, not a description of
    where the row silently stays. Once the budget is spent it moves to `needs_human` — see
    `_escalate_stranded_judge_unknowns` — rather than sitting in `awaiting_review` forever
    looking exactly like a run still waiting on an ordinary review.
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
    assert run["status"] == "needs_human"           # ⛔ never merged, never blocked —
    # but NOT stranded in `awaiting_review` either: the budget is spent, so a human is told.
    assert "could not verify" in (run["last_error"] or "")


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
        # ⚖️🧊 CMX-239: also a real, definitive verdict (a guard SURVIVED corruption) — just
        # one the run row never recorded because the CAS lost the race. Re-judging the SAME
        # commit would only re-discover a fact this call already has; it is not an unknown
        # and must not spend the cannot_verify retry budget re-proving it.
        (judge.J_BLOCKED_RACE, 0, False),
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


def test_a_cannot_verify_past_budget_escalates_instead_of_stranding_silently(tmp_path, monkeypatch):
    """⚖️🧊 CMX-253. Once `judge_cannot_verify_tries` reaches the bound on the SAME
    `pr_head_sha`, the trigger query never re-selects the row again — and `cannot_verify`
    never earns a `request_changes` escalation either (it "blocks nothing and approves
    nothing"). Before this fix nothing else moved the row: it sat in `awaiting_review`
    forever, presenting as an ordinary run still waiting for review. It must instead move to
    `needs_human`, with the judge's own last detail carried into `last_error` for the human
    who picks it up.
    """
    monkeypatch.setenv("CHELA_JUDGE_MAX_UNKNOWN_RETRIES", "2")
    wf = _wf(tmp_path)

    def spawn(w, row, sha, conn):
        return True    # never reached — the budget is already spent

    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, workflow_path=str(wf.path), pr_head_sha=None,
                 judge_sha="cafe1234", judge_state=judge.J_CANNOT_VERIFY,
                 judge_cannot_verify_tries=2, judge_detail="a flake")

    result = _tick(wf, spawn)
    assert result["judge_stranded"] == 1
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "needs_human"
    assert run["judge_state"] == judge.J_CANNOT_VERIFY   # the verdict itself is untouched
    assert "a flake" in run["last_error"]
    assert "cafe1234"[:12] in run["last_error"]


def test_a_row_already_moved_on_is_not_re_escalated_every_tick(tmp_path, monkeypatch):
    """⚖️🧊 CMX-253 Objective 1, negative control. `_escalate_stranded_judge_unknowns` must
    be scoped to `status='awaiting_review'` — a row that already left that status (escalated
    to `needs_human` on a prior tick, or since merged/closed) still matches every OTHER arm
    of the query forever (same `judge_state`, same `judge_sha=pr_head_sha`, `tries` still at
    the bound), so a query that dropped the status filter would re-`_escalate` it on EVERY
    subsequent tick — clobbering `last_error`/`ended_at` on a run a human (or the merge path)
    already resolved, and re-notifying the inbox for a decision that was already made."""
    monkeypatch.setenv("CHELA_JUDGE_MAX_UNKNOWN_RETRIES", "2")
    wf = _wf(tmp_path)

    def spawn(w, row, sha, conn):
        return True    # never reached — status is not awaiting_review

    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, workflow_path=str(wf.path), status="merged",
                 pr_head_sha="cafe1234", judge_sha="cafe1234",
                 judge_state=judge.J_CANNOT_VERIFY, judge_cannot_verify_tries=2,
                 judge_detail="a flake", ended_at="2026-07-14T11:00:00+00:00",
                 last_error="already resolved, do not touch")

    result = _tick(wf, spawn)
    assert result["judge_stranded"] == 0
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "merged"                       # ⛔ not clobbered back to needs_human
    assert run["ended_at"] == "2026-07-14T11:00:00+00:00"  # ⛔ untouched
    assert run["last_error"] == "already resolved, do not touch"


def test_a_HELD_workflow_still_escalates_a_stranded_judge_unknown(tmp_path, monkeypatch):
    """⚖️🧊 CMX-253 Objective 1 placement. `_escalate_stranded_judge_unknowns` sits ABOVE the
    `blocked`/`hold` returns on purpose — dispatcher.py's own ⛔ comment on step 1e′ says it
    "starts no agent, takes no slot". Escalation is not a claim, so a paused queue (or a
    broken WORKFLOW.md) must not also silence the one transition that un-strands a run that
    already spent its retry budget — the failure mode `test_a_HOLD_pauses_the_rework_but_
    NEVER_the_escalation` already pins for the 1d rework-cap escalation.

    Every other stranded-judge test drives an ordinary unheld tick, so nothing pinned this
    placement: gating the 1e′ call behind `blocked or hold.active()` — i.e. moving it
    conceptually below the hold/blocked returns — would leave the run stuck in
    `awaiting_review` forever, indistinguishable from one still waiting on review, while the
    queue is paused.
    """
    monkeypatch.setenv("CHELA_JUDGE_MAX_UNKNOWN_RETRIES", "2")
    wf = _wf(tmp_path)
    held = hold.Hold(reason="rewriting the queue", by="liav", pid=1,
                      created_at=time.time(), expires_at=time.time() + 3600)

    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, workflow_path=str(wf.path), pr_head_sha=None,
                 judge_sha="cafe1234", judge_state=judge.J_CANNOT_VERIFY,
                 judge_cannot_verify_tries=2, judge_detail="a flake")

    def spawn(w, row, sha, conn):
        return True    # never reached — the budget is already spent, and the queue is held

    from chela.workflow import WorkflowStatus

    class R:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, *a, **k):
        r = R()
        if isinstance(cmd, list) and cmd[:2] == ["tmux", "list-windows"]:
            r.stdout = ""
        if isinstance(cmd, list) and cmd[:3] == ["gh", "pr", "view"] and "statusCheckRollup,headRefOid" in cmd:
            r.stdout = json.dumps({"headRefOid": "cafe1234", "statusCheckRollup": [
                {"__typename": "CheckRun", "name": "t", "status": "COMPLETED",
                 "conclusion": "SUCCESS", "workflowName": "CI",
                 "detailsUrl": "https://github.com/o/r/actions/runs/1/job/2"}]})
        elif isinstance(cmd, list) and cmd[:3] == ["gh", "pr", "view"]:
            r.stdout = json.dumps({"state": "OPEN", "mergeable": "MERGEABLE"})
        return r

    with patch.object(dispatcher, "load_workflow_cached",
                      return_value=WorkflowStatus(path=wf.path, workflow=wf, error=None)), \
         patch.object(dispatcher, "get_source", return_value=_EmptySource()), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher.subprocess, "run", side_effect=fake_run), \
         patch.object(dispatcher, "_spawn_judge", side_effect=spawn), \
         patch.object(dispatcher, "remove_worktree", return_value=True), \
         patch.object(dispatcher, "_failing_log_tail", return_value=""), \
         patch.object(dispatcher, "_respawn_rework", return_value=False), \
         patch.object(dispatcher.hold, "expire_if_stale", return_value=None), \
         patch.object(dispatcher.hold, "active", return_value=held):
        result = dispatcher.tick(wf.path)

    assert result["held"] is True                     # the queue really was paused
    assert result["judge_stranded"] == 1               # ⛔ and the escalation still ran
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "needs_human"
    assert "a flake" in run["last_error"]


def test_a_BLOCKED_workflow_still_escalates_a_stranded_judge_unknown(tmp_path, monkeypatch):
    """⚖️🧊 CMX-253 Objective 1 placement, the OTHER half of the HELD test above.
    `_escalate_stranded_judge_unknowns` sits ABOVE the `blocked` return too, for the identical
    reason as the hold half: dispatcher.py's own ⛔ comment on step 1e′ says it "starts no
    agent, takes no slot" — a WORKFLOW.md that stopped parsing freezes NEW dispatch (that is
    what `blocked` means), but it must not also silence the one transition that un-strands a
    run that already spent its retry budget.

    Every other stranded-judge test drives an unblocked tick, so nothing pinned this half:
    gating the 1e′ call behind `blocked or hold.active()` — i.e. moving it conceptually below
    the hold/blocked returns — would leave the run stuck in `awaiting_review` forever,
    indistinguishable from one still waiting on review, while the config stays broken.
    """
    monkeypatch.setenv("CHELA_JUDGE_MAX_UNKNOWN_RETRIES", "2")
    wf = _wf(tmp_path)

    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, workflow_path=str(wf.path), pr_head_sha=None,
                 judge_sha="cafe1234", judge_state=judge.J_CANNOT_VERIFY,
                 judge_cannot_verify_tries=2, judge_detail="a flake")

    def spawn(w, row, sha, conn):
        return True    # never reached — the budget is already spent, and dispatch is blocked

    from chela.workflow import WorkflowStatus

    class R:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, *a, **k):
        r = R()
        if isinstance(cmd, list) and cmd[:2] == ["tmux", "list-windows"]:
            r.stdout = ""
        if isinstance(cmd, list) and cmd[:3] == ["gh", "pr", "view"] and "statusCheckRollup,headRefOid" in cmd:
            r.stdout = json.dumps({"headRefOid": "cafe1234", "statusCheckRollup": [
                {"__typename": "CheckRun", "name": "t", "status": "COMPLETED",
                 "conclusion": "SUCCESS", "workflowName": "CI",
                 "detailsUrl": "https://github.com/o/r/actions/runs/1/job/2"}]})
        elif isinstance(cmd, list) and cmd[:3] == ["gh", "pr", "view"]:
            r.stdout = json.dumps({"state": "OPEN", "mergeable": "MERGEABLE"})
        return r

    with patch.object(dispatcher, "load_workflow_cached",
                      return_value=WorkflowStatus(path=wf.path, workflow=wf,
                                                   error="WORKFLOW.md: bad yaml at line 4")), \
         patch.object(dispatcher, "get_source", return_value=_EmptySource()), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher.subprocess, "run", side_effect=fake_run), \
         patch.object(dispatcher, "_spawn_judge", side_effect=spawn), \
         patch.object(dispatcher, "remove_worktree", return_value=True), \
         patch.object(dispatcher, "_failing_log_tail", return_value=""), \
         patch.object(dispatcher, "_respawn_rework", return_value=False), \
         patch.object(dispatcher.hold, "expire_if_stale", return_value=None), \
         patch.object(dispatcher.hold, "active", return_value=None):
        result = dispatcher.tick(wf.path)

    assert result["blocked"] is True                   # the config really was broken
    assert result["held"] is False                      # this is the OTHER gate, not a hold
    assert result["judge_stranded"] == 1                # ⛔ and the escalation still ran
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "needs_human"
    assert "a flake" in run["last_error"]


def test_a_running_judge_at_the_retry_bound_is_not_escalated_mid_run(tmp_path, monkeypatch):
    """⚖️🧊 CMX-253 Objective 1, negative control on the query's OWN `judge_state` clause.
    `tries == judge_max_unknown_retries()` is the NORMAL state of a run's LAST retry the moment
    it launches: `_spawn_judge` bumps `judge_cannot_verify_tries` to the bound and flips
    `judge_state` to `J_RUNNING` in the very same UPDATE. From that instant until the judge
    publishes, the row matches every OTHER arm of `_escalate_stranded_judge_unknowns`'s query
    (same workflow, `status='awaiting_review'`, `judge_sha=pr_head_sha`, `tries>=max`) — the
    `judge_state=J_CANNOT_VERIFY` clause is the only thing standing between a judge that is
    still actively working and being yanked to `needs_human` out from under it.

    Seen to go red: widen the query's `judge_state=?` clause to match unconditionally (e.g.
    `AND (judge_state=? OR 1=1)`) — a run mid-final-attempt gets escalated while its judge is
    still running, on the exact HAPPY path every retry passes through.
    """
    monkeypatch.setenv("CHELA_JUDGE_MAX_UNKNOWN_RETRIES", "2")
    wf = _wf(tmp_path)

    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, workflow_path=str(wf.path), pr_head_sha=None,
                 judge_sha="cafe1234", judge_state=judge.J_RUNNING,
                 judge_cannot_verify_tries=2, judge_detail="")

    def spawn(w, row, sha, conn):
        return True    # never reached — the queue has no fresh work to dispatch this tick

    # The judge's window stays alive across the tick — this is a judge that is genuinely still
    # running, not a vanished one; `_judge_watchdog` (a different guard) must leave it alone.
    win = (judge.judge_window_name("test-1"),)
    result = _tick(wf, spawn, windows=win)
    assert result["judge_stranded"] == 0
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "awaiting_review"           # ⛔ not yanked out from under the judge
    assert run["judge_state"] == judge.J_RUNNING


def test_a_fresh_commit_resets_the_budget_instead_of_escalating(tmp_path, monkeypatch):
    """A `cannot_verify` past budget on an OLD head must not strand the run once a rework (or
    a human push) lands a new commit — the new `pr_head_sha` no longer matches `judge_sha`,
    so this is a fresh judgement, not a spent retry, and the trigger picks it up instead of
    `_escalate_stranded_judge_unknowns`."""
    monkeypatch.setenv("CHELA_JUDGE_MAX_UNKNOWN_RETRIES", "2")
    wf = _wf(tmp_path)

    spawned: list[str] = []

    def spawn(w, row, sha, conn):
        spawned.append(sha)
        conn.execute("UPDATE runs SET judge_sha=?, judge_state=? WHERE task_id=?",
                     (sha, judge.J_RUNNING, row["task_id"]))
        conn.commit()
        return True

    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, workflow_path=str(wf.path), pr_head_sha=None,
                 judge_sha="cafe1234", judge_state=judge.J_CANNOT_VERIFY,
                 judge_cannot_verify_tries=2, judge_detail="a flake")

    result = _tick(wf, spawn, sha="f00dbabe")
    assert result["judge_stranded"] == 0
    assert spawned == ["f00dbabe"]
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "awaiting_review"


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


def test_a_vanished_judge_window_does_not_spend_a_retry(tmp_path, monkeypatch):
    """⚖️🕳️ CMX-253 Objective 2. A judge whose window vanished (host reboot, tmux death)
    before it published a verdict never got to run at all — re-launching it is the FIRST
    attempt on this commit, not a retry of a failed one. Counting it against
    `judge_cannot_verify_tries` would burn the whole bounded budget on a string of reboots
    that told us nothing about the PR (observed live 2026-08-12, seven reboots in a row).

    Seen to go red: revert `_spawn_judge`'s `judge_no_verdict` check (or `_judge_watchdog`'s
    stamping of it) and this fails — `judge_cannot_verify_tries` becomes 1, exactly the
    stranding bug CMX-253 Objective 2 exists to close.
    """
    monkeypatch.setenv("CHELA_JUDGE_MAX_UNKNOWN_RETRIES", "2")
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
        return r["judge_state"], r["judge_cannot_verify_tries"], r["judge_no_verdict"]

    _spawn("cafe1234")
    assert _state() == (judge.J_RUNNING, 0, 0)

    # The judge's window vanishes before it ever published anything — no lock file on disk
    # either (a real host reboot leaves nothing behind), so the watchdog reaps it.
    with dispatcher._db() as conn:
        with patch.object(judge, "judge_lock_live", return_value=False):
            handed_over = dispatcher._judge_watchdog(conn, wf, live_windows=set())
        conn.commit()
    assert handed_over == 1
    state, tries, no_verdict = _state()
    assert state == judge.J_CANNOT_VERIFY
    assert no_verdict == 1
    assert tries == 0                    # untouched by the vanish itself

    # Re-launching the SAME sha must NOT count this as a spent retry.
    _spawn("cafe1234")
    assert _state() == (judge.J_RUNNING, 0, 0)


def test_an_expired_login_is_reaped_immediately_and_does_not_spend_a_retry(tmp_path, monkeypatch):
    """⚖️🔌 CMX-282. An expired login leaves the judge's window ALIVE (tmux never dropped
    it) and its pane sitting at "Login expired · Please run /login" — no amount of waiting
    fixes that, so the watchdog must reap it on sight instead of holding it for the full
    JUDGE_TIMEOUT_SECONDS (60min) the way a genuine stall would. Measured live 2026-08-14:
    two judges sat at exactly this banner until the 60-minute timeout arm finally caught them.

    Because the judge never got a chance to run (same as a vanished window), it must not
    spend the bounded `judge_cannot_verify_tries` budget either — re-launching it on the
    same commit is the FIRST attempt, not a retry of a failed one.

    Seen to go red: drop the `login_expired` check from `_judge_watchdog` (falls through to
    the `alive and not timed_out: continue` and waits the full hour), or stamp
    `judge_no_verdict=0` for it (spends a retry it shouldn't).
    """
    monkeypatch.setenv("CHELA_JUDGE_MAX_UNKNOWN_RETRIES", "2")
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
        return r["judge_state"], r["judge_cannot_verify_tries"], r["judge_no_verdict"]

    _spawn("cafe1234")
    assert _state() == (judge.J_RUNNING, 0, 0)

    window = judge.judge_window_name("test-1")
    banner = "✽ Sonnet 5\n\nLogin expired · Please run /login\n\n❯ "
    # ⛔ side_effect, not return_value: a flat return_value answers the banner for ANY
    # target, so `_capture_pane("")` (the wrong-pane mutation) would pass just as well as
    # `_capture_pane(window)`. Only the judge's OWN window may see the banner — see
    # DEFEAT_SHAPES.md.
    capture_calls = []

    def _capture(w):
        capture_calls.append(w)
        return banner if w == window else ""

    with dispatcher._db() as conn:
        with patch.object(dispatcher, "_capture_pane", side_effect=_capture), \
             patch.object(dispatcher, "_kill_windows_named") as kill:
            handed_over = dispatcher._judge_watchdog(conn, wf, live_windows={window})
        conn.commit()
    assert handed_over == 1
    assert window in capture_calls                # the pane read must target the judge's OWN window
    kill.assert_called_once_with(window)          # ⛔ still alive — must be torn down, not left
    state, tries, no_verdict = _state()
    assert state == judge.J_CANNOT_VERIFY
    # ⛔ full sentence, not a lowercase "login" substring: that substring is satisfied by
    # the "/login" fragment alone, so it would still pass if the leading clause were
    # rewritten to claim the window disappeared instead of the login expiring.
    assert dispatcher.resolve_run("abc123")["judge_detail"] == (
        "the judge's session login expired mid-run (\"Login expired · Please run "
        "/login\") — not a verdict on the PR"
    )
    assert no_verdict == 1
    assert tries == 0                             # untouched — this reap is not a spent attempt

    # Re-launching the SAME sha must NOT count this as a spent retry.
    _spawn("cafe1234")
    assert _state() == (judge.J_RUNNING, 0, 0)


def test_an_expired_login_reaps_even_when_the_judge_lock_says_alive(tmp_path, monkeypatch):
    """⚖️🔌 CMX-282, negative control on the CMX-229 lock cross-check. `login_expired` is a
    BOUND on that cross-check exactly like `timed_out` is (see the comment above the
    `judge.judge_lock_live(...)` call in `_judge_watchdog`): once the pane evidence says the
    session is stuck at the login banner, no lock file claiming the process is still alive
    may hold teardown — the process being alive is exactly the problem, not proof of health.

    Without this control, a version of the fix that let a live lock override `login_expired`
    (i.e. only short-circuits `judge_lock_live` when NOT login_expired, same bug shape as the
    surviving mutation `if not timed_out and not login_expired and judge.judge_lock_live(...)`
    → `if not timed_out and judge.judge_lock_live(...)`) would pass every other test here,
    because none of them mount a LIVE lock at the same time as an expired-login banner.

    Seen to go red: reintroduce `judge.judge_lock_live(...)` into the boolean the watchdog
    consults when `login_expired` is True (i.e. drop the `not login_expired` short-circuit) —
    a live lock then holds teardown instead of reaping, and `handed_over` comes back 0.
    """
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, workflow_path=str(wf.path))

    with dispatcher._db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()
        with patch.object(dispatcher, "detached_worktree", return_value=(None, True)), \
             patch.object(dispatcher, "render_prompt", return_value="x"), \
             patch.object(dispatcher, "_judge_vars", return_value={}), \
             patch.object(dispatcher, "_launch_agent", return_value=None):
            assert dispatcher._spawn_judge(wf, row, "cafe1234", conn) is True

    window = judge.judge_window_name("test-1")
    banner = "✽ Sonnet 5\n\nLogin expired · Please run /login\n\n❯ "
    # ⛔ side_effect, not return_value — see the sibling test above for why a flat
    # return_value can't tell `_capture_pane(window)` from `_capture_pane("")`.
    capture_calls = []

    def _capture(w):
        capture_calls.append(w)
        return banner if w == window else ""

    with dispatcher._db() as conn:
        with patch.object(dispatcher, "_capture_pane", side_effect=_capture), \
             patch.object(dispatcher, "_kill_windows_named") as kill, \
             patch.object(judge, "judge_lock_live", return_value=True):
            handed_over = dispatcher._judge_watchdog(conn, wf, live_windows={window})
        conn.commit()
    assert handed_over == 1                       # reaped despite the lock claiming alive
    assert window in capture_calls                # the pane read must target the judge's OWN window
    kill.assert_called_once_with(window)
    r = dispatcher.resolve_run("abc123")
    assert r["judge_state"] == judge.J_CANNOT_VERIFY
    assert r["judge_detail"] == (
        "the judge's session login expired mid-run (\"Login expired · Please run "
        "/login\") — not a verdict on the PR"
    )


def test_a_genuine_cannot_verify_verdict_still_spends_a_retry(tmp_path, monkeypatch):
    """⚖️🕳️ CMX-253 Objective 2, negative control. A `cannot_verify` the judge actually
    PRODUCED (it ran, and came back with an unknown — `set_judge_state`'s default,
    `judge_no_verdict=False`) is unchanged by this fix and still costs a bounded retry, same
    as CMX-81 always did. Without this control, a fix that stopped counting `cannot_verify`
    ENTIRELY (rather than only the no-verdict case) would still pass the vanished-window test
    above."""
    monkeypatch.setenv("CHELA_JUDGE_MAX_UNKNOWN_RETRIES", "2")
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
        return r["judge_state"], r["judge_cannot_verify_tries"], r["judge_no_verdict"]

    _spawn("cafe1234")
    assert _state() == (judge.J_RUNNING, 0, 0)

    # The judge actually ran and reported an unknown — e.g. `judge.judge_run` writing
    # `report.cannot_verify` through its normal path. `no_verdict` defaults False.
    dispatcher.set_judge_state("abc123", judge.J_CANNOT_VERIFY, "no judge.test_cmd configured")
    assert _state() == (judge.J_CANNOT_VERIFY, 0, 0)

    _spawn("cafe1234")
    assert _state() == (judge.J_RUNNING, 1, 0)   # ⛔ this one DOES cost a retry


def test_a_stale_no_verdict_flag_is_cleared_by_the_next_real_verdict(tmp_path, monkeypatch):
    """⚖️🕳️ CMX-253 Objective 2, negative control on `set_judge_state` itself, not on
    `_spawn_judge`. Its own docstring makes the claim twice: "every normal caller passes
    nothing, which clears it back to 0 — the column always describes the state THIS call just
    wrote, never a stale prior one." The reachable route where that matters is a row reaped as
    no-verdict (`judge_no_verdict=1`, left by `_judge_watchdog` after a vanished window) and
    then judged FOR REAL by a hand-run `chela judge run` — which writes its verdict through
    `set_judge_state`, not through `_spawn_judge` (whose own UPDATE unconditionally zeroes the
    column, so that path can never expose a stale 1). If the stale flag survives this write,
    the very next `_spawn_judge` on the same commit reads `judge_no_verdict` still truthy and
    wrongly declines to count a `cannot_verify` the judge really did produce.

    Seen to go red: swap `judge_no_verdict=?` for `judge_no_verdict=COALESCE(judge_no_verdict,
    ?)` in `set_judge_state`'s SHA UPDATE — the 1 survives the real verdict, and the retry
    this genuine unknown must spend never gets counted. The SHA branch, not the no-sha one, is
    what this must pin: every real caller in `judge.judge_run` passes `sha=judged_sha`
    (chela/judge.py:1568,1576,1603) — the no-sha branch is dead on that path.
    """
    monkeypatch.setenv("CHELA_JUDGE_MAX_UNKNOWN_RETRIES", "2")
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        # A prior attempt on this same commit was reaped as no-verdict — exactly the state
        # `_judge_watchdog` leaves behind (see test_a_vanished_judge_window_does_not_spend_a_
        # retry): judge_sha already at this head, tries untouched, the flag set.
        _run_row(conn, tmp_path, workflow_path=str(wf.path), judge_sha="cafe1234",
                 judge_state=judge.J_CANNOT_VERIFY, judge_cannot_verify_tries=0,
                 judge_no_verdict=1)

    # A hand-run `chela judge run` judges the SAME commit for real and comes back cannot_verify
    # — set_judge_state's `sha=` call, `no_verdict` defaults False, exactly judge.judge_run's
    # own path (chela/judge.py:1603: `set_judge_state(task_id, report.state, ..., sha=judged_sha)`).
    dispatcher.set_judge_state("abc123", judge.J_CANNOT_VERIFY, "no judge.test_cmd configured",
                                sha="cafe1234")
    run = dispatcher.resolve_run("abc123")
    assert run["judge_no_verdict"] == 0          # ⛔ the stale reboot flag must not survive
    assert run["judge_sha"] == "cafe1234"        # sha branch really ran, not the no-sha one

    def _spawn(sha):
        with dispatcher._db() as conn:
            row = conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()
            with patch.object(dispatcher, "detached_worktree", return_value=(None, True)), \
                 patch.object(dispatcher, "render_prompt", return_value="x"), \
                 patch.object(dispatcher, "_judge_vars", return_value={}), \
                 patch.object(dispatcher, "_launch_agent", return_value=None):
                assert dispatcher._spawn_judge(wf, row, sha, conn) is True

    _spawn("cafe1234")
    r = dispatcher.resolve_run("abc123")
    assert (r["judge_state"], r["judge_cannot_verify_tries"], r["judge_no_verdict"]) == \
        (judge.J_RUNNING, 1, 0)   # ⛔ this genuine cannot_verify DOES cost a retry


def test_a_stale_no_verdict_flag_is_cleared_by_the_no_sha_branch_too(tmp_path):
    """⚖️🕳️ CMX-253 Objective 2, mirror of `test_a_stale_no_verdict_flag_is_cleared_by_the_
    next_real_verdict` for `set_judge_state`'s OTHER branch. That test pins the `sha=` branch,
    which is the one `judge.judge_run`'s real verdict calls take — but the no-sha branch is
    not dead code: `judge.judge_run` itself calls `set_judge_state(task_id, J_CANNOT_VERIFY,
    "the workflow could not be read")` with NO `sha` (chela/judge.py:1396) when the WORKFLOW.md
    it was pointed at no longer parses. A row reaped as no-verdict by `_judge_watchdog` and
    then re-run against a workflow that has since gone unreadable takes exactly this branch,
    and must clear the stale flag the same as the sha branch does.

    Seen to go red: swap `judge_no_verdict=?` for `judge_no_verdict=COALESCE(judge_no_verdict,
    ?)` in `set_judge_state`'s no-sha UPDATE — the 1 survives this write.
    """
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        # Same starting state as the sha-branch test: a prior attempt was reaped as
        # no-verdict, leaving the flag set.
        _run_row(conn, tmp_path, workflow_path=str(wf.path), judge_sha="cafe1234",
                 judge_state=judge.J_CANNOT_VERIFY, judge_cannot_verify_tries=0,
                 judge_no_verdict=1)

    # judge.judge_run's unreadable-workflow path: no `sha=` kwarg at all.
    dispatcher.set_judge_state("abc123", judge.J_CANNOT_VERIFY, "the workflow could not be read")
    run = dispatcher.resolve_run("abc123")
    assert run["judge_no_verdict"] == 0          # ⛔ the stale reboot flag must not survive
    assert run["judge_sha"] == "cafe1234"        # no-sha branch never touches judge_sha


@pytest.mark.parametrize("call_kwargs", [
    pytest.param({"sha": "cafe1234"}, id="sha_branch"),
    pytest.param({}, id="no_sha_branch"),
])
def test_a_genuine_no_verdict_is_not_cleared_by_set_judge_state(tmp_path, call_kwargs):
    """⚖️🕳️ CMX-253 Objective 2, negative control on the OTHER half of the two tests above.
    Those two only ever call `set_judge_state` with `no_verdict` defaulting False, so an
    implementation that always writes ``judge_no_verdict=0`` — ignoring the `no_verdict`
    argument entirely — would pass both of them and still be wrong: it would silently
    destroy the flag's meaning, because `_judge_watchdog`'s window-vanished branch (see
    `judge_no_verdict`'s column comment) depends on a caller being able to SET the flag,
    not just clear it. This pins the write half on both UPDATE branches: a call that really
    is reporting "no verdict was produced" (`no_verdict=True`) must land as 1, whether or
    not `sha=` is given.

    Seen to go red: hardcode either UPDATE's `judge_no_verdict` column to `0` (or to
    `COALESCE(judge_no_verdict, ?)`, which the two clearing tests above cannot catch when
    the column starts at 0 — `COALESCE(0, ?)` also returns the stale 0, not the new value).
    """
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, workflow_path=str(wf.path), judge_sha="cafe1234",
                 judge_no_verdict=0)

    dispatcher.set_judge_state(
        "abc123", judge.J_CANNOT_VERIFY,
        "the judge's window disappeared before it published a verdict",
        no_verdict=True, **call_kwargs,
    )
    run = dispatcher.resolve_run("abc123")
    assert run["judge_no_verdict"] == 1          # ⛔ a genuine no-verdict must be recorded


def _fake_tmux_new_window(target_id: str):
    """Fake `subprocess.run` good enough to drive `_launch_agent`'s real tmux half:
    `tmux new-window` reports `target_id`, everything else (send-keys, …) is a no-op."""
    from types import SimpleNamespace

    def fake_run(argv, *a, **k):
        if argv[:2] == ["tmux", "new-window"]:
            return SimpleNamespace(stdout=f"{target_id}\n", returncode=0)
        return SimpleNamespace(stdout="", returncode=0)

    return fake_run


def test_spawn_judge_stamps_its_OWN_window_id_never_the_runs(tmp_path, monkeypatch):
    """🤫 CMX-97. `_spawn_judge` calls `_launch_agent(..., judge_window=True)` so the
    RUN's `window_id` must stay untouched — but the judge still needs to be found by
    `dispatched_window_ids` (CMX-73's forum-topic gate, CMX-76's Wall tile), so it gets
    its OWN `judge_window_id`/`judge_window_epoch` pair instead. ⛔ Runs `_launch_agent` for
    REAL (only the tmux subprocess calls are faked) — CMX-136 moved the stamp INSIDE that
    function, so mocking `_launch_agent` itself would no longer exercise the stamp at all."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, workflow_path=str(wf.path), window_id="@1",
                 window_epoch="epoch-orig")

    monkeypatch.setattr(dispatcher, "_kill_windows_named", lambda *_a, **_k: None)
    monkeypatch.setattr(dispatcher, "_wait_for_ready", lambda *a, **k: True)
    monkeypatch.setattr(dispatcher, "_send_seed", lambda *a, **k: True)
    monkeypatch.setattr(dispatcher.subprocess, "run", _fake_tmux_new_window("@42"))
    monkeypatch.setattr(dispatcher.epoch, "current", lambda: "epoch-now")

    with dispatcher._db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()
        with patch.object(dispatcher, "detached_worktree", return_value=(None, True)), \
             patch.object(dispatcher, "render_prompt", return_value="x"), \
             patch.object(dispatcher, "_judge_vars", return_value={}):
            assert dispatcher._spawn_judge(wf, row, "cafe1234", conn) is True

    run = dispatcher.resolve_run("abc123")
    assert run["judge_window_id"] == "@42"
    assert run["judge_window_epoch"] == "epoch-now"
    # ⛔ the run's OWN window is a completely different identity, and stays as it was.
    assert run["window_id"] == "@1"
    assert run["window_epoch"] == "epoch-orig"


def test_spawn_judge_stamps_nothing_when_the_launch_returns_no_real_id(tmp_path, monkeypatch):
    """`_new_window` degrades to a bare name when the `@id` can't be parsed (see
    `_launch_agent`) — a name in `judge_window_id` would be a lie `dispatched_window_ids`
    keys a decision on, so it must stay NULL rather than record a lie."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, workflow_path=str(wf.path))

    monkeypatch.setattr(dispatcher, "_kill_windows_named", lambda *_a, **_k: None)
    monkeypatch.setattr(dispatcher, "_wait_for_ready", lambda *a, **k: True)
    monkeypatch.setattr(dispatcher, "_send_seed", lambda *a, **k: True)
    monkeypatch.setattr(dispatcher.subprocess, "run", _fake_tmux_new_window("judge-test-1"))

    with dispatcher._db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()
        with patch.object(dispatcher, "detached_worktree", return_value=(None, True)), \
             patch.object(dispatcher, "render_prompt", return_value="x"), \
             patch.object(dispatcher, "_judge_vars", return_value={}):
            assert dispatcher._spawn_judge(wf, row, "cafe1234", conn) is True

    run = dispatcher.resolve_run("abc123")
    assert run["judge_window_id"] is None
    assert run["judge_window_epoch"] is None


def test_spawn_judge_stamps_the_window_id_before_the_ready_wait_not_after(tmp_path, monkeypatch):
    """⏱️ CMX-136. The whole point of moving the stamp inside `_launch_agent` is to close
    the race where the judge's tmux window existed (so `/api/agents` reported it live) but
    `judge_window_id` was still NULL (so `dispatched_window_ids` could not see it yet) — the
    Wall drew it full-size like a human window for the entire ready-wait + `_send_seed` gap.
    So the stamp must land BEFORE `_wait_for_ready` is even called, not after. A regression
    that put the stamp back after the full launch (e.g. keying off the return value again)
    would still pass the two tests above — only checking ordering catches it."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, workflow_path=str(wf.path))

    seen_at_ready_wait = {}

    def _wait_for_ready(*a, **k):
        run = dispatcher.resolve_run("abc123")
        seen_at_ready_wait["judge_window_id"] = run["judge_window_id"]
        return True

    monkeypatch.setattr(dispatcher, "_kill_windows_named", lambda *_a, **_k: None)
    monkeypatch.setattr(dispatcher, "_wait_for_ready", _wait_for_ready)
    monkeypatch.setattr(dispatcher, "_send_seed", lambda *a, **k: True)
    monkeypatch.setattr(dispatcher.subprocess, "run", _fake_tmux_new_window("@42"))

    with dispatcher._db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()
        with patch.object(dispatcher, "detached_worktree", return_value=(None, True)), \
             patch.object(dispatcher, "render_prompt", return_value="x"), \
             patch.object(dispatcher, "_judge_vars", return_value={}):
            assert dispatcher._spawn_judge(wf, row, "cafe1234", conn) is True

    assert seen_at_ready_wait["judge_window_id"] == "@42"


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


def test_judge_max_concurrent_gates_how_many_spawn_per_tick(tmp_path):
    """⚖️ CMX-278: `JUDGE_MAX_CONCURRENT` was a hardcoded ``1`` with no knob — this is that
    same per-tick gate, now `config.judge_max_concurrent()`. Two FRESH runs (no judge
    running yet) on the same workflow; the default (``1``) spawns only the first and leaves
    the second for a later tick, same as the single-run "left alone" test above."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, task_id="abc123", workflow_path=str(wf.path),
                 window_name="test-1", branch_name="test-1")
        _run_row(conn, tmp_path, task_id="def456", workflow_path=str(wf.path),
                 window_name="test-2", branch_name="test-2")

    spawns: list[str] = []
    summary = _tick(wf, lambda w, row, sha, conn: (spawns.append(row["task_id"]), True)[1],
                     open_ids=("abc123", "def456"))

    assert summary["judged"] == 1
    assert len(spawns) == 1


def test_judge_max_concurrent_env_raises_the_per_tick_gate(tmp_path, monkeypatch):
    """🔴 Same two fresh runs, but `CHELA_JUDGE_MAX_CONCURRENT=2` — both spawn in the same
    tick. ⚖️ Corrupt (register the knob in `config.DISPATCH_KNOBS` but never call it from
    the dispatcher's judge-spawn loop) → this goes RED while the config-level knob tests
    stay green, same gap CMX-220's `gate_max_waits` wiring tests exist to catch."""
    monkeypatch.setenv("CHELA_JUDGE_MAX_CONCURRENT", "2")
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, task_id="abc123", workflow_path=str(wf.path),
                 window_name="test-1", branch_name="test-1")
        _run_row(conn, tmp_path, task_id="def456", workflow_path=str(wf.path),
                 window_name="test-2", branch_name="test-2")

    spawns: list[str] = []
    summary = _tick(wf, lambda w, row, sha, conn: (spawns.append(row["task_id"]), True)[1],
                     open_ids=("abc123", "def456"))

    assert summary["judged"] == 2
    assert sorted(spawns) == ["abc123", "def456"]


def _write_live_judge_lock(wf, task_id: str) -> Path:
    """A real ``.judgelock`` sibling of the judge worktree, naming THIS test process — the
    same shape :func:`judge._claim_judge_slot` writes, read back by
    :func:`judge.judge_lock_live` exactly as the watchdog will."""
    from chela import sessions

    worktree = judge.judge_worktree_path(wf, task_id)
    lock_path = worktree.parent / f".{worktree.name}.judgelock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    pid = os.getpid()
    lock_path.write_text(json.dumps(
        {"pid": pid, "started": sessions.proc_started(pid), "task_id": task_id}
    ))
    return lock_path


def test_a_missing_judge_window_is_HELD_not_reaped_while_its_lock_is_live(tmp_path):
    """⚖️🕳️ CMX-229 Objective 2. Measured live on CMX-227: the watchdog reaped a judge's
    worktree/window off a single tick's tmux snapshot and SIGKILLed it (exit 137) mid-
    `chela judge run`. `alive=False` here (the window is missing from THIS tick's snapshot)
    — the old behaviour reaped immediately. With a live judge lock naming a real process,
    the watchdog must hold instead: no reap, no CANNOT_VERIFY, no worktree removal."""
    wf = _wf(tmp_path)
    from chela.dispatcher import _now
    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, workflow_path=str(wf.path),
                 judge_state=judge.J_RUNNING, judge_sha="cafe1234", judge_started_at=_now())
    lock_path = _write_live_judge_lock(wf, "abc123")

    removed: list[Path] = []
    with patch.object(dispatcher, "remove_worktree", side_effect=lambda repo, p, root: removed.append(p) or True):
        summary = _tick(wf, lambda *a: True, windows=())   # window missing from THIS snapshot

    assert summary["judge_lost"] == 0
    assert dispatcher.resolve_run("abc123")["judge_state"] == judge.J_RUNNING
    assert removed == []                              # ⛔ the worktree must not be touched
    assert lock_path.exists()                          # …and the lock itself is left alone


def test_the_hold_still_expires_once_the_judge_TIMES_OUT(tmp_path):
    """⛔ COUNTERWEIGHT — the hold above must be BOUNDED, not a second, unbounded lock-driven
    timeout. A judge whose window is missing AND whose own start time is past
    `JUDGE_TIMEOUT_SECONDS` is reaped regardless of what its lock file still claims — a
    hold that ignored this bound would leak a wedged watchdog forever on a judge that
    claimed its lock and then hung."""
    from datetime import datetime, timedelta, timezone

    wf = _wf(tmp_path)
    stale_started = (
        datetime.now(timezone.utc) - timedelta(seconds=dispatcher.JUDGE_TIMEOUT_SECONDS + 60)
    ).isoformat()
    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, workflow_path=str(wf.path),
                 judge_state=judge.J_RUNNING, judge_sha="cafe1234", judge_started_at=stale_started)
    _write_live_judge_lock(wf, "abc123")

    summary = _tick(wf, lambda *a: True, windows=())

    assert summary["judge_lost"] == 1
    run = dispatcher.resolve_run("abc123")
    assert run["judge_state"] == judge.J_CANNOT_VERIFY
    assert "did not finish" in run["judge_detail"]
    # ⚖️🕳️ CMX-253 Objective 2, negative control. A judge that TIMED OUT DID get a chance to
    # run — it is stuck, not thinking, and that is still a counted unknown (CMX-81's bounded
    # retry must see it). Only a window that VANISHED before it ever ran is exempt; conflating
    # the two would let a permanently wedged judge dodge the retry budget forever.
    assert run["judge_no_verdict"] == 0


@pytest.mark.parametrize("delta,expect_alive", [
    (0.0, True),        # the same process, same reader
    (0.4, True),        # /proc wrote .97, the `ps` fallback read .00 — same process
    (0.99, True),       # the widest the fallback's whole-second resolution can produce
    (1.5, False),       # beyond it: a different process wearing the same pid
    (100.0, False),
])
def test_the_judge_lock_start_time_window_is_exactly_the_fallbacks_resolution(delta, expect_alive):
    """🔴 Pins the 1.0s window at BOTH edges. The existing recycled-pid test uses a 100s
    delta, so it proves the comparison exists but cannot tell `< 1.0` from `< 3600` — the
    VALUE-SIZE blindness CMX-219's judge flagged on exactly this kind of check.

    The bound is not arbitrary and must not drift in either direction:
      * WIDER re-opens CMX-219's hole — a recycled pid whose new process started close to
        the dead one's start time would read as the same process.
      * NARROWER (e.g. exact equality, the "obvious" CMX-219 fix) breaks the `ps` fallback:
        `/proc` reports sub-second, `ps -o lstart=` reports whole seconds, so the same
        untouched process legitimately differs by up to one second across the two readers.
    """
    from unittest.mock import patch
    from chela import sessions

    base = 1785703040.97
    with patch.object(sessions, "proc_started", lambda pid: base):
        assert judge._judge_lock_owner_alive({"pid": 4242, "started": base - delta}) is expect_alive


def test_cmd_judge_prints_the_blocked_race_verdict_distinctly(capsys):
    """⚖️🧊 CMX-239 round 2: `chela judge run`'s CLI print branch for `J_BLOCKED_RACE` is a
    dead ``elif`` away from silently falling through to the ``else`` — which prints "every
    guard held", the exact opposite of what happened. Drive the real argparse dispatch (the
    way `test_contract_cli.py` proves the merge/escalate call-sites) so a corrupted
    ``elif state == judge.J_BLOCKED_RACE:`` turns this red instead of leaving it green.
    """
    from unittest.mock import patch

    from chela import main

    fake = {
        "ok": False, "task_id": "cmx-99", "blocking": 2, "round": 1,
        "error": "the run moved to merged before the verdict could be written",
        "state": judge.J_BLOCKED_RACE, "outcomes": [],
    }
    with patch.object(main.judge, "judge_run", return_value=fake):
        with patch.object(sys, "argv",
                           ["chela", "judge", "run", "cmx-99", "--experiments", "x.json"]):
            main.main()
    out = capsys.readouterr().out
    assert "SURVIVED corruption, but the run had already moved on" in out
    assert "This needs a human look NOW" in out
    assert "every guard held" not in out


def test_cmd_judge_prints_the_stale_head_notice_not_every_guard_held(capsys):
    """⚖️⏱️ CMX-246 rework, finding 3: `chela judge run`'s CLI print branch for `J_STALE_HEAD`
    is a dead ``elif`` away from silently falling through to the ``else`` — which prints
    "every guard held. The run stays awaiting_review", exactly what a human would read as
    "nothing needs their attention" for a run whose verdict was actually just discarded as
    superseded. Same drive-the-real-argparse-dispatch pattern as the `J_BLOCKED_RACE` CLI
    test above.

    Disable ``elif state == judge.J_STALE_HEAD:`` (e.g. ``elif False and state == ...:``) and
    this goes red: it falls to the ``else`` branch and prints "every guard held" instead of
    the supersession notice."""
    from unittest.mock import patch

    from chela import main

    fake = {
        "ok": False, "task_id": "cmx-99", "state": judge.J_STALE_HEAD, "outcomes": [],
        "error": "verdict was for 'oldsha000001', but the PR's head is now 'newsha000002' — "
                 "discarded, no round spent",
        "comment_posted": True,
    }
    with patch.object(main.judge, "judge_run", return_value=fake):
        with patch.object(sys, "argv",
                           ["chela", "judge", "run", "cmx-99", "--experiments", "x.json"]):
            main.main()
    out = capsys.readouterr().out
    assert "no rework round was spent" in out
    assert "a fresh judge covers the new head" in out
    assert "every guard held" not in out


def test_taskmodal_judge_badge_key_matches_j_blocked_race_value():
    """⚖️🧊 CMX-239 round 4: the judge corrupted `J_BLOCKED_RACE`'s VALUE (not a call site)
    and the suite stayed green. `taskmodal.js`'s `_JUDGE_BADGE` is keyed on the literal
    ``'blocked_race'``, and `tests/taskmodal_judge_badge.test.mjs` only pins that SAME
    hardcoded literal against itself — it never looks at `judge.py`. Every Python test, in
    turn, references the SYMBOL `judge.J_BLOCKED_RACE`, never its string value, so neither
    side of the language boundary would notice the two drifting apart. If they do,
    `item.judge_state` (populated straight from the DB column `J_BLOCKED_RACE` writes) no
    longer matches any `_JUDGE_BADGE` key, and `_JUDGE_BADGE[item.judge_state] ||
    'badge-priority-low'`'s fallback silently degrades a CONFIRMED blocking finding into
    `cannot_verify`'s low-priority tier.

    This test is the only thing that reads BOTH sides: it takes `judge.J_BLOCKED_RACE`'s
    live value and requires that exact string to appear as a key in the real
    `taskmodal.js` source. Corrupt the constant's value in `judge.py` alone (JS untouched)
    and this goes red, because the JS source no longer has a key matching the new value.
    """
    js_path = (Path(__file__).resolve().parent.parent / "chela" / "dashboard" / "static"
               / "js" / "taskmodal.js")
    src = js_path.read_text()
    match = re.search(r"const _JUDGE_BADGE = \{(.*?)\n\};", src, re.DOTALL)
    assert match, "taskmodal.js's _JUDGE_BADGE object literal not found — did it move or get renamed?"
    keys = set(re.findall(r"^\s*(\w+):", match.group(1), re.MULTILINE))
    assert judge.J_BLOCKED_RACE in keys, (
        f"_JUDGE_BADGE has no key matching judge.J_BLOCKED_RACE ({judge.J_BLOCKED_RACE!r}) — "
        f"the Python↔JS judge_state contract has drifted. _JUDGE_BADGE keys: {sorted(keys)}"
    )


# --- (i) CMX-249: self_check — the CHECK, not the habit -----------------------------------


def test_self_check_survived_and_killed_match_run_experiments(tmp_path):
    """The mechanics :func:`self_check` shares with :func:`run_experiments` via
    ``_apply_experiments`` must produce the SAME verdicts on the same fixtures."""
    fake_root = _project(tmp_path / "fake", guard_test=FAKE_GUARD_TEST)
    report = judge.self_check(fake_root, TEST_CMD, {"experiments": [_exp()]}, timeout=120)
    assert [o.verdict for o in report.outcomes] == [judge.SURVIVED]
    assert report.state == judge.J_BLOCKED
    assert (fake_root / "guard.py").read_text() == GUARD_PY   # restored

    real_root = _project(tmp_path / "real", guard_test=REAL_GUARD_TEST)
    report = judge.self_check(real_root, TEST_CMD, {"experiments": [_exp()]}, timeout=120)
    assert [o.verdict for o in report.outcomes] == [judge.KILLED]
    assert report.state == judge.J_CLEAN


def test_self_check_runs_on_a_worktree_WITH_UNCOMMITTED_TRACKED_CHANGES(tmp_path):
    """⛔ The whole reason self_check exists: it runs BEFORE the agent commits, so the guard
    it is about to mutate IS an uncommitted tracked edit. Unlike `run_experiments`
    (`test_a_dirty_worktree_verifies_NOTHING`), that must NOT make this cannot_verify — and
    the uncommitted edit itself must survive the mutate/restore cycle untouched."""
    root = _project(tmp_path / "repo", guard_test=FAKE_GUARD_TEST)
    dirty_source = GUARD_PY + "\n# an in-progress edit, not yet committed\n"
    (root / "guard.py").write_text(dirty_source)

    report = judge.self_check(root, TEST_CMD, {"experiments": [_exp()]}, timeout=120)

    assert not report.cannot_verify
    assert [o.verdict for o in report.outcomes] == [judge.SURVIVED]
    assert (root / "guard.py").read_text() == dirty_source     # the in-progress edit survives


def test_self_check_with_no_experiments_is_cannot_verify(tmp_path):
    root = _project(tmp_path / "repo", guard_test=REAL_GUARD_TEST)

    report = judge.self_check(root, TEST_CMD, {"experiments": []}, timeout=120)

    assert report.cannot_verify
    assert "nothing was corrupted" in report.cannot_verify
    assert report.state == judge.J_CANNOT_VERIFY


def test_self_check_on_a_red_baseline_does_not_touch_git(tmp_path):
    """⛔ `run_experiments`' red-baseline diagnosis (`_diagnose_red_baseline`) checks out
    `base_branch` and back — safe on a throwaway detached worktree, NOT safe on a tree an
    agent is actively editing. self_check must report the red baseline plainly, with no
    branch-hopping: none of `_diagnose_red_baseline`'s own phrasing (its `origin/<ref>` case,
    or its base_branch-less case, `"the workflow names no ``workspace.base_branch``"`) may
    appear, because self_check must never call it at all, not even with base_branch=""."""
    root = _project(tmp_path / "repo", guard_test="def test_broken():\n    assert False\n")

    report = judge.self_check(root, TEST_CMD, {"experiments": [_exp()]}, timeout=120)

    assert report.cannot_verify
    assert "NOT GREEN" in report.cannot_verify
    assert "origin/" not in report.cannot_verify
    assert "base_branch" not in report.cannot_verify
    assert report.outcomes == []


def _workflow_md(tmp_path: Path, test_cmd: str) -> Path:
    p = tmp_path / "WORKFLOW.md"
    p.write_text(
        "---\nproject_key: TEST\njudge:\n  test_cmd: " + json.dumps(test_cmd) +
        "\n  suite_timeout_seconds: 120\n---\nbody\n"
    )
    return p


def test_run_self_check_reads_test_cmd_from_the_workflow(tmp_path):
    root = _project(tmp_path / "repo", guard_test=FAKE_GUARD_TEST)
    exp_path = tmp_path / "experiments.json"
    exp_path.write_text(json.dumps({"experiments": [_exp()]}))
    wf_path = _workflow_md(tmp_path, TEST_CMD)

    result = judge.run_self_check(root, exp_path, workflow_path=wf_path)

    assert result["ok"]
    assert result["state"] == judge.J_BLOCKED
    assert result["blocking"] == 1


def test_run_self_check_forwards_the_ENTIRE_judge_config_in_one_call(tmp_path, monkeypatch):
    """⚖️🔁 CMX-266, the remainder of CMX-258 / PR #327: that PR's judge found ``test_cmd``
    unforwarded in rework round 12 and ``suite_timeout_seconds`` unforwarded in round 13 —
    two separate rounds for two fields of the SAME config. This guard corrupts the ONE
    accessor (:func:`judge.judge_suite_config`) both fields come from, so a regression to
    the old "each field sourced by hand, one call site at a time" shape — where a future
    field can silently stop reaching :func:`judge.self_check` — fails HERE, in one place,
    instead of waiting for the judge to find each dropped field on its own round."""
    wf_path = _workflow_md(tmp_path, "some-distinctive-cmd")
    exp_path = tmp_path / "experiments.json"
    exp_path.write_text(json.dumps({"experiments": [_exp()]}))

    captured = {}

    def fake_self_check(worktree, test_cmd, raw, *, timeout):
        captured["test_cmd"] = test_cmd
        captured["timeout"] = timeout
        return judge.Report()

    monkeypatch.setattr(judge, "self_check", fake_self_check)

    result = judge.run_self_check(tmp_path, exp_path, workflow_path=wf_path)

    assert result["ok"]
    # both fields of _workflow_md's `judge:` block, from the ONE JudgeSuiteConfig built by
    # judge_suite_config — not a test_cmd sourced correctly while timeout falls back to the
    # module default (or vice versa).
    assert captured["test_cmd"] == "some-distinctive-cmd"
    assert captured["timeout"] == 120


def test_run_self_check_explicit_test_cmd_wins_over_the_workflow(tmp_path):
    root = _project(tmp_path / "repo", guard_test=REAL_GUARD_TEST)
    exp_path = tmp_path / "experiments.json"
    exp_path.write_text(json.dumps({"experiments": [_exp()]}))
    # A workflow that names a suite which cannot possibly be this one — proves --test-cmd,
    # not the workflow, is what ran.
    wf_path = _workflow_md(tmp_path, "false")

    result = judge.run_self_check(root, exp_path, workflow_path=wf_path, test_cmd=TEST_CMD)

    assert result["ok"]
    assert result["state"] == judge.J_CLEAN


def test_run_self_check_with_no_test_cmd_and_no_workflow_errors(tmp_path):
    exp_path = tmp_path / "experiments.json"
    exp_path.write_text(json.dumps({"experiments": [_exp()]}))

    result = judge.run_self_check(tmp_path, exp_path)

    assert not result["ok"]
    assert "no suite to run" in result["error"]


def test_run_self_check_missing_experiments_file_errors(tmp_path):
    result = judge.run_self_check(tmp_path, tmp_path / "nope.json", test_cmd=TEST_CMD)

    assert not result["ok"]
    assert "does not exist" in result["error"]


def test_cmd_judge_self_check_cli_exits_nonzero_on_a_survived_guard(tmp_path, capsys):
    """Drive the real argparse dispatch (as `test_cmd_judge_prints_the_blocked_race_verdict_
    distinctly` does above) so a corrupted `elif args.judge_cmd == "self-check":` — or a
    corrupted exit-code branch inside `cmd_judge_self_check` — turns this red."""
    root = _project(tmp_path / "repo", guard_test=FAKE_GUARD_TEST)
    exp_path = tmp_path / "experiments.json"
    exp_path.write_text(json.dumps({"experiments": [_exp()]}))

    from chela import main

    with patch.object(sys, "argv", ["chela", "judge", "self-check", "--experiments",
                                     str(exp_path), "--test-cmd", TEST_CMD, "--cwd", str(root)]):
        with pytest.raises(SystemExit) as exc:
            main.main()
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "SURVIVED corruption" in out
    assert "DECORATION" in out


def test_cmd_judge_self_check_cli_exits_zero_when_every_guard_is_killed(tmp_path, capsys):
    root = _project(tmp_path / "repo", guard_test=REAL_GUARD_TEST)
    exp_path = tmp_path / "experiments.json"
    exp_path.write_text(json.dumps({"experiments": [_exp()]}))

    from chela import main

    with patch.object(sys, "argv", ["chela", "judge", "self-check", "--experiments",
                                     str(exp_path), "--test-cmd", TEST_CMD, "--cwd", str(root)]):
        main.main()      # the clean path never calls sys.exit — falling through IS exit 0
    assert "safe to commit" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# CMX-319 — the staleness check must not read its reference from the row it checks
# ---------------------------------------------------------------------------

def _blocking_run_with_heads(tmp_path, *, row_head, judged_head="oldsha000001",
                             task_id="abc123"):
    repo = _workflow_repo(tmp_path, task_id, FAKE_GUARD_TEST)
    with dispatcher._db() as conn:
        _run_row(conn, repo, task_id, judge_sha=judged_head, pr_head_sha=row_head)
    exp_file = tmp_path / "experiments.json"
    exp_file.write_text(json.dumps({"experiments": [_exp()]}))
    return exp_file


def test_a_STALE_pr_head_sha_column_no_longer_hides_a_dead_head(tmp_path):
    """⛔ CMX-319, the live incident. The row's `pr_head_sha` is ITSELF stale — frozen at the
    commit the judge is judging, because the run went `done` and nothing refreshed it after a
    rework pushed. The old check read its reference straight out of that column, so it
    compared `oldsha000001 != oldsha000001` — False by construction — concluded the head was
    fresh, and published a verdict about a commit that no longer exists as a CONFIRMED
    finding. On 2026-08-21 that put three false `SURVIVED` findings on PR #393 and had the
    decisions inbox escalate them as "needs a human look NOW".

    GitHub says the head is `newsha000002`, so this IS stale and must be reported as such.

    Revert `live_head` to `(live_run or {}).get("pr_head_sha")` and this goes red: the row
    agrees with the judged sha, staleness is never detected, and the run takes a rework round
    for a dead commit.
    """
    exp_file = _blocking_run_with_heads(tmp_path, row_head="oldsha000001")
    posted: list[str] = []
    with patch.object(dispatcher, "pr_live_head_sha", return_value="newsha000002"), \
         patch.object(dispatcher, "_post_pr_comment",
                      side_effect=lambda url, d, body: (posted.append(body), (True, ""))[1]):
        result = judge.judge_run("abc123", exp_file, cleanup=False)

    assert result["state"] == judge.J_STALE_HEAD, (
        f"a verdict about a dead commit was published as {result['state']!r} — the row's "
        "own pr_head_sha agreed with the judged sha, which is exactly the case the old "
        "self-referential check could never see"
    )
    run = dispatcher.resolve_run("abc123")
    assert (run["rework_count"] or 0) == 0, "a round was spent on a dead commit"
    assert run["judge_state"] != judge.J_BLOCKED_RACE, (
        "a superseded verdict was recorded as a confirmed race finding — that is what the "
        "inbox escalates at full severity"
    )
    assert posted and "newsha000002" in posted[0], (
        "the posted comment must name the newer commit that superseded this verdict"
    )


def test_a_genuinely_current_head_still_blocks_normally(tmp_path):
    """MUST BE ACCEPTED. The guard is worthless if it calls everything stale: when GitHub
    agrees the judged commit IS the head, a surviving guard is a real, current finding and
    must take its rework round exactly as before.

    This is the control for the test above — without it, `stale_head = True` unconditionally
    would pass that one while silently disabling every blocking verdict in the system.
    """
    exp_file = _blocking_run_with_heads(tmp_path, row_head="oldsha000001")
    with patch.object(dispatcher, "pr_live_head_sha", return_value="oldsha000001"), \
         patch.object(dispatcher, "_post_pr_comment",
                      side_effect=lambda url, d, body: (True, "")):
        result = judge.judge_run("abc123", exp_file, cleanup=False)

    assert result["state"] == judge.J_BLOCKED, (
        f"a current, confirmed finding was downgraded to {result['state']!r}"
    )
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "changes_requested"


def test_an_unreachable_github_degrades_to_the_row_never_to_a_false_freshness(tmp_path):
    """`pr_live_head_sha` returns None for every failure — gh missing, unauthenticated,
    timed out. That is UNKNOWN, and the caller must fall back to the row's column, i.e. to
    exactly the behaviour that shipped before CMX-319, rather than inventing staleness or
    asserting freshness. Here the row still carries the real mismatch, so it is detected.
    """
    exp_file = _blocking_run_with_heads(tmp_path, row_head="newsha000002")
    posted: list[str] = []
    with patch.object(dispatcher, "pr_live_head_sha", return_value=None), \
         patch.object(dispatcher, "_post_pr_comment",
                      side_effect=lambda url, d, body: (posted.append(body), (True, ""))[1]):
        result = judge.judge_run("abc123", exp_file, cleanup=False)

    assert result["state"] == judge.J_STALE_HEAD
    assert dispatcher.resolve_run("abc123")["rework_count"] == 0


def test_the_live_head_OVERRIDES_a_row_that_disagrees_with_github(tmp_path):
    """Precedence, pinned: GitHub is the authority and the row is only the fallback. A row
    that wrongly claims a DIFFERENT head must not manufacture staleness when GitHub says the
    judged commit is still current — otherwise a lagging column turns every real finding
    into a discarded one, which is the same failure inverted.
    """
    exp_file = _blocking_run_with_heads(tmp_path, row_head="rowsha000003")
    with patch.object(dispatcher, "pr_live_head_sha", return_value="oldsha000001"), \
         patch.object(dispatcher, "_post_pr_comment",
                      side_effect=lambda url, d, body: (True, "")):
        result = judge.judge_run("abc123", exp_file, cleanup=False)

    assert result["state"] == judge.J_BLOCKED, (
        "the row's stale disagreement overrode GitHub's answer — the live head must win"
    )


# ⚖️ CMX-319 round 1 rework. Every test above patches `dispatcher.pr_live_head_sha` itself
# via `patch.object`, so none of them ever executes the function's body or looks at what it
# was called with — a mutation that guts the body's `return` (or a wiring mutation that
# hands it `None, None` at the call site) is invisible to all of them. These call the real
# function against a faked `subprocess.run`, the same discipline `test_fetch_ci_jobs_*` in
# `tests/test_dispatcher_ci.py` uses for `_read_pr_checks`'s sibling reader.
class _GhResult:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_pr_live_head_sha_asks_gh_for_exactly_this_pr_in_this_repo():
    """The call-site arguments are what make the answer real: this must run
    ``gh pr view <number> --json headRefOid`` with ``cwd`` set to the repo it was handed —
    not some other PR, not some other checkout. A wiring mutation that hands the function
    ``None, None`` regardless of the real ``pr_url``/``repo_dir`` short-circuits before ever
    reaching this call, which is exactly what this pins."""
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _GhResult(stdout=json.dumps({"headRefOid": "livesha000009"}))

    with patch.object(dispatcher.subprocess, "run", side_effect=fake_run):
        sha = dispatcher.pr_live_head_sha(
            "https://github.com/o/r/pull/91", "/some/repo/dir"
        )

    assert sha == "livesha000009"
    assert captured["cmd"] == ["gh", "pr", "view", "91", "--json", "headRefOid"], (
        f"wrong argv: {captured['cmd']!r}"
    )
    assert captured["kwargs"].get("cwd") == "/some/repo/dir"
    # capture_output=False leaves out.stdout as None; json.loads(None) raises an uncaught
    # TypeError (not in the (JSONDecodeError, ValueError) catch), breaking the documented
    # "None on every failure path" contract on every real judge run.
    assert captured["kwargs"].get("capture_output") is True
    assert captured["kwargs"].get("timeout") == 20
    assert captured["kwargs"].get("errors") == "replace"


def test_pr_live_head_sha_reads_headRefOid_specifically_not_headRefName():
    """Pins the JSON key, not just that *something* came back. Swapping ``headRefOid`` for
    ``headRefName`` would make this return a branch name — every verdict's sha comparison
    would then compare unequal, and the caller's staleness guard would discard every
    blocking finding as stale. A payload that carries ONLY ``headRefOid`` (no
    ``headRefName`` at all) proves the value returned came from that key."""
    with patch.object(
        dispatcher.subprocess, "run",
        return_value=_GhResult(stdout=json.dumps({"headRefOid": "onlythiskey0001"})),
    ):
        sha = dispatcher.pr_live_head_sha("https://github.com/o/r/pull/1", "/repo")

    assert sha == "onlythiskey0001"


def test_pr_live_head_sha_is_None_with_no_pr_url_or_repo_dir():
    with patch.object(
        dispatcher.subprocess, "run",
        side_effect=AssertionError("must not shell out with nothing to ask about"),
    ):
        assert dispatcher.pr_live_head_sha(None, "/repo") is None
        assert dispatcher.pr_live_head_sha("not a pr url", "/repo") is None
        assert dispatcher.pr_live_head_sha("https://github.com/o/r/pull/1", None) is None
        assert dispatcher.pr_live_head_sha("https://github.com/o/r/pull/1", "") is None


def test_pr_live_head_sha_is_None_when_gh_cannot_be_executed():
    with patch.object(dispatcher.subprocess, "run", side_effect=OSError("no gh on PATH")):
        assert dispatcher.pr_live_head_sha("https://github.com/o/r/pull/1", "/repo") is None


def test_pr_live_head_sha_is_None_when_gh_times_out():
    with patch.object(
        dispatcher.subprocess, "run",
        side_effect=subprocess.TimeoutExpired(cmd=["gh"], timeout=20),
    ):
        assert dispatcher.pr_live_head_sha("https://github.com/o/r/pull/1", "/repo") is None


def test_pr_live_head_sha_is_None_on_a_non_zero_exit():
    """``stdout`` here is a WELL-FORMED payload that would happily parse to a sha — gh can
    print a stale/partial body alongside a non-zero exit. If the returncode check is
    dead-coded (e.g. ``if False and out.returncode != 0``), this must NOT stay None by
    accident of ``json.loads("")`` raising on an empty default; it would instead return
    ``"shouldnotbeused"``, which is what makes this pin the returncode check itself rather
    than merely reproducing the empty-stdout case a dead-coded check also happens to catch."""
    with patch.object(
        dispatcher.subprocess, "run",
        return_value=_GhResult(
            returncode=1,
            stdout=json.dumps({"headRefOid": "shouldnotbeused"}),
            stderr="gh: rate limited",
        ),
    ):
        assert dispatcher.pr_live_head_sha("https://github.com/o/r/pull/1", "/repo") is None


def test_pr_live_head_sha_is_None_on_unparseable_json():
    with patch.object(
        dispatcher.subprocess, "run", return_value=_GhResult(stdout="not json{{{"),
    ):
        assert dispatcher.pr_live_head_sha("https://github.com/o/r/pull/1", "/repo") is None


def test_pr_live_head_sha_is_None_when_json_is_not_an_object():
    """Valid JSON, wrong shape (a bare list) — ``.get`` on it would raise, and this
    function's whole contract is that it never raises."""
    with patch.object(
        dispatcher.subprocess, "run", return_value=_GhResult(stdout=json.dumps([1, 2, 3])),
    ):
        assert dispatcher.pr_live_head_sha("https://github.com/o/r/pull/1", "/repo") is None


def test_pr_live_head_sha_is_None_when_headRefOid_is_missing_or_blank():
    with patch.object(
        dispatcher.subprocess, "run", return_value=_GhResult(stdout=json.dumps({})),
    ):
        assert dispatcher.pr_live_head_sha("https://github.com/o/r/pull/1", "/repo") is None
    with patch.object(
        dispatcher.subprocess, "run",
        return_value=_GhResult(stdout=json.dumps({"headRefOid": "   "})),
    ):
        assert dispatcher.pr_live_head_sha("https://github.com/o/r/pull/1", "/repo") is None


def test_judge_run_calls_pr_live_head_sha_with_THIS_runs_real_pr_url_and_repo_dir(tmp_path):
    """The [WIRING] shape: a call site that hands `pr_live_head_sha` `None, None` instead of
    `pr_url, repo_dir` makes the function short-circuit on `if not number or not repo_dir`
    and return `None` — falling back to the row, which is exactly the pre-CMX-319 hole this
    PR closes, while every existing test still passes because they all stub
    `pr_live_head_sha` wholesale via `patch.object` and never look at what it was called
    with. This does not stub the function at all: it fakes `subprocess.run` underneath the
    REAL `pr_live_head_sha` and asserts the argv it built names THIS run's actual PR number
    (91, parsed from the row's `https://github.com/o/r/pull/91`) and THIS run's actual repo
    dir — not a call that could never look anything real up.

    The row's `pr_head_sha` ("oldsha000001") matches the judged sha, so if the call site
    ever degrades to `None, None` (real `pr_live_head_sha` returns `None` on that input,
    caller falls back to the row) this goes GREEN (`J_BLOCKED`, not `J_STALE_HEAD`) instead
    of red — a genuine wiring regression must flip the observed state, not just the argv.
    """
    exp_file = _blocking_run_with_heads(tmp_path, row_head="oldsha000001")
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _GhResult(stdout=json.dumps({"headRefOid": "newsha000002"}))

    with patch.object(dispatcher.subprocess, "run", side_effect=fake_run), \
         patch.object(dispatcher, "_post_pr_comment",
                      side_effect=lambda url, d, body: (True, "")):
        result = judge.judge_run("abc123", exp_file, cleanup=False)

    assert captured.get("cmd") == ["gh", "pr", "view", "91", "--json", "headRefOid"], (
        f"pr_live_head_sha was not reached with this run's real PR — got {captured!r}"
    )
    repo = dispatcher.resolve_run("abc123")["worktree_path"]
    assert captured["kwargs"].get("cwd") == repo
    assert result["state"] == judge.J_STALE_HEAD, (
        f"got {result['state']!r} — GitHub's live head (newsha000002) disagrees with the "
        "judged commit (oldsha000001), which the row alone agrees with; a call site "
        "degraded to None, None would fall back to the row and wrongly report this current"
    )

