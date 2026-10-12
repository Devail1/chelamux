"""⚖️🧹 CMX-88 — ``chela judge sweep`` and the ``task-finished`` gate built on it.

Every test runs against a throwaway git repo (``origin/dev`` is a local ref, no remote) and a
temp ``CHELA_DIR``/runs DB — never a live tmux or ``~/.chela``.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from chela import dispatcher, sweep

TEST_CMD = f'"{sys.executable}" -m pytest -q -p no:cacheprovider tests'

BASE_FEAT = '''\
def old(x):
    if x > 5:
        return "big"
    return "small"
'''

BRANCH_FEAT = BASE_FEAT + '''

def new(x):
    if x == 3:
        return "three"
    return "other"
'''
NEW_IF_LINE = 8          # `    if x == 3:` in BRANCH_FEAT
OLD_IF_LINE = 2          # `    if x > 5:` — untouched by the branch, and no test covers it

COVERING_TEST = '''\
from feat import new


def test_new_three():
    assert new(3) == "three"


def test_new_other():
    assert new(4) == "other"
'''

UNRELATED_TEST = '''\
def test_nothing():
    assert True
'''


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *args],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def _repo(root: Path, test_body: str, *, commit_branch: bool = True) -> Path:
    """A repo whose ``origin/dev`` holds ``BASE_FEAT``; the branch adds ``new()`` on top."""
    root.mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "feat.py").write_text(BASE_FEAT)
    (root / "tests" / "test_feat.py").write_text(UNRELATED_TEST)
    _git(root, "init", "-q", "-b", "dev")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    _git(root, "update-ref", "refs/remotes/origin/dev", _git(root, "rev-parse", "HEAD"))
    _git(root, "checkout", "-qb", "feature")
    (root / "feat.py").write_text(BRANCH_FEAT)
    (root / "tests" / "test_feat.py").write_text(test_body)
    if commit_branch:
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", "feature")
    return root


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CHELA_DIR", str(tmp_path / "chela-dir"))
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")


def _sweep(root: Path, test_cmd: str = TEST_CMD) -> sweep.SweepReport:
    return sweep.sweep(root, "origin/dev", test_cmd, timeout=120)


# --- the sweep ------------------------------------------------------------------------------


def test_a_changed_if_with_no_test_survives(tmp_path):
    root = _repo(tmp_path / "r", UNRELATED_TEST)
    report = _sweep(root)
    assert report.cannot_verify == ""
    keys = {r.mutation.key for r in report.survivors}
    assert keys == {f"feat.py:{NEW_IF_LINE}"}
    kinds = {r.mutation.kind for r in report.survivors}
    assert {"if-false", "compare-flip"} <= kinds


def test_a_changed_if_with_a_covering_test_is_killed(tmp_path):
    root = _repo(tmp_path / "r", COVERING_TEST)
    report = _sweep(root)
    assert report.cannot_verify == ""
    assert report.survivors == []
    assert report.as_dict()["killed"] == len(report.results) >= 2


def test_only_changed_lines_are_mutated(tmp_path):
    """`old()`'s `if x > 5:` is untested — mutated, it would SURVIVE. It is not on the
    branch's diff, so the sweep must not touch it."""
    root = _repo(tmp_path / "r", UNRELATED_TEST)
    report = _sweep(root)
    lines = {r.mutation.line for r in report.results}
    assert OLD_IF_LINE not in lines
    assert lines == {NEW_IF_LINE}


def test_uncommitted_and_untracked_changes_are_swept_too(tmp_path):
    root = _repo(tmp_path / "r", UNRELATED_TEST, commit_branch=False)
    (root / "extra.py").write_text("def f(a):\n    if a:\n        return 1\n    return 2\n")
    report = _sweep(root)
    keys = {r.mutation.key for r in report.survivors}
    assert f"feat.py:{NEW_IF_LINE}" in keys
    assert "extra.py:2" in keys


