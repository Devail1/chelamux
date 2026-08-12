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


def _repo_with_origin(root: Path) -> str:
    """A git repo whose HEAD is also ``refs/remotes/origin/master`` — no real remote
    needed, just a ref :func:`dispatcher.check_no_new_guards` can resolve ``origin/<base
    branch>`` against. Returns the base sha; the caller commits ON TOP of it to build a
    diff."""
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
    return base_sha


def _exp(**over) -> dict:
    exp = {"guard": "the glyph cue", "kind": "mutation", "file": "guard.py",
           "before": GLYPH_BEFORE, "after": GLYPH_AFTER}
    exp.update(over)
    return exp


def _workflow_md(tmp_path: Path) -> Path:
    p = tmp_path / "WORKFLOW.md"
    p.write_text(
        "---\nproject_key: TEST\njudge:\n  test_cmd: " + json.dumps(TEST_CMD) +
        "\n  suite_timeout_seconds: 120\n---\nbody\n"
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
    assert "DECORATION" in capsys.readouterr().out
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
    assert "DECORATION" in capsys.readouterr().out
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
    assert "self-check could not run" in capsys.readouterr().out
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


def test_check_no_new_guards_unknown_for_unknown_task_id(tmp_path):
    assert dispatcher.check_no_new_guards("no-such-task") is None


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
