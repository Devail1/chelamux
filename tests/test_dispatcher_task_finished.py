"""⚖️🔎 CMX-250 — ``chela task-finished`` binds Done Criteria #3's OUTCOME, not just its
invocation.

CMX-249 shipped ``chela judge self-check``: the judge's own apply_mutation / parse_check /
run_suite / adjudicate mechanics, runnable by an agent against its own worktree before it
commits. Nothing downstream ever read the result — an agent could run it, watch it print
"SURVIVED corruption", and commit anyway with zero consequence. These tests pin the gate
that closes that: ``dispatcher.verify_self_check`` re-runs the SAME mechanics against the
run's OWN worktree (looked up by task_id, not a bare ``--cwd`` the caller could point
anywhere), and ``cmd_task_finished`` refuses the awaiting_review transition when a guard
SURVIVED or the check CANNOT VERIFY — the outcome blocks, not just the habit of running it.

⛔ Review round 1 found the seam: the refusal logic and ``verify_self_check`` were each
guarded, but every CLI test mocked ``dispatcher.verify_self_check`` with a canned dict, so
neither the argument the CLI actually passes through nor an ``ok: False`` self-check ever
met the real function. The "end-to-end" block below drives ``cmd_task_finished`` against
the REAL ``verify_self_check`` (a real worktree, a real pytest subprocess), mocking only
``mark_awaiting_review`` — closing that gap by construction rather than by inspection. The
``check_no_new_guards`` block covers the opt-out review round 1 also asked for: a
report-only cross-check of ``--no-new-guards`` against the run's own diff.
"""
from __future__ import annotations

import ast
import inspect
import json
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest

from chela import dispatcher, event_log

TEST_CMD = f'"{sys.executable}" -m pytest -q'

GUARD_PY = '''\
def chip(state):
    glyph = "*" if state == "on" else "-"
    return {"glyph": glyph}
'''

REAL_GUARD_TEST = '''\
from guard import chip

def test_the_glyph_is_not_empty():
    assert chip("on")["glyph"] != ""
'''

FAKE_GUARD_TEST = '''\
from guard import chip

def test_a_chip_exists():
    assert "glyph" in chip("on")
'''

GLYPH_BEFORE = '    glyph = "*" if state == "on" else "-"'
GLYPH_AFTER = '    glyph = ""'


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *args],
        check=True, capture_output=True,
    )


def _project(root: Path, guard_test: str = REAL_GUARD_TEST) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "guard.py").write_text(GUARD_PY)
    (root / "test_guard.py").write_text(guard_test)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "the feature and its proof")
    return root