def test_the_file_is_byte_identical_after_the_sweep(tmp_path):
    root = _repo(tmp_path / "r", UNRELATED_TEST)
    before = (root / "feat.py").read_bytes()
    report = _sweep(root)
    assert report.results
    assert (root / "feat.py").read_bytes() == before
    assert not sweep.marker_path(root).exists()
    assert _git(root, "status", "--porcelain") == ""


def test_an_unparseable_mutation_is_skipped_and_never_counted_as_killed(tmp_path, monkeypatch):
    root = _repo(tmp_path / "r", COVERING_TEST)
    before = (root / "feat.py").read_bytes()
    line = BRANCH_FEAT.split("\n")[NEW_IF_LINE - 1]
    broken = sweep.Mutation("feat.py", NEW_IF_LINE, "if-false", line, "    if False and (x == 3:")
    monkeypatch.setattr(sweep, "collect_mutations", lambda wt, base: ([broken], ""))
    report = _sweep(root)
    d = report.as_dict()
    assert [r["status"] for r in d["unparseable"]] == [sweep.UNPARSEABLE]
    assert d["killed"] == 0
    assert d["survivors"] == []
    assert (root / "feat.py").read_bytes() == before


def _cli(root: Path, test_cmd: str, chela_dir: Path, **kw) -> subprocess.Popen:
    env = {**os.environ, "CHELA_DIR": str(chela_dir)}
    return subprocess.Popen(
        [sys.executable, "-c", "from chela.main import main; main()", "judge", "sweep",
         "--cwd", str(root), "--base", "origin/dev", "--test-cmd", test_cmd],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, **kw,
    )


def test_cli_exits_1_on_a_survivor_and_0_when_covered(tmp_path):
    bad = _repo(tmp_path / "bad", UNRELATED_TEST)
    proc = _cli(bad, TEST_CMD, tmp_path / "cd")
    out, _ = proc.communicate(timeout=300)
    assert proc.returncode == 1, out
    assert f"[SURVIVED] feat.py:{NEW_IF_LINE}" in out

    good = _repo(tmp_path / "good", COVERING_TEST)
    proc = _cli(good, TEST_CMD, tmp_path / "cd")
    out, _ = proc.communicate(timeout=300)
    assert proc.returncode == 0, out
    assert "0 survived" in out


# A suite that is instant on the clean tree and hangs while a mutation is on disk — so the
# test can kill the sweep at exactly the moment a mutation is live.
SLOW_SUITE = '''\
import pathlib, sys, time
if "False and" in pathlib.Path("feat.py").read_text():
    pathlib.Path(sys.argv[1]).write_text("mutated")
    time.sleep(120)
'''


def _start_and_wait_for_mutation(tmp_path: Path, root: Path) -> tuple[subprocess.Popen, Path]:
    flag = tmp_path / "flag"
    script = tmp_path / "slow_suite.py"
    script.write_text(SLOW_SUITE)
    proc = _cli(root, f'"{sys.executable}" "{script}" "{flag}"', tmp_path / "cd",
                start_new_session=True)
    deadline = time.monotonic() + 60
    while not flag.exists():
        assert proc.poll() is None, proc.communicate()[0]
        assert time.monotonic() < deadline, "the sweep never reached a mutation"
        time.sleep(0.1)
    return proc, flag


def test_a_killed_sweep_is_recovered_on_the_next_start(tmp_path):
    root = _repo(tmp_path / "r", COVERING_TEST)
    original = (root / "feat.py").read_bytes()
    proc, _ = _start_and_wait_for_mutation(tmp_path, root)
    os.killpg(proc.pid, signal.SIGKILL)
    proc.wait(timeout=30)
    # The kill really did land mid-mutation: the file is corrupted and the marker says so.
    assert (root / "feat.py").read_bytes() != original
    assert sweep.marker_path(root).exists()

    report = _sweep(root)
    assert "restored feat.py" in report.recovered
    assert (root / "feat.py").read_bytes() == original
    assert not sweep.marker_path(root).exists()


