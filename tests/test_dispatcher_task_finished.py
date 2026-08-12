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

import json
import subprocess
import sys
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


def _repo_with_origin(root: Path, branch: str = "master") -> str:
    """A git repo whose HEAD is also ``refs/remotes/origin/<branch>`` — no real remote
    needed, just a ref :func:`dispatcher.check_no_new_guards` can resolve ``origin/<base
    branch>`` against. Returns the base sha; the caller commits ON TOP of it to build a
    diff."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text("hi\n")
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
    result = dispatcher.verify_self_check("no-such-task", str(tmp_path / "e.json"))

    assert not result["ok"]
    assert "no run found" in result["error"]


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
    assert "CANNOT VERIFY" in capsys.readouterr().out
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
    these flags exist and must not be broken by a gate it was never told to satisfy."""
    from chela import main

    with patch.object(dispatcher, "verify_self_check") as verify, \
         patch.object(dispatcher, "mark_awaiting_review",
                       return_value={"ok": True, "task_id": "t1", "pr_url": "https://x/1"}):
        with patch.object(sys, "argv", ["chela", "task-finished", "t1"]):
            main.main()
    verify.assert_not_called()
    out = capsys.readouterr().out
    assert "was not enforced" in out
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


# --- dispatcher.check_no_new_guards -----------------------------------------------------
#
# ⚖️🔎 CMX-250 review round 1, finding 2: `--no-new-guards` was a bare self-declaration
# nothing cross-checked. This is report-only — it must never refuse the transition, only
# make a wrong opt-out visible.


def test_check_no_new_guards_true_and_logs_an_event_when_diff_touches_tests(tmp_path, monkeypatch):
    monkeypatch.setenv("CHELA_EVENTS_FILE", str(tmp_path / "events.jsonl"))
    root = tmp_path / "wt"
    _repo_with_origin(root)
    (root / "tests").mkdir()
    (root / "tests" / "test_new_thing.py").write_text("def test_x():\n    assert True\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "add a guard")
    wf = _workflow_md(tmp_path)
    _insert_run("t1", root, wf)

    result = dispatcher.check_no_new_guards("t1")

    assert result is True
    events = event_log.read()["events"]
    matches = [e for e in events if e.get("type") == "no_new_guards_mismatch"]
    assert len(matches) == 1
    assert matches[0]["payload"]["task_id"] == "t1"
    assert matches[0]["payload"]["files"] == ["tests/test_new_thing.py"]
    # ⛔ CMX-250 review round 5, finding 4: the payload alone isn't what a human reads on
    # the dashboard — the event's human-readable summary must say WHAT happened, not be
    # blanked to "". Pin the actual words, not just that the field is non-empty.
    assert "no-new-guards was passed" in matches[0]["summary"]
    assert "touches tests/" in matches[0]["summary"]
    assert "1 file(s)" in matches[0]["summary"]


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
    (root / "tests").mkdir()
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
    (root / "tests").mkdir()
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
    (decoy_root / "tests").mkdir()
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