def _repo_with_origin(
    root: Path, branch: str = "master", extra_base_files: dict[str, str] | None = None,
) -> str:
    """A git repo whose HEAD is also ``refs/remotes/origin/<branch>`` — no real remote
    needed, just a ref :func:`dispatcher.check_no_new_guards` can resolve ``origin/<base
    branch>`` against. Returns the base sha; the caller commits ON TOP of it to build a
    diff.

    ⛔ CMX-258 rework round 4, findings 1-2: the base already carries an existing
    ``tests/test_existing.py`` — a caller that MODIFIES it (rather than adding a brand-new
    tests/ file) is the only way to distinguish "the diff touches tests/" from "the diff
    ADDS a file under tests/", since every fixture before this one built its guard as a
    fresh ADD on top of a base that had no tests/ file at all.

    ``extra_base_files`` lets a caller seed additional tracked files into the BASE commit
    (so a later commit can MODIFY or DELETE them to build a diff on those axes) without
    disturbing every other caller of this helper, which passes nothing.
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text("hi\n")
    (root / "tests").mkdir()
    (root / "tests" / "test_existing.py").write_text("def test_x():\n    assert True\n")
    for rel, content in (extra_base_files or {}).items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    _git(root, "init", "-q", "-b", branch)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    base_sha = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    _git(root, "update-ref", f"refs/remotes/origin/{branch}", base_sha)
    return base_sha


def _exp(**over) -> dict:
    exp = {"guard": "the glyph cue", "kind": "mutation", "file": "guard.py",
           "before": GLYPH_BEFORE, "after": GLYPH_AFTER}
    exp.update(over)
    return exp


def _workflow_md(tmp_path: Path, base_branch: str | None = None) -> Path:
    p = tmp_path / "WORKFLOW.md"
    workspace = f"\nworkspace:\n  base_branch: {base_branch}" if base_branch else ""
    p.write_text(
        "---\nproject_key: TEST\njudge:\n  test_cmd: " + json.dumps(TEST_CMD) +
        "\n  suite_timeout_seconds: 120" + workspace + "\n---\nbody\n"
    )
    return p


@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")


def _insert_run(task_id: str, worktree_path, workflow_path) -> None:
    with dispatcher._db() as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, window_name, "
            "worktree_path, branch_name, started_at, attempt, task_number) "
            "VALUES (?, ?, 'do a thing', 'running', 'cmx-1', ?, 'cmx-1', "
            "'2026-08-12T10:00:00+00:00', 1, 1)",
            (task_id, str(workflow_path), str(worktree_path)),
        )
        conn.commit()


# --- dispatcher.verify_self_check -----------------------------------------------------


def test_verify_self_check_blocks_when_a_guard_survives(tmp_path):
    root = _project(tmp_path / "wt", guard_test=FAKE_GUARD_TEST)
    wf = _workflow_md(tmp_path)
    exp_path = tmp_path / "experiments.json"
    exp_path.write_text(json.dumps({"experiments": [_exp()]}))
    _insert_run("t1", root, wf)

    result = dispatcher.verify_self_check("t1", str(exp_path))

    assert result["ok"]
    assert result["blocking"] == 1
    assert not result["cannot_verify"]
    # ⛔ the per-experiment outcomes must reach the caller, not just the blocking COUNT —
    # they are the only actionable half of a refusal (CMX-250 review round 1, finding 4).
    assert [o["verdict"] for o in result["outcomes"]] == ["SURVIVED"]
    assert result["outcomes"][0]["file"] == "guard.py"
    assert result["outcomes"][0]["guard"] == "the glyph cue"


def test_verify_self_check_clears_when_every_guard_holds(tmp_path):
    root = _project(tmp_path / "wt", guard_test=REAL_GUARD_TEST)
    wf = _workflow_md(tmp_path)
    exp_path = tmp_path / "experiments.json"
    exp_path.write_text(json.dumps({"experiments": [_exp()]}))
    _insert_run("t1", root, wf)

    result = dispatcher.verify_self_check("t1", str(exp_path))

    assert result["ok"]
    assert result["blocking"] == 0
    assert not result["cannot_verify"]
    assert [o["verdict"] for o in result["outcomes"]] == ["KILLED"]
    assert result["outcomes"][0]["file"] == "guard.py"
    assert result["outcomes"][0]["guard"] == "the glyph cue"


def test_verify_self_check_propagates_error_when_the_check_itself_cannot_run(tmp_path):
    """⛔ CMX-250 review round 1, finding 2: ``run_self_check``'s own ``ok: False`` (an
    experiments file that does not load, a ``WORKFLOW.md`` that does not parse, ...) must
    reach the caller as-is, not be swallowed into a false ``ok: True``."""
    root = _project(tmp_path / "wt", guard_test=REAL_GUARD_TEST)
    wf = _workflow_md(tmp_path)
    _insert_run("t1", root, wf)

    result = dispatcher.verify_self_check("t1", str(tmp_path / "no-such-experiments.json"))

    assert not result["ok"]
    assert "does not exist" in result["error"]


def test_verify_self_check_forwards_a_nonempty_cannot_verify_even_when_ok(tmp_path):
    """⛔ CMX-250 review round 3, finding 1: when ``run_self_check`` comes back ``ok: True``
    with a non-empty ``cannot_verify`` (an empty experiments list — nothing was corrupted,
    so nothing was proven), that value must reach the caller VERBATIM. Every other test
    here only ever exercises ``cannot_verify == ""``, so a mutation that unconditionally
    forwards ``""`` regardless of what ``run_self_check`` actually returned stayed
    invisible — the refusal arm ``main.py`` exits 1 on reads this exact field."""
    root = _project(tmp_path / "wt", guard_test=REAL_GUARD_TEST)
    wf = _workflow_md(tmp_path)
    exp_path = tmp_path / "experiments.json"
    exp_path.write_text(json.dumps({"experiments": []}))
    _insert_run("t1", root, wf)

    result = dispatcher.verify_self_check("t1", str(exp_path))

    assert result["ok"]
    assert result["blocking"] == 0
    assert "no experiments were given" in result["cannot_verify"]


def test_verify_self_check_unknown_task_id_errors(tmp_path):
    """⛔ CMX-258 review round 2, finding 2: the refusal must say WHICH task_id had no
    run — round 5 finding 3's 'the WHY is the only actionable half' rule applied to this
    path. Pinning only the constant 'no run found' leaves the mutation that drops the
    task_id interpolation (`f"no run found for task_id {task_id}"` →
    `"no run found for task_id"`) invisible, since the message text is unchanged."""
    result = dispatcher.verify_self_check("no-such-task", str(tmp_path / "e.json"))

    assert not result["ok"]
    assert "no run found" in result["error"]
    assert "no-such-task" in result["error"]


def test_verify_self_check_missing_worktree_path_errors(tmp_path):
    wf = _workflow_md(tmp_path)
    with dispatcher._db() as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, window_name, "
            "worktree_path, branch_name, started_at, attempt, task_number) "
            "VALUES ('t1', ?, 'do a thing', 'running', 'cmx-1', NULL, 'cmx-1', "
            "'2026-08-12T10:00:00+00:00', 1, 1)",
            (str(wf),),
        )
        conn.commit()

    result = dispatcher.verify_self_check("t1", str(tmp_path / "e.json"))

    assert not result["ok"]
    assert "worktree_path" in result["error"]


def test_verify_self_check_uses_the_row_matching_its_own_task_id(tmp_path):
    """⛔ CMX-250 review round 4, finding 1: every test above inserts exactly ONE run, so
    nothing proves the lookup actually FILTERS by ``task_id`` rather than fetching any row
    in the table. Insert a decoy run FIRST whose guard SURVIVES, then the real run "t1"
    whose guard is KILLED — a neutered ``WHERE task_id=?`` (e.g. ``... OR 1=1``) fetches
    whichever row the table returns first (the decoy, inserted first) and reports a
    surviving guard for a run that never had one."""
    wf = _workflow_md(tmp_path)
    exp_path = tmp_path / "experiments.json"
    exp_path.write_text(json.dumps({"experiments": [_exp()]}))

    decoy_root = _project(tmp_path / "decoy-wt", guard_test=FAKE_GUARD_TEST)
    _insert_run("decoy", decoy_root, wf)

    root = _project(tmp_path / "wt", guard_test=REAL_GUARD_TEST)
    _insert_run("t1", root, wf)

    result = dispatcher.verify_self_check("t1", str(exp_path))

    assert result["ok"]
    assert result["blocking"] == 0
    assert [o["verdict"] for o in result["outcomes"]] == ["KILLED"]


def test_verify_self_check_uses_the_runs_own_judge_test_cmd_not_a_hardcoded_default(tmp_path):
    """⛔ CMX-258 rework round 12 (judge finding 3, MUTATION): the self-check must be measured
    against THE RUN'S OWN ``judge.test_cmd`` — read from its own WORKFLOW.md via
    ``workflow_path`` — not an ambient default a caller could hardcode instead.
    ``run_self_check``'s own docstring says this is exactly why: 'a self-check that comes
    back clean was measured against the exact suite the judge will later measure against,
    not a narrower one an agent could pick to always pass'. Point this run's own
    ``judge.test_cmd`` at a command that never runs pytest at all and always fails; a
    hardcoded ``sys.executable -m pytest -q`` fallback would instead run the REAL (green)
    suite and report zero blocking — an outcome indistinguishable from every other test in
    this file, since they all happen to use that exact command as their own ``TEST_CMD``
    too. The baseline-red message interpolates the command it ran, so asserting the bogus
    command appears in ``cannot_verify`` is the direct proof this run's own test_cmd was
    used, not a substitute for it."""
    root = _project(tmp_path / "wt", guard_test=REAL_GUARD_TEST)
    bogus_cmd = f'"{sys.executable}" -c "import sys; sys.exit(7)"'
    wf = tmp_path / "WORKFLOW.md"
    wf.write_text(
        "---\nproject_key: TEST\njudge:\n  test_cmd: " + json.dumps(bogus_cmd) +
        "\n  suite_timeout_seconds: 120\n---\nbody\n"
    )
    exp_path = tmp_path / "experiments.json"
    exp_path.write_text(json.dumps({"experiments": [_exp()]}))
    _insert_run("t1", root, wf)

    result = dispatcher.verify_self_check("t1", str(exp_path))

    assert result["ok"]
    assert bogus_cmd in result["cannot_verify"]
    assert "NOT GREEN before any mutation" in result["cannot_verify"]


# --- cmd_task_finished CLI: flag validation and gating ---------------------------------


def test_cmd_task_finished_rejects_both_flags_at_once(tmp_path, capsys):
    from chela import main

    with patch.object(sys, "argv", ["chela", "task-finished", "t1",
                                     "--self-check-experiments", str(tmp_path / "e.json"),
                                     "--no-new-guards"]):
        with pytest.raises(SystemExit) as exc:
            main.main()
    assert exc.value.code == 2
    assert "at most one of" in capsys.readouterr().out


def test_cmd_task_finished_refuses_transition_when_self_check_blocks(tmp_path, capsys):
    from chela import main

    with patch.object(dispatcher, "verify_self_check",
                       return_value={"ok": True, "blocking": 1, "cannot_verify": "",
                                     "outcomes": [{"verdict": "SURVIVED", "file": "guard.py",
                                                    "guard": "the glyph cue"}]}), \
         patch.object(dispatcher, "mark_awaiting_review") as mark:
        with patch.object(sys, "argv", ["chela", "task-finished", "t1",
                                         "--self-check-experiments", str(tmp_path / "e.json")]):
            with pytest.raises(SystemExit) as exc:
                main.main()
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "DECORATION" in out
    # ⛔ CMX-250 review round 5, finding 2: the per-outcome print must show WHICH verdict
    # went with WHICH guard, not just the file/guard text — "SURVIVED" alone already
    # appears in the unrelated summary line below ("1 guard(s) SURVIVED corruption"), so a
    # mutation that drops the `[{verdict:8}] ` prefix from the per-outcome loop stayed
    # invisible to a substring check on "SURVIVED" or on "guard.py: the glyph cue" alone.
    assert "[SURVIVED] guard.py: the glyph cue" in out
    mark.assert_not_called()      # ⛔ the transition must never happen on a blocked self-check


def test_cmd_task_finished_refuses_transition_when_self_check_cannot_verify(tmp_path, capsys):
    from chela import main

    with patch.object(dispatcher, "verify_self_check",
                       return_value={"ok": True, "blocking": 0,
                                     "cannot_verify": "the suite is NOT GREEN", "outcomes": []}), \
         patch.object(dispatcher, "mark_awaiting_review") as mark:
        with patch.object(sys, "argv", ["chela", "task-finished", "t1",
                                         "--self-check-experiments", str(tmp_path / "e.json")]):
            with pytest.raises(SystemExit) as exc:
                main.main()
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "CANNOT VERIFY" in out
    # ⛔ CMX-258 rework round 1, finding 1: "CANNOT VERIFY" alone is a constant in main.py —
    # a mutation that drops the interpolated `{check['cannot_verify']}` reason stays
    # invisible to that substring check. The WHY is the only actionable half; pin it.
    assert "the suite is NOT GREEN" in out
    mark.assert_not_called()


def test_cmd_task_finished_proceeds_when_self_check_is_clean(tmp_path, capsys):
    from chela import main

    with patch.object(dispatcher, "verify_self_check",
                       return_value={"ok": True, "blocking": 0, "cannot_verify": "",
                                     "outcomes": [{"verdict": "KILLED", "file": "guard.py",
                                                    "guard": "the glyph cue"}]}), \
         patch.object(dispatcher, "mark_awaiting_review",
                       return_value={"ok": True, "task_id": "t1", "pr_url": "https://x/1"}):
        with patch.object(sys, "argv", ["chela", "task-finished", "t1",
                                         "--self-check-experiments", str(tmp_path / "e.json")]):
            main.main()      # falls through — no sys.exit on the clean path
    out = capsys.readouterr().out
    assert "every guard held" in out
    assert "awaiting review" in out
    # ⛔ CMX-250 review round 5, finding 2 (sibling, KILLED side): "KILLED" appears NOWHERE
    # else in the clean-path output, so this pins the per-outcome verdict prefix without
    # depending on the unrelated SURVIVED summary line the blocked-path test can lean on.
    assert "[KILLED  ] guard.py: the glyph cue" in out


def test_cmd_task_finished_no_new_guards_skips_self_check_and_proceeds(capsys):
    from chela import main

    with patch.object(dispatcher, "verify_self_check") as verify, \
         patch.object(dispatcher, "mark_awaiting_review",
                       return_value={"ok": True, "task_id": "t1", "pr_url": "https://x/1"}):
        with patch.object(sys, "argv", ["chela", "task-finished", "t1", "--no-new-guards"]):
            main.main()
    verify.assert_not_called()   # ⛔ --no-new-guards must never invoke self-check
    assert "awaiting review" in capsys.readouterr().out


def test_cmd_task_finished_with_neither_flag_warns_but_still_proceeds(capsys):
    """⛔ Backward compatibility: a run dispatched under an older WORKFLOW.md never learned
    these flags exist and must not be broken by a gate it was never told to satisfy.

    ⛔ CMX-258 rework round 10 (judge finding 1): the bare 'was not enforced' fact was the
    only thing pinned here — a mutation that strips the notice down to that constant half
    and drops the actionable half (which flag to pass NEXT time) survived every test. This
    is the ONLY channel that tells an agent dispatched under an older WORKFLOW.md which
    flag exists; pin both flag names too, not just the fact that something was skipped."""
    from chela import main

    with patch.object(dispatcher, "verify_self_check") as verify, \
         patch.object(dispatcher, "mark_awaiting_review",
                       return_value={"ok": True, "task_id": "t1", "pr_url": "https://x/1"}):
        with patch.object(sys, "argv", ["chela", "task-finished", "t1"]):
            main.main()
    verify.assert_not_called()
    out = capsys.readouterr().out
    assert "was not enforced" in out
    assert "--self-check-experiments <path>" in out
    assert "--no-new-guards" in out
    assert "awaiting review" in out


# --- end-to-end: cmd_task_finished driving the REAL dispatcher.verify_self_check --------
#
# ⛔ CMX-250 review round 1 ("THE JUDGE"): every CLI test above patches
# `dispatcher.verify_self_check` with a canned dict, so two things were unmeasured — which
# `experiments` PATH the CLI actually passes through (a bare `""` would still look like a
# valid call to a mock), and whether an `ok: False` self-check truly refuses the
# transition end to end. These drive `cmd_task_finished` against the REAL
# `dispatcher.verify_self_check` (a real worktree, a real pytest subprocess), mocking only
# `mark_awaiting_review` — git/tmux, not this gate.


def test_cmd_task_finished_end_to_end_blocks_on_a_surviving_guard(tmp_path, capsys):
    from chela import main

    root = _project(tmp_path / "wt", guard_test=FAKE_GUARD_TEST)
    wf = _workflow_md(tmp_path)
    exp_path = tmp_path / "experiments.json"
    exp_path.write_text(json.dumps({"experiments": [_exp()]}))
    _insert_run("t1", root, wf)

    with patch.object(dispatcher, "mark_awaiting_review") as mark:
        with patch.object(sys, "argv", ["chela", "task-finished", "t1",
                                         "--self-check-experiments", str(exp_path)]):
            with pytest.raises(SystemExit) as exc:
                main.main()
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "DECORATION" in out
    # ⛔ CMX-250 review round 3, finding 4: which guard survived is the only actionable
    # half of the refusal — the per-outcome print loop must actually run, not just the
    # blocking COUNT it feeds into. Round 5, finding 2: the verdict itself must be in that
    # line too — "SURVIVED" alone is unpinning, since it also appears in the summary line.
    assert "[SURVIVED] guard.py: the glyph cue" in out
    mark.assert_not_called()


def test_cmd_task_finished_end_to_end_proceeds_when_every_guard_holds(tmp_path, capsys):
    from chela import main

    root = _project(tmp_path / "wt", guard_test=REAL_GUARD_TEST)
    wf = _workflow_md(tmp_path)
    exp_path = tmp_path / "experiments.json"
    exp_path.write_text(json.dumps({"experiments": [_exp()]}))
    _insert_run("t1", root, wf)

    with patch.object(dispatcher, "mark_awaiting_review",
                       return_value={"ok": True, "task_id": "t1",
                                     "pr_url": "https://x/1"}) as mark:
        with patch.object(sys, "argv", ["chela", "task-finished", "t1",
                                         "--self-check-experiments", str(exp_path)]):
            main.main()
    out = capsys.readouterr().out
    assert "every guard held" in out
    assert "awaiting review" in out
    # ⛔ CMX-250 review round 5, finding 2: pin the verdict in the per-outcome line, not
    # just the file/guard text — "KILLED" appears nowhere else on this clean path.
    assert "[KILLED  ] guard.py: the glyph cue" in out
    mark.assert_called_once_with("t1")


def test_cmd_task_finished_end_to_end_refuses_when_self_check_cannot_run(tmp_path, capsys):
    from chela import main

    root = _project(tmp_path / "wt", guard_test=REAL_GUARD_TEST)
    wf = _workflow_md(tmp_path)
    _insert_run("t1", root, wf)

    with patch.object(dispatcher, "mark_awaiting_review") as mark:
        with patch.object(sys, "argv", ["chela", "task-finished", "t1",
                                         "--self-check-experiments",
                                         str(tmp_path / "no-such-experiments.json")]):
            with pytest.raises(SystemExit) as exc:
                main.main()
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "self-check could not run" in out
    # ⛔ CMX-250 review round 5, finding 3: the WHY — the actual `check["error"]` text — is
    # the only actionable half of this refusal; "self-check could not run" alone is a
    # literal in `main.py` and stays green even if the f-string interpolation is dropped.
    assert "no-such-experiments.json" in out
    assert "does not exist" in out
    mark.assert_not_called()


def test_cmd_task_finished_reports_the_exact_error_text_when_self_check_could_not_run(capsys):
    """⛔ CMX-250 review round 5, finding 3 (sibling): pins the interpolation directly
    against a mocked `dispatcher.verify_self_check`, independent of whatever wording
    `judge.load_experiments` happens to use today."""
    from chela import main

    with patch.object(dispatcher, "verify_self_check",
                       return_value={"ok": False, "error": "a very specific reason"}), \
         patch.object(dispatcher, "mark_awaiting_review") as mark:
        with patch.object(sys, "argv", ["chela", "task-finished", "t1",
                                         "--self-check-experiments", "e.json"]):
            with pytest.raises(SystemExit) as exc:
                main.main()
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "self-check could not run" in out
    assert "a very specific reason" in out
    mark.assert_not_called()


def test_cmd_task_finished_self_check_forwards_its_own_task_id_not_a_hardcoded_one(capsys):
    """⛔ CMX-250 round 8, finding 1 (closed up front on re-scope, quoted verbatim):
    ``cmd_task_finished`` must self-check THE RUN IT WAS INVOKED FOR — the CLI's
    forwarding of its own ``task_id`` into ``verify_self_check``.

        -         check = dispatcher.verify_self_check(args.task_id, experiments)
        +         check = dispatcher.verify_self_check("t1", experiments)

    Every other test in this file invokes ``task-finished`` with the literal ``"t1"``, so a
    hardcoded ``"t1"`` in production satisfies every one of them — including
    ``check.assert_called_once_with("t1")`` above. Use a task_id that is NOT ``"t1"`` so the
    mutation goes red."""
    from chela import main

    with patch.object(dispatcher, "verify_self_check",
                       return_value={"ok": True, "blocking": 0, "cannot_verify": "",
                                     "outcomes": []}) as verify, \
         patch.object(dispatcher, "mark_awaiting_review",
                       return_value={"ok": True, "task_id": "cmx-777",
                                     "pr_url": "https://x/1"}):
        with patch.object(sys, "argv", ["chela", "task-finished", "cmx-777",
                                         "--self-check-experiments", "e.json"]):
            main.main()
    verify.assert_called_once_with("cmx-777", "e.json")


# --- dispatcher.check_no_new_guards -----------------------------------------------------
#
# ⚖️🔎 CMX-250 review round 1, finding 2: `--no-new-guards` was a bare self-declaration
# nothing cross-checked. This is report-only — it must never refuse the transition, only
# make a wrong opt-out visible.


def test_check_no_new_guards_true_and_logs_an_event_when_diff_touches_tests(tmp_path, monkeypatch):
    """⛔ CMX-258 review round 2, finding 1: the event must name THE RUN IT WAS INVOKED
    FOR — round 8's exact task_id-forwarding class, at the one place a human reads it.
    Use a task_id that is NOT the literal "t1" (the same reason the CLI-level forwarding
    tests use "cmx-777"), so a hardcoded `payload={"task_id": "t1", ...}` goes red instead
    of coincidentally satisfying the assertion because the run under test IS "t1".

    ⛔ CMX-258 rework round 4, findings 1-2: MODIFY the base's existing
    `tests/test_existing.py` (not just add a new one) — the only way to distinguish
    'the diff touches tests/' from 'the diff ADDS a file under tests/', which
    `--diff-filter=A` would also satisfy. And touch a root-level `tests_helper.py` — a path
    that STARTS WITH "tests" but is NOT under the tests/ directory — so a filter of
    `startswith("tests")` (missing the trailing separator) wrongly pulls it into `touched`
    and the `files == [...]` assertion below catches it.

    ⛔ CMX-258 rework round 5, findings 2-3: the base also carries `tests/conftest.py`
    (MODIFIED here, not just test_existing.py) — a non-`test_*`-named path under tests/, so
    a filter narrowed to `startswith("tests/test_")` would silently drop it from `touched`
    — and `tests/test_doomed.py` (DELETED here) — a `git diff --diff-filter=d` (excluding
    deletions) would silently drop it too. All three real tests/ paths must show up in the
    payload for the cross-check to mean "the diff touches tests/" rather than one of those
    narrower, uncheckable accidents.
    """
    monkeypatch.setenv("CHELA_EVENTS_FILE", str(tmp_path / "events.jsonl"))
    root = tmp_path / "wt"
    _repo_with_origin(root, extra_base_files={
        "tests/conftest.py": "import pytest\n",
        "tests/test_doomed.py": "def test_doomed():\n    assert True\n",
    })
    (root / "tests" / "test_existing.py").write_text(
        "def test_x():\n    assert True\n\n\ndef test_y():\n    assert True\n"
    )
    (root / "tests" / "conftest.py").write_text("import pytest  # changed\n")
    (root / "tests" / "test_doomed.py").unlink()
    (root / "tests_helper.py").write_text("x = 1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "change/delete guards and touch a tests-prefixed decoy path")
    wf = _workflow_md(tmp_path)
    _insert_run("cmx-778", root, wf)

    result = dispatcher.check_no_new_guards("cmx-778")

    assert result is True
    events = event_log.read()["events"]
    matches = [e for e in events if e.get("type") == "no_new_guards_mismatch"]
    assert len(matches) == 1
    assert matches[0]["payload"]["task_id"] == "cmx-778"
    assert matches[0]["payload"]["files"] == [
        "tests/conftest.py", "tests/test_doomed.py", "tests/test_existing.py",
    ]
    # ⛔ CMX-250 review round 5, finding 4: the payload alone isn't what a human reads on
    # the dashboard — the event's human-readable summary must say WHAT happened, not be
    # blanked to "". Pin the actual words, not just that the field is non-empty.
    assert "no-new-guards was passed" in matches[0]["summary"]
    assert "touches tests/" in matches[0]["summary"]
    assert "3 file(s)" in matches[0]["summary"]
    # ⛔ CMX-258 review round 2 non-blocking note: the summary leads with the task_id too
    # — pin both halves of the same unpinned binding in one guard.
    assert matches[0]["summary"].startswith("cmx-778: ")


@pytest.mark.parametrize(
    "path, expected",
    [
        pytest.param("tests/test_added.py", True, id="added-under-tests"),
        pytest.param("tests/sub/test_nested.py", True, id="nested-under-top-level-tests"),
        pytest.param("tests/conftest.py", True, id="non-test-underscore-named-under-tests"),
        pytest.param("tests_helper.py", False, id="starts-with-tests-no-slash"),
        pytest.param("tests-helpers/x.py", False, id="starts-with-tests-hyphen"),
        pytest.param("chela/dashboard/widget.test.mjs", True, id="dot-test-mjs-outside-tests-dir"),
        pytest.param("tests/widget.test.mjs", True, id="dot-test-mjs-under-tests-dir"),
        pytest.param("chela/dashboard/static/app.mjs", False, id="plain-mjs-module-is-not-a-guard"),
        pytest.param("chela/tests/x.py", False, id="nested-tests-dir-outside-top-level"),
        pytest.param("README.md", False, id="no-guard-touched-at-all"),
    ],
)
def test_check_no_new_guards_path_classification_table(tmp_path, path, expected):
    """⛔ CMX-258 rework round 6: the human retry brief asked for the WHOLE definition
    ``check_no_new_guards`` cross-checks to be written down and table-tested, instead of one
    clause being closed per round while an adversary enumerates the next. This is that
    table — each row commits exactly ONE new/changed path on top of the same clean base and
    asserts whether it counts as touching a guard, per :func:`dispatcher._is_guard_path`:

    * ``tests/test_added.py`` / ``tests/sub/test_nested.py`` / ``tests/conftest.py`` — three
      shapes of "under the top-level ``tests/`` directory" (direct, nested, non-``test_``-
      named) all count.
    * ``tests_helper.py`` / ``tests-helpers/x.py`` — two shapes of "merely starts with the
      letters tests" must NOT count; neither is under the ``tests/`` directory.
    * ``chela/dashboard/widget.test.mjs`` / ``tests/widget.test.mjs`` — a ``*.test.mjs`` JS
      suite counts as a guard everywhere in the repo, not just under ``tests/`` — this is
      round 6's blocking finding: ``tests/test_js_suites.py`` globs and runs ``*.test.mjs``
      from the whole tree (a real one, ``chela/dashboard/static/collab/fit.test.mjs``,
      already lives outside ``tests/``), so a cross-check keyed on ``.py`` files under
      ``tests/`` alone is blind to all of them.
    * ``chela/dashboard/static/app.mjs`` — the negative control on the EXTENSION axis, round
      8's blocking finding: a plain ``.mjs`` module (not ``.test.mjs``, not under ``tests/``)
      must NOT count. Nothing in this repo's suites executes a plain ``.mjs`` file, so
      widening the clause from ``endswith(".test.mjs")`` to ``endswith(".mjs")`` would
      misclassify it as a guard while every other row here stayed green.
    * ``chela/tests/x.py`` — a Python file under a NESTED ``tests/`` directory must NOT
      count: pytest's own ``testpaths = ["tests"]`` (``pyproject.toml``) never collects it,
      so mutating it is not protected by anything this repo's suite runs. This resolves the
      axis round 5's own note called "undecided by design" — decided here, not accidentally.
    * ``README.md`` — the negative control: a diff that touches no guard at all must report
      no mismatch.
    """
    root = tmp_path / "wt"
    _repo_with_origin(root)
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("guard or decoy content\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", f"touch {path}")
    wf = _workflow_md(tmp_path)
    _insert_run("t1", root, wf)

    assert dispatcher.check_no_new_guards("t1") is expected


def test_check_no_new_guards_false_and_logs_nothing_when_diff_is_clean(tmp_path, monkeypatch):
    monkeypatch.setenv("CHELA_EVENTS_FILE", str(tmp_path / "events.jsonl"))
    root = tmp_path / "wt"
    _repo_with_origin(root)
    (root / "README.md").write_text("hi, updated\n")
    _git(root, "commit", "-aqm", "docs tweak, no guard")
    wf = _workflow_md(tmp_path)
    _insert_run("t1", root, wf)

    result = dispatcher.check_no_new_guards("t1")

    assert result is False
    assert not (tmp_path / "events.jsonl").exists()


def test_check_no_new_guards_unknown_when_origin_ref_does_not_resolve(tmp_path):
    # `_project` builds a repo with no `origin` remote/ref at all.
    root = _project(tmp_path / "wt")
    wf = _workflow_md(tmp_path)
    _insert_run("t1", root, wf)

    assert dispatcher.check_no_new_guards("t1") is None


def test_check_no_new_guards_unknown_when_origin_ref_resolves_to_empty_stdout(tmp_path, monkeypatch):
    """⛔ CMX-250 review round 6, finding 2: ``git rev-parse --verify --quiet <ref>`` exiting
    0 with EMPTY stdout is not reachable through real git today (a missing ref exits
    nonzero) — this is belt-and-braces. But drop the ``not resolved.stdout.strip()`` half
    and ``base_sha`` becomes ``""``; the diff range collapses to ``...HEAD`` (git defaults
    the empty side to HEAD), the diff comes back empty, and a run that DID add a guard would
    report the confident ``False`` of "no test files touched" instead of ``None``
    (unknown) — exactly the misread this function exists to prevent. Force the returncode-0/
    empty-stdout rev-parse directly, the same monkeypatch shape as the diff-command-failure
    test below."""
    root = tmp_path / "wt"
    _repo_with_origin(root)
    (root / "tests" / "test_new_thing.py").write_text("def test_x():\n    assert True\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "add a guard")
    wf = _workflow_md(tmp_path)
    _insert_run("t1", root, wf)

    real_run = subprocess.run

    def fake_run(cmd, *args, **kwargs):
        if "rev-parse" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(dispatcher.subprocess, "run", fake_run)

    assert dispatcher.check_no_new_guards("t1") is None


def test_check_no_new_guards_unknown_when_diff_command_fails(tmp_path, monkeypatch):
    # `origin/<base>` resolves fine (unlike the test above) but the subsequent `git diff`
    # itself fails — a distinct unknown, and it must stay `None`, not collapse into the
    # confident `False` of "no test files touched".
    root = tmp_path / "wt"
    _repo_with_origin(root)
    (root / "README.md").write_text("hi, updated\n")
    _git(root, "commit", "-aqm", "a follow-up commit")
    wf = _workflow_md(tmp_path)
    _insert_run("t1", root, wf)

    real_run = subprocess.run

    def fake_run(cmd, *args, **kwargs):
        if "diff" in cmd:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(dispatcher.subprocess, "run", fake_run)

    assert dispatcher.check_no_new_guards("t1") is None


def test_check_no_new_guards_unknown_when_workflow_does_not_load(tmp_path):
    """⛔ CMX-250 review round 3, finding 2: the sibling of round 2's diff-command-failure
    branch — ``load_workflow`` itself can raise (missing file, unparsable WORKFLOW.md), and
    that must ALSO come back ``None`` (unknown), never the confident ``False`` of "no test
    files touched". Round 2 pinned only the diff-command branch of
    ``except Exception: return None``; this pins the ``load_workflow`` branch of the SAME
    except clause — a distinct mutation site the same test can't cover."""
    root = tmp_path / "wt"
    _repo_with_origin(root)
    wf = tmp_path / "no-such-WORKFLOW.md"      # never written — load_workflow raises
    _insert_run("t1", root, wf)

    assert dispatcher.check_no_new_guards("t1") is None