def test_sigterm_restores_the_file_before_the_sweep_exits(tmp_path):
    root = _repo(tmp_path / "r", COVERING_TEST)
    original = (root / "feat.py").read_bytes()
    proc, _ = _start_and_wait_for_mutation(tmp_path, root)
    proc.send_signal(signal.SIGTERM)
    proc.wait(timeout=60)
    try:
        os.killpg(proc.pid, signal.SIGKILL)     # reap the hung suite if it outlived us
    except ProcessLookupError:
        pass
    assert (root / "feat.py").read_bytes() == original
    assert not sweep.marker_path(root).exists()


def test_recovery_refuses_a_file_edited_since_the_kill(tmp_path):
    root = _repo(tmp_path / "r", COVERING_TEST)
    sweep._write_marker(root, "feat.py", "original\n", "mutated\n")
    (root / "feat.py").write_text("someone else's edit\n")
    with patch.object(sweep, "_pid_alive", return_value=False):
        done, err = sweep.recover(root)
    assert done == ""
    assert "edited since" in err
    assert (root / "feat.py").read_text() == "someone else's edit\n"
    assert sweep.marker_path(root).exists()


# --- mutation generation --------------------------------------------------------------------


def test_py_true_twin_only_for_an_if_with_an_else():
    src = "def f(a, b):\n    if a:\n        return 1\n    if b:\n        x = 1\n    else:\n        x = 2\n    return x\n"
    muts = sweep.py_mutations("m.py", src, {2, 4})
    kinds = {(m.line, m.kind) for m in muts}
    assert (2, "if-false") in kinds and (2, "if-true") not in kinds
    assert (4, "if-false") in kinds and (4, "if-true") in kinds


def test_py_return_code_completed_process_and_return_none_mutations():
    src = (
        "import subprocess\n"
        "def f(a):\n"
        "    if a:\n"
        "        return None\n"
        "    return a + 1\n"
        "def g():\n"
        "    return subprocess.CompletedProcess([], 0)\n"
        "def h():\n"
        "    return 1\n"
    )
    muts = {(m.line, m.kind): m.after.strip() for m in sweep.py_mutations("m.py", src, {4, 5, 7, 9})}
    assert muts[(4, "return-value")] == "return a + 1"
    assert muts[(5, "return-none")] == "return None"
    assert muts[(7, "returncode")] == "return subprocess.CompletedProcess([], 1)"
    assert muts[(9, "return-code")] == "return 0"


def test_js_mutations_skip_strings_and_comments():
    src = 'function f(a) {\n  if (a === 1) {\n    return 0;\n  }\n  const s = "a < b"; // c > d\n}\n'
    muts = sweep.js_mutations("m.js", src, {2, 3, 5})
    afters = {(m.line, m.kind): m.after.strip() for m in muts}
    assert afters[(2, "if-false")] == "if (false && (a === 1)) {"
    assert afters[(2, "compare-flip")] == "if (a !== 1) {"
    assert afters[(3, "return-code")] == "return 1;"
    assert not [m for m in muts if m.line == 5]
    assert (2, "if-true") not in afters          # this `if` has no else to fall into


def test_js_true_twin_only_for_an_if_with_an_else():
    src = ("function f(a) {\n  if (a) {\n    g();\n  } else {\n    h();\n  }\n"
           "  if (b) { g(); } else { h(); }\n  if (c) {\n    g();\n  }\n}\n")
    kinds = {(m.line, m.kind) for m in sweep.js_mutations("m.js", src, {2, 7, 8})}
    assert (2, "if-true") in kinds
    assert (7, "if-true") in kinds
    assert (8, "if-true") not in kinds
    assert {(2, "if-false"), (7, "if-false"), (8, "if-false")} <= kinds