def test_check_no_new_guards_uses_the_runs_own_base_branch_not_a_hardcoded_default(tmp_path):
    """⛔ CMX-250 review round 3, finding 3: the diff must run against THIS run's own
    ``workspace.base_branch`` — read from its own workflow — not a hardcoded ``"master"``.
    Only ``origin/trunk`` exists here (no ``origin/master`` at all); a hardcoded ``"master"``
    can never resolve that ref and would report ``None`` (unknown) instead of correctly
    diffing against the run's real base and finding the new guard."""
    root = tmp_path / "wt"
    _repo_with_origin(root, branch="trunk")
    (root / "tests" / "test_new_thing.py").write_text("def test_x():\n    assert True\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "add a guard")
    wf = _workflow_md(tmp_path, base_branch="trunk")
    _insert_run("t1", root, wf)

    assert dispatcher.check_no_new_guards("t1") is True


def test_check_no_new_guards_uses_the_row_matching_its_own_task_id(tmp_path, monkeypatch):
    """⛔ CMX-250 review round 4, finding 2: the sibling of ``verify_self_check``'s own-row
    gap at the other call site. Insert a decoy run FIRST whose diff touches ``tests/``,
    then the real run "t1" whose diff is a clean docs-only change — a neutered
    ``WHERE task_id=?`` fetches the decoy's row (inserted first) and reports/logs a
    mismatch for a run that never touched a test file."""
    monkeypatch.setenv("CHELA_EVENTS_FILE", str(tmp_path / "events.jsonl"))
    wf = _workflow_md(tmp_path)

    decoy_root = tmp_path / "decoy-wt"
    _repo_with_origin(decoy_root)
    (decoy_root / "tests" / "test_new_thing.py").write_text("def test_x():\n    assert True\n")
    _git(decoy_root, "add", "-A")
    _git(decoy_root, "commit", "-qm", "decoy touches tests/")
    _insert_run("decoy", decoy_root, wf)

    root = tmp_path / "wt"
    _repo_with_origin(root)
    (root / "README.md").write_text("hi, updated\n")
    _git(root, "commit", "-aqm", "t1's own docs-only change")
    _insert_run("t1", root, wf)

    result = dispatcher.check_no_new_guards("t1")

    assert result is False
    assert not (tmp_path / "events.jsonl").exists()


def test_check_no_new_guards_diffs_from_the_merge_base_not_a_two_dot_diff(tmp_path):
    """⛔ CMX-250 review round 4, finding 3: in every test above ``origin/<base_branch>`` is
    a strict ancestor of HEAD, so ``base_sha..HEAD`` (two-dot) and ``base_sha...HEAD``
    (three-dot / merge-base) give identical results — the three-dot is unmeasured. In real
    dispatch, origin's base branch almost always advances PAST the run's own branch point.
    Here origin/master gains its own ``tests/`` file AFTER the run branched off, while the
    run's own commit only ever touches ``README.md``. A two-dot diff compares raw trees and
    would attribute origin's newer tests/ file to this run (a false positive, exactly the
    cry-wolf failure the report-only design exists to avoid); the correct merge-base diff
    must see only the run's own guard-free change."""
    root = tmp_path / "wt"
    root.mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text("hi\n")
    _git(root, "init", "-q", "-b", "master")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    base_sha = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    _git(root, "update-ref", "refs/remotes/origin/master", base_sha)

    # origin advances independently of the run, adding a guard the run never saw.
    _git(root, "checkout", "-q", "-b", "origin-advance")
    (root / "tests").mkdir()
    (root / "tests" / "test_new_thing.py").write_text("def test_x():\n    assert True\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "origin adds a guard after the run branched")
    origin_sha = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    _git(root, "update-ref", "refs/remotes/origin/master", origin_sha)
    _git(root, "checkout", "-q", "master")

    # the run's own commit, on top of the ORIGINAL base, touches only README.md.
    (root / "README.md").write_text("hi, updated by the run\n")
    _git(root, "commit", "-aqm", "run's own docs-only change")

    wf = _workflow_md(tmp_path)
    _insert_run("t1", root, wf)

    assert dispatcher.check_no_new_guards("t1") is False


def test_check_no_new_guards_unknown_for_unknown_task_id(tmp_path):
    assert dispatcher.check_no_new_guards("no-such-task") is None


def test_check_no_new_guards_unknown_when_row_has_no_worktree_path(tmp_path):
    """⛔ CMX-250 review round 5, finding 1: a row CAN exist for this task_id (unlike the
    test above) while still carrying no ``worktree_path`` — e.g. a run that hasn't been
    claimed into a worktree yet. That must ALSO come back ``None`` (unknown), never fall
    through to a ``git`` invocation against an empty path. A mutation that drops the
    ``not row["worktree_path"]`` half of the guard (leaving only ``row is None``) stays
    invisible unless a row with a real task_id and a blank worktree_path is inserted."""
    wf = _workflow_md(tmp_path)
    with dispatcher._db() as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, window_name, "
            "worktree_path, branch_name, started_at, attempt, task_number) "
            "VALUES ('t1', ?, 'do a thing', 'running', 'cmx-1', NULL, 'cmx-1', "
            "'2026-08-12T10:00:00+00:00', 1, 1)",
            (str(wf),),
        )
        conn.commit()

    assert dispatcher.check_no_new_guards("t1") is None


def test_check_no_new_guards_unknown_when_row_has_no_workflow_path(tmp_path):
    """⛔ CMX-250 review round 5, finding 1 (sibling): the same guard's other half —
    ``not row["workflow_path"]`` — must ALSO stop the lookup before ``load_workflow`` is
    ever called with ``None``."""
    root = tmp_path / "wt"
    _repo_with_origin(root)
    with dispatcher._db() as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, window_name, "
            "worktree_path, branch_name, started_at, attempt, task_number) "
            "VALUES ('t1', '', 'do a thing', 'running', 'cmx-1', ?, 'cmx-1', "
            "'2026-08-12T10:00:00+00:00', 1, 1)",
            (str(root),),
        )
        conn.commit()

    assert dispatcher.check_no_new_guards("t1") is None


def test_cmd_task_finished_no_new_guards_warns_when_diff_touches_tests(capsys):
    from chela import main

    with patch.object(dispatcher, "check_no_new_guards", return_value=True) as check, \
         patch.object(dispatcher, "mark_awaiting_review",
                       return_value={"ok": True, "task_id": "t1", "pr_url": "https://x/1"}):
        with patch.object(sys, "argv", ["chela", "task-finished", "t1", "--no-new-guards"]):
            main.main()      # ⛔ report-only: warns but still proceeds
    check.assert_called_once_with("t1")
    out = capsys.readouterr().out
    assert "diff" in out and "tests/" in out
    assert "awaiting review" in out


def test_cmd_task_finished_no_new_guards_silent_when_diff_is_clean(capsys):
    from chela import main

    with patch.object(dispatcher, "check_no_new_guards", return_value=False), \
         patch.object(dispatcher, "mark_awaiting_review",
                       return_value={"ok": True, "task_id": "t1", "pr_url": "https://x/1"}):
        with patch.object(sys, "argv", ["chela", "task-finished", "t1", "--no-new-guards"]):
            main.main()
    out = capsys.readouterr().out
    assert "touches tests/" not in out
    assert "awaiting review" in out


def test_cmd_task_finished_no_new_guards_silent_when_diff_is_unknown(capsys):
    """⛔ CMX-250 review round 6, finding 1: the CLI consumer's half of the None-vs-False
    invariant. ``dispatcher.check_no_new_guards`` returning ``None`` means "cannot tell" —
    it must NOT be read as "the diff touches tests/". A mutation from ``if touched_tests:``
    to ``if touched_tests is not False:`` makes ``None`` trip the same branch as ``True``,
    falsely telling the agent its diff touches tests/ when the check could not verify that
    at all."""
    from chela import main

    with patch.object(dispatcher, "check_no_new_guards", return_value=None) as check, \
         patch.object(dispatcher, "mark_awaiting_review",
                       return_value={"ok": True, "task_id": "t1", "pr_url": "https://x/1"}):
        with patch.object(sys, "argv", ["chela", "task-finished", "t1", "--no-new-guards"]):
            main.main()
    check.assert_called_once_with("t1")
    out = capsys.readouterr().out
    assert "touches tests/" not in out
    assert "awaiting review" in out