def test_test_files_are_never_mutated(tmp_path):
    root = _repo(tmp_path / "r", COVERING_TEST)
    changed, err = sweep.changed_lines(root, "origin/dev")
    assert err == ""
    assert set(changed) == {"feat.py"}


# --- task-finished --------------------------------------------------------------------------


def _run_row(tmp_path: Path, root: Path, task_id: str = "cmx-88") -> None:
    wf = tmp_path / "WORKFLOW.md"
    wf.write_text("---\nproject_key: TEST\nworkspace:\n  base_branch: dev\njudge:\n"
                  f"  test_cmd: {json.dumps(TEST_CMD)}\n  suite_timeout_seconds: 120\n---\nbody\n")
    with dispatcher._db() as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, window_name, "
            "worktree_path, branch_name, started_at, attempt, task_number) "
            "VALUES (?, ?, 'x', 'running', 'cmx-88', ?, 'feature', "
            "'2026-10-12T10:00:00+00:00', 1, 88)",
            (task_id, str(wf), str(root)),
        )
        conn.commit()


def _task_finished(*extra: str) -> int | None:
    from chela import main

    with patch.object(sys, "argv", ["chela", "task-finished", "cmx-88", "--no-new-guards",
                                     *extra]):
        try:
            main.main()
        except SystemExit as e:
            return e.code
    return None


def test_task_finished_refuses_on_a_survivor(tmp_path, capsys):
    root = _repo(tmp_path / "r", UNRELATED_TEST)
    _run_row(tmp_path, root)
    with patch.object(dispatcher, "check_no_new_guards", return_value=False), \
         patch.object(dispatcher, "request_task_finished") as req:
        code = _task_finished()
    assert code == 1
    out = capsys.readouterr().out
    assert f"[SURVIVED] feat.py:{NEW_IF_LINE}" in out
    req.assert_not_called()


def test_task_finished_accepts_an_equivalent_note_and_records_it_on_the_run(tmp_path, capsys):
    root = _repo(tmp_path / "r", UNRELATED_TEST)
    _run_row(tmp_path, root)
    why = "new() is a demo helper nothing calls"
    with patch.object(dispatcher, "check_no_new_guards", return_value=False):
        code = _task_finished("--sweep-equivalent", f"feat.py:{NEW_IF_LINE}: {why}")
    assert code is None, capsys.readouterr().out
    # The marker carries it; the daemon writes it onto the run row.
    carried = dispatcher._read_task_finished_sweep_equivalents(root)
    assert [e["key"] for e in carried] == [f"feat.py:{NEW_IF_LINE}"]
    assert carried[0]["why"] == why
    assert any("False and" in m for m in carried[0]["mutations"])
    with patch.object(dispatcher, "_read_pr_url", return_value=None), \
         patch.object(dispatcher, "_read_pr_status", return_value=(None, None)), \
         patch.object(dispatcher, "_kill_window"):
        assert dispatcher.mark_awaiting_review("cmx-88", sweep_equivalents=carried)["ok"]
    row = dispatcher.resolve_run("cmx-88")
    assert json.loads(row["sweep_equivalents"])[0]["why"] == why
    # …and the judge is shown it.
    with dispatcher._db() as conn:
        db_row = conn.execute("SELECT * FROM runs WHERE task_id='cmx-88'").fetchone()
    assert why in dispatcher._judge_sweep_equivalents(db_row)


def test_task_finished_refuses_an_equivalent_note_for_a_line_that_did_not_survive(tmp_path, capsys):
    root = _repo(tmp_path / "r", UNRELATED_TEST)
    _run_row(tmp_path, root)
    with patch.object(dispatcher, "check_no_new_guards", return_value=False), \
         patch.object(dispatcher, "request_task_finished") as req:
        code = _task_finished(
            "--sweep-equivalent", f"feat.py:{NEW_IF_LINE}: demo helper",
            "--sweep-equivalent", f"feat.py:{OLD_IF_LINE}: not a survivor at all",
        )
    assert code == 1
    out = capsys.readouterr().out
    assert f"[NOT A SURVIVOR] feat.py:{OLD_IF_LINE}" in out
    req.assert_not_called()