def test_cmd_task_finished_no_new_guards_forwards_its_own_task_id_not_a_hardcoded_one(capsys):
    """⛔ CMX-250 round 8, finding 2 (closed up front on re-scope, quoted verbatim): the
    ``--no-new-guards`` cross-check must read THE RUN IT WAS INVOKED FOR — the same
    task_id-forwarding gap as finding 1, at the sibling call site.

        -         touched_tests = dispatcher.check_no_new_guards(args.task_id)
        +         touched_tests = dispatcher.check_no_new_guards("t1")

    Same shape as the sibling test above: every other ``--no-new-guards`` test uses the
    literal ``"t1"``, so a task_id that is NOT ``"t1"`` is required to catch a hardcoded
    forward."""
    from chela import main

    with patch.object(dispatcher, "check_no_new_guards", return_value=False) as check, \
         patch.object(dispatcher, "mark_awaiting_review",
                       return_value={"ok": True, "task_id": "cmx-777",
                                     "pr_url": "https://x/1"}):
        with patch.object(sys, "argv", ["chela", "task-finished", "cmx-777", "--no-new-guards"]):
            main.main()
    check.assert_called_once_with("cmx-777")


# --- CMX-258 rework round 3 (+ round 11's completeness fix) -----------------------------
#
# Every finding on this PR since round 8 has been ONE of two invariants, closed one call
# site per round: (a) a task_id forwarded into a lookup, hardcoded and going unnoticed
# because every test happened to use the same literal id everywhere; (b) a refusal whose
# message drops the one fact that made it actionable. `chela/main.py`'s `cmd_task_finished`
# has SEVEN `task-finished:` / self-check print sites (the both-flags rejection, the
# self-check-could-not-run refusal, the guards-SURVIVED refusal, the CANNOT-VERIFY refusal,
# the --no-new-guards tests/-touched notice, the neither-flag notice, and the
# mark_awaiting_review failure) — this table has one row per site.
#
# ⛔ Round 10 hand-counted "six" and missed the neither-flag notice — the count itself was
# the defect, the same class of miss as round 6's nine `.mjs` rows that never varied the
# deciding axis. `test_task_finished_scenario_table_covers_every_print_exit_site` below
# derives the site count from `cmd_task_finished`'s own AST instead of trusting a human
# recount, so an 8th site added later fails that test instead of silently escaping this
# table the way the 7th did.
#
# Two rows double as the mandatory negative controls: "both_flags_at_once" is a refusal
# that legitimately has no task_id to report (its message is coherent without one, and
# `must_not_contain` proves nothing was fabricated to fill that gap — "always interpolate
# an id" cannot pass this row), and "no_new_guards_mismatch_notice" is a forwarded id that
# IS correct still reaching a successful transition ("reject everything" cannot pass this
# row, since it must exit 0/fall through, not raise).

_TASK_FINISHED_SCENARIOS = [
    dict(
        name="both_flags_at_once",
        task_id="cmx-901",
        argv_extra=["--self-check-experiments", "e.json", "--no-new-guards"],
        setup=lambda: {},
        expect_exit=2,
        must_contain=["at most one of"],
        must_not_contain=["cmx-901"],
    ),
    dict(
        name="self_check_could_not_run",
        task_id="cmx-902",
        argv_extra=["--self-check-experiments", "e.json"],
        setup=lambda: {
            "dispatcher.verify_self_check": dict(
                return_value={"ok": False, "error": "widget.json does not exist"}),
        },
        expect_exit=1,
        must_contain=["self-check could not run", "widget.json does not exist"],
        forward=("dispatcher.verify_self_check", ("cmx-902", "e.json")),
    ),
    dict(
        name="guards_survived",
        task_id="cmx-903",
        argv_extra=["--self-check-experiments", "e.json"],
        setup=lambda: {
            "dispatcher.verify_self_check": dict(return_value={
                "ok": True, "blocking": 1, "cannot_verify": "",
                "outcomes": [{"verdict": "SURVIVED", "file": "g.py", "guard": "the cue"}],
            }),
        },
        expect_exit=1,
        must_contain=["DECORATION", "[SURVIVED] g.py: the cue"],
        forward=("dispatcher.verify_self_check", ("cmx-903", "e.json")),
    ),
    dict(
        name="cannot_verify",
        task_id="cmx-904",
        argv_extra=["--self-check-experiments", "e.json"],
        setup=lambda: {
            "dispatcher.verify_self_check": dict(return_value={
                "ok": True, "blocking": 0, "cannot_verify": "the suite is NOT GREEN",
                "outcomes": [],
            }),
        },
        expect_exit=1,
        must_contain=["CANNOT VERIFY", "the suite is NOT GREEN"],
        forward=("dispatcher.verify_self_check", ("cmx-904", "e.json")),
    ),
    dict(
        name="no_new_guards_mismatch_notice",
        task_id="cmx-905",
        argv_extra=["--no-new-guards"],
        setup=lambda: {
            "dispatcher.check_no_new_guards": dict(return_value=True),
            "dispatcher.mark_awaiting_review": dict(return_value={
                "ok": True, "task_id": "cmx-905", "pr_url": "https://x/1"}),
        },
        expect_exit=None,
        # ⛔ CMX-258 rework round 10 (judge finding 2): "touches tests/" and "skipping
        # self-check" pin only the bare fact — a mutation that drops WHY it matters (Done
        # Criteria #3 requires a self-check for this diff) and WHAT happened to that fact
        # (recorded, not blocked — the entire deterrent, since this path is report-only)
        # survived. Pin both actionable halves, not just the trigger condition.
        must_contain=["touches tests/", "skipping self-check", "Done Criteria #3",
                      "Not blocked, but recorded"],
        forward=("dispatcher.check_no_new_guards", ("cmx-905",)),
    ),
    dict(
        name="neither_flag_notice",
        task_id="cmx-907",
        argv_extra=[],
        setup=lambda: {
            "dispatcher.mark_awaiting_review": dict(return_value={
                "ok": True, "task_id": "cmx-907", "pr_url": "https://x/1"}),
        },
        expect_exit=None,
        # ⛔ CMX-258 rework round 11 (judge finding 1): this is the SEVENTH
        # `task-finished` print site — the one an agent dispatched under an older
        # WORKFLOW.md actually hits, and the only channel that tells it which flag to
        # pass. Pin both the trigger condition and the actionable half (the two flag
        # names it should pass next time), not just the bare "not enforced" fact.
        must_contain=["neither --self-check-experiments nor --no-new-guards",
                      "Done Criteria #3 was not enforced",
                      "--self-check-experiments <path>",
                      "--no-new-guards if this PR truly adds no guards"],
    ),
    dict(
        name="mark_awaiting_review_fails",
        task_id="cmx-906",
        argv_extra=[],
        setup=lambda: {
            "dispatcher.mark_awaiting_review": dict(return_value={
                "ok": False, "error": "run is in status 'done', refusing to transition"}),
        },
        expect_exit=1,
        must_contain=["task-finished:", "run is in status 'done', refusing to transition"],
        forward=("dispatcher.mark_awaiting_review", ("cmx-906",)),
    ),
]