def test_task_finished_refuses_an_equivalent_note_with_no_reason(tmp_path, capsys):
    with patch.object(dispatcher, "verify_sweep") as verify, \
         patch.object(dispatcher, "check_no_new_guards", return_value=False), \
         patch.object(dispatcher, "request_task_finished") as req:
        code = _task_finished("--sweep-equivalent", f"feat.py:{NEW_IF_LINE}:   ")
    assert code == 2
    assert "gives no reason" in capsys.readouterr().out
    verify.assert_not_called()
    req.assert_not_called()


def test_task_finished_proceeds_with_no_note_when_every_mutation_is_killed(tmp_path, capsys):
    root = _repo(tmp_path / "r", COVERING_TEST)
    _run_row(tmp_path, root)
    with patch.object(dispatcher, "check_no_new_guards", return_value=False), \
         patch.object(dispatcher, "request_task_finished",
                      return_value={"ok": True, "task_id": "cmx-88"}) as req:
        code = _task_finished()
    assert code is None, capsys.readouterr().out
    req.assert_called_once_with("cmx-88", handoff=None)


def test_mark_awaiting_review_appends_the_equivalents_to_the_pr_body(tmp_path):
    root = _repo(tmp_path / "r", UNRELATED_TEST)
    _run_row(tmp_path, root)
    notes = [{"key": "feat.py:8", "why": "demo helper", "mutations": ["if-false: a → b"]}]
    calls = []

    def fake_run(argv, **kw):
        calls.append((argv, kw.get("input")))
        out = "the agent's body" if argv[:3] == ["gh", "pr", "view"] else ""
        return subprocess.CompletedProcess(argv, 0, out, "")

    with patch.object(dispatcher, "_read_pr_url", return_value="https://github.com/o/r/pull/7"), \
         patch.object(dispatcher, "_read_pr_status", return_value=(None, None)), \
         patch.object(dispatcher, "_kill_window"), \
         patch.object(dispatcher.subprocess, "run", fake_run):
        assert dispatcher.mark_awaiting_review("cmx-88", sweep_equivalents=notes)["ok"]
    edits = [inp for argv, inp in calls if argv[:3] == ["gh", "pr", "edit"]]
    assert len(edits) == 1
    assert edits[0].startswith("the agent's body")
    assert "`feat.py:8` — demo helper" in edits[0]


def test_workflow_md_tells_the_agent_to_sweep_and_add_a_red_test_per_survivor():
    text = " ".join((Path(__file__).resolve().parent.parent / "WORKFLOW.md").read_text().split())
    assert "`chela judge sweep`" in text
    assert "add a test that goes RED under that exact mutation" in text
    assert "--sweep-equivalent '<file:line>: <why no outcome can change>'" in text


def test_the_judge_prompt_shows_the_runs_sweep_equivalents(tmp_path):
    from chela.workflow import load_workflow

    root = _repo(tmp_path / "r", UNRELATED_TEST)
    _run_row(tmp_path, root)
    with dispatcher._db() as conn:
        conn.execute("UPDATE runs SET sweep_equivalents=? WHERE task_id='cmx-88'",
                     (json.dumps([{"key": "feat.py:8", "why": "demo helper"}]),))
        conn.commit()
        row = conn.execute("SELECT * FROM runs WHERE task_id='cmx-88'").fetchone()
    wf = load_workflow(tmp_path / "WORKFLOW.md")
    shown = dispatcher._judge_vars(wf, row, root, "deadbeef")["sweep_equivalents"]
    assert "`feat.py:8` — demo helper" in shown
    assert "{{sweep_equivalents}}" in dispatcher.JUDGE_PROMPT