@pytest.mark.parametrize("scenario", _TASK_FINISHED_SCENARIOS, ids=lambda s: s["name"])
def test_cmd_task_finished_every_refusal_names_its_actionable_fact_and_forwards_its_task_id(
    scenario, capsys,
):
    """⛔ CMX-258 rework round 3: one row per ``task-finished:``/self-check print site in
    ``cmd_task_finished``. Each row asserts BOTH halves the judge kept finding one at a
    time: the id reaching whichever dispatcher call produced this scenario is
    ``args.task_id`` itself, never a hardcoded literal (``forward``, using a task_id that is
    not ``"t1"`` like every sibling forwarding test in this file), and the printed message
    names the fact that makes it actionable (``must_contain`` / ``must_not_contain``)."""
    from chela import main

    mocks = {}
    with ExitStack() as stack:
        for target, kwargs in scenario["setup"]().items():
            _module, attr = target.split(".")
            mock = stack.enter_context(patch.object(dispatcher, attr, **kwargs))
            mocks[target] = mock

        argv = ["chela", "task-finished", scenario["task_id"], *scenario["argv_extra"]]
        with patch.object(sys, "argv", argv):
            if scenario["expect_exit"] is None:
                main.main()      # falls through — no sys.exit on this scenario's path
            else:
                with pytest.raises(SystemExit) as exc:
                    main.main()
                assert exc.value.code == scenario["expect_exit"]

    out = capsys.readouterr().out
    for substring in scenario["must_contain"]:
        assert substring in out, f"{scenario['name']}: expected {substring!r} in {out!r}"
    for substring in scenario.get("must_not_contain", []):
        assert substring not in out, f"{scenario['name']}: unexpected {substring!r} in {out!r}"

    if scenario.get("forward"):
        target, expected_args = scenario["forward"]
        mocks[target].assert_called_once_with(*expected_args)


def _is_exit_call(node: ast.AST) -> bool:
    """``sys.exit(...)`` or a bare ``exit(...)`` — either spelling ends the branch."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return (isinstance(func, ast.Attribute) and func.attr == "exit") or (
        isinstance(func, ast.Name) and func.id == "exit"
    )


def _branch_has_exit(stmts: list[ast.stmt]) -> bool:
    return any(_is_exit_call(n) for stmt in stmts for n in ast.walk(stmt))


def _count_task_finished_sites() -> int:
    """⛔ CMX-258 rework round 11 (judge finding 1): derive the number of distinguishable
    ``cmd_task_finished`` print/exit sites from the function's own AST, instead of trusting
    a hand count — a hand count has now been wrong twice on this run (round 10 said "six"
    when there were seven; round 6's nine ``.mjs`` table rows never varied the deciding
    axis).

    A "site" is either of the two shapes every row in ``_TASK_FINISHED_SCENARIOS`` actually
    has:

    (a) a ``sys.exit(...)`` call. Each one IS a distinguishable refusal — the both-flags
        rejection, the self-check-could-not-run/guards-SURVIVED/CANNOT-VERIFY refusals, and
        the mark_awaiting_review failure all end in one.
    (b) a branch of a top-level ``if``/``elif``/``else`` chain that contains NO
        ``sys.exit`` anywhere inside it. Such a branch never earns its own exit-based site,
        so its only content — the notice itself — IS the site (the ``--no-new-guards``
        tests/-touched notice and the neither-flag notice: both report-only, neither ever
        exits).

    This falls out to the right answer on the branches that must NOT count: the
    per-outcome loop print sits inside a branch that also contains three ``sys.exit``
    calls (already counted via (a), so the branch itself is skipped by (b) to avoid double
    counting), and the unconditional confirmation prints (self-check success, the
    ``--no-new-guards`` skip line, the final "awaiting review" line) are continuations of a
    branch already accounted for by (a) or (b), not branches of their own.
    """
    from chela import main

    source = inspect.getsource(main.cmd_task_finished)
    tree = ast.parse(source)
    func = tree.body[0]
    assert isinstance(func, ast.FunctionDef)

    exit_count = sum(1 for n in ast.walk(func) if _is_exit_call(n))

    non_exit_branches = 0
    for stmt in func.body:
        if not isinstance(stmt, ast.If):
            continue
        node = stmt
        while True:
            if not _branch_has_exit(node.body):
                non_exit_branches += 1
            if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
                node = node.orelse[0]
                continue
            if node.orelse and not _branch_has_exit(node.orelse):
                non_exit_branches += 1
            break

    return exit_count + non_exit_branches


def test_task_finished_scenario_table_covers_every_print_exit_site_in_the_function():
    """⛔ CMX-258 rework round 11 (judge finding 1, ⭐⭐): make the table's completeness
    CHECKABLE rather than trusted. ``_count_task_finished_sites`` walks
    ``cmd_task_finished``'s own AST (see its docstring for exactly what counts as a site);
    this test asserts ``_TASK_FINISHED_SCENARIOS`` has one row per site it finds. An 8th
    site added to ``cmd_task_finished`` later — another ``sys.exit``, or another
    non-exiting ``elif``/``else`` branch — then fails THIS test instead of silently
    escaping the table the way the 7th (the neither-flag notice) escaped round 10's hand
    count.
    """
    found = _count_task_finished_sites()
    assert len(_TASK_FINISHED_SCENARIOS) == found, (
        f"cmd_task_finished has {found} print/exit site(s) by AST walk, but "
        f"_TASK_FINISHED_SCENARIOS has {len(_TASK_FINISHED_SCENARIOS)} row(s) — "
        "add (or remove) a row so the table matches the code, not a hand count."
    )
