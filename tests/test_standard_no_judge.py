"""⚖️🪜 CMX-296 — a STANDARD-tier PR (product/UI, no core path) gets no judge round.

* the dispatcher stamps ``standard_no_judge`` on its head (before the judge slot gate, and in
  ``_spawn_judge``), with ``risk_tier`` — a core, always-human or unreadable diff still queues;
* ``chela merge`` accepts the stamp on green CI + MERGEABLE only while the LIVE diff is still
  standard; a core run without a clean verdict is refused;
* ``chela task-finished`` on a standard diff plans lint + ONE suite — never the self-check
  battery or the sweep — and a core diff keeps both (asserted on the planned checks and on
  which verifiers ran, not on a log line).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from chela import contract, dispatcher, event_log, judge, risktier
from chela.dispatcher import CI_PASSING, CIStatus
from chela.workflow import WorkflowDef
from tests.test_contract import _seed_run
from tests.test_judge import _run_row, _tick, _wf

HEAD = "cafe1234"


@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    monkeypatch.setattr(judge, "judge_logs_dir", lambda: tmp_path / "judge-logs")
    monkeypatch.setattr(event_log, "LOG_PATH", tmp_path / "events.jsonl", raising=False)


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


# --- the stamp: before the slot gate, and in _spawn_judge --------------------------------


def _stamp(tmp_path, files, **cfg):
    wf = _wf(tmp_path, **cfg)
    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, workflow_path=str(wf.path))
        row = conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()
    with patch.object(judge, "_changed_files", return_value=files), \
         patch.object(judge, "live_judge_run", return_value=None):
        stamped = dispatcher._stamp_prose_only(wf, row)
    return stamped, dispatcher.resolve_run("abc123")


def test_a_standard_pr_is_stamped_standard_no_judge(tmp_path):
    stamped, run = _stamp(tmp_path, ["chela/dashboard/static/app.js", "tests/test_app.py"])
    assert stamped is True
    assert (run["judge_state"], run["judge_sha"], run["risk_tier"]) == \
        ("standard_no_judge", HEAD, "standard")
    assert run["judge_detail"] == judge.STANDARD_NO_JUDGE_DETAIL
    assert run["status"] == "awaiting_review"


def test_a_prose_pr_is_still_stamped_prose_only_with_its_tier(tmp_path):
    stamped, run = _stamp(tmp_path, ["docs/guide.md"])
    assert stamped is True
    assert (run["judge_state"], run["risk_tier"]) == ("prose_only", "prose")


@pytest.mark.parametrize("files", [
    ["chela/dispatcher.py"],                         # core
    ["chela/app.py", "chela/judge_select.py"],       # standard riding with core
    ["chela/app.py", "chela/contract.py"],           # always-human
    ["chela/app.py", "chela/automerge.py"],          # merge machinery is core
    ["web/AGENTS.md"],                               # agent instructions at any depth
    None,                                            # unreadable ⇒ core, never standard
    [],                                              # empty ⇒ core
], ids=["core", "standard+core", "always-human", "automerge", "instructions", "unknown",
        "empty"])
def test_a_non_standard_pr_is_never_stamped(tmp_path, files):
    stamped, run = _stamp(tmp_path, files)
    assert stamped is False
    assert (run["judge_state"], run["judge_sha"], run["risk_tier"]) == (None, None, None)


@pytest.mark.parametrize("path, stamped", [("server/auth/login.py", False),
                                           ("server/ui/page.py", True)])
def test_a_workflow_core_path_takes_the_judge(tmp_path, path, stamped):
    """The stamp classifies against the workflow's OWN `merge.core` additions."""
    got, run = _stamp(tmp_path, [path], merge={"core": ["server/auth/**"]})
    assert got is stamped
    assert run["judge_state"] == ("standard_no_judge" if stamped else None)


def test_a_standard_pr_is_stamped_while_every_judge_slot_is_busy(tmp_path, monkeypatch):
    monkeypatch.setenv("CHELA_JUDGE_MAX_CONCURRENT", "1")
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, task_id="busy1", workflow_path=str(wf.path),
                 window_name="test-9", branch_name="test-9", judge_state=judge.J_RUNNING,
                 judge_sha=HEAD, judge_started_at=dispatcher._now())
        _run_row(conn, tmp_path, task_id="abc123", workflow_path=str(wf.path))
    spawns: list[str] = []
    with patch.object(judge, "_changed_files", return_value=["chela/app.py"]):
        summary = _tick(wf, lambda w, row, sha, conn, task=None: (spawns.append(row["task_id"]), True)[1],
                        windows=("judge-test-9",), open_ids=("busy1", "abc123"))
    assert spawns == [] and summary["judged"] == 0
    assert dispatcher.resolve_run("abc123")["judge_state"] == "standard_no_judge"


@pytest.fixture
def repo(tmp_path) -> Path:
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "dev", str(origin)], check=True,
                   capture_output=True)
    work = tmp_path / "repo"
    subprocess.run(["git", "clone", str(origin), str(work)], check=True, capture_output=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        _git("config", k, v, cwd=work)
    (work / "chela").mkdir()
    (work / "chela" / "app.py").write_text("VALUE = 1\n")
    (work / "chela" / "dispatcher.py").write_text("VALUE = 1\n")
    _git("add", "chela", cwd=work)
    _git("commit", "-m", "seed", cwd=work)
    _git("push", "-u", "origin", "dev", cwd=work)
    return work


def _pr(repo: Path, rel: str) -> str:
    _git("checkout", "-b", "pr-1", cwd=repo)
    (repo / rel).write_text("VALUE = 2\n")
    _git("commit", "-am", "the PR", cwd=repo)
    sha = _git("rev-parse", "HEAD", cwd=repo)
    _git("checkout", "dev", cwd=repo)
    return sha


@pytest.mark.parametrize("rel, judged", [("chela/app.py", False), ("chela/dispatcher.py", True)])
def test_spawn_judge_stamps_standard_and_judges_core(tmp_path, repo, monkeypatch, rel, judged):
    """`_spawn_judge` itself (a real worktree, a real diff) — the path a relaunched or
    re-judged row takes past the pre-gate stamp."""
    sha = _pr(repo, rel)
    launched: list = []
    monkeypatch.setattr(dispatcher, "_launch_agent", lambda *a, **k: launched.append(1))
    wf = WorkflowDef(path=repo / "WORKFLOW.md", config=_wf(tmp_path).config, prompt_template="")
    with dispatcher._db() as conn:
        _run_row(conn, repo, workflow_path=str(wf.path), pr_head_sha=sha, worktree_path=str(repo))
        row = conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()
        spawned = dispatcher._spawn_judge(wf, row, sha, conn)
    run = dispatcher.resolve_run("abc123")
    assert (spawned, bool(launched)) == (judged, judged)
    assert run["judge_sha"] == sha
    assert run["judge_state"] == (judge.J_RUNNING if judged else "standard_no_judge")
    assert run["risk_tier"] == ("core" if judged else "standard")


# --- chela merge -----------------------------------------------------------------------------


@pytest.fixture
def wfrepo(tmp_path) -> Path:
    (tmp_path / "WORKFLOW.md").write_text(
        "---\nproject_key: CMX\nworkspace:\n  base_branch: dev\n"
        "merge:\n  core:\n    - \"server/auth/**\"\n---\nbody\n")
    return tmp_path


@pytest.mark.parametrize("judge_state, files, says", [
    ("standard_no_judge", ["chela/app.py", "tests/test_app.py"], None),   # merges, no judge row
    ("clean", ["chela/dispatcher.py"], None),                             # control: core + clean
    ("standard_no_judge", ["chela/app.py", "chela/dispatcher.py"], "'core'"),
    ("standard_no_judge", ["server/auth/login.py"], "'core'"),           # workflow core path
    ("standard_no_judge", ["docs/x.md"], None),         # prose is lighter than standard
    ("standard_no_judge", ["chela/app.py", "CLAUDE.md"], "'core'"),     # agent instructions
    ("standard_no_judge", ["chela/app.py", "chela/contract.py"], "ALWAYS-HUMAN"),
    ("standard_no_judge", None, "could not be read"),
    ("blocked", ["chela/dispatcher.py"], "not 'clean'"),                 # core, no clean verdict
    (None, ["chela/dispatcher.py"], "never ran"),
])
def test_merge_accepts_standard_no_judge_only_on_a_standard_live_diff(wfrepo, judge_state,
                                                                      files, says):
    _seed_run(wfrepo, judge_state=judge_state)
    with patch.object(contract, "_read_pr_files", return_value=files), \
         patch.object(contract, "_read_pr_base", return_value="dev"), \
         patch.object(dispatcher, "_read_pr_checks", return_value=CIStatus(CI_PASSING)), \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")), \
         patch.object(contract, "_squash_merge", return_value={"ok": True}) as squash:
        result = contract.merge("t1")
    assert result["ok"] is squash.called is (says is None), result
    if says:
        assert says in result["error"]
        assert result["tier"] == "escalate"
    else:
        event = event_log.read(types=["orchestrator.merge"])["events"][-1]
        assert f"(judge {judge_state}, CI green, mergeable)" in event["summary"]


def test_standard_no_judge_is_merge_ready():
    assert judge.J_STANDARD_NO_JUDGE == "standard_no_judge"
    assert "standard_no_judge" in judge.MERGE_READY


# --- task-finished: the planned checks follow the tier ---------------------------------------


@pytest.mark.parametrize("tier, experiments, planned", [
    ("standard", True, ("lint", "suite")),
    ("standard", False, ("lint", "suite")),
    ("core", True, ("self_check", "sweep")),
    ("core", False, ("sweep",)),
    ("prose", False, ("sweep",)),
    ("human", True, ("self_check", "sweep")),
])
def test_hand_in_checks_by_tier(tier, experiments, planned):
    assert dispatcher.hand_in_checks(tier, experiments=experiments) == planned


def _task_finished(tier, argv, standard=None):
    from chela import main

    std = standard or {"ok": True, "steps": [
        {"name": "lint", "cmd": "ruff check", "passed": True, "detail": ""},
        {"name": "suite", "cmd": "pytest -q", "passed": True, "detail": ""}]}
    with patch.object(dispatcher, "hand_in_tier", return_value=tier) as tier_of, \
         patch.object(dispatcher, "verify_standard_hand_in", return_value=std) as standard_v, \
         patch.object(dispatcher, "verify_self_check",
                      return_value={"ok": True, "blocking": 0, "cannot_verify": "",
                                    "outcomes": []}) as self_check, \
         patch.object(dispatcher, "verify_sweep",
                      return_value={"ok": True, "survivors": [], "mutations": 0, "killed": 0,
                                    "cannot_verify": "", "recovered": ""}) as sweep_v, \
         patch.object(dispatcher, "request_task_finished",
                      return_value={"ok": True, "task_id": "t1"}) as finish, \
         patch.object(sys, "argv", ["chela", "task-finished", "t1", *argv]):
        code = 0
        try:
            main.main()
        except SystemExit as e:
            code = e.code
    return code, tier_of, standard_v, self_check, sweep_v, finish


def test_a_standard_hand_in_runs_lint_and_the_suite_never_the_battery(tmp_path, capsys):
    code, tier_of, standard_v, self_check, sweep_v, finish = _task_finished(
        "standard", ["--self-check-experiments", str(tmp_path / "e.json")])
    assert code == 0
    tier_of.assert_called_once_with("t1")
    standard_v.assert_called_once_with("t1")
    self_check.assert_not_called()
    sweep_v.assert_not_called()
    finish.assert_called_once()
    assert "sweep_equivalents" not in finish.call_args.kwargs
    out = capsys.readouterr().out
    assert "[PASSED  ] lint: ruff check" in out and "[PASSED  ] suite: pytest -q" in out
    assert "is not run on a standard-tier PR" in out


def test_a_core_hand_in_still_runs_the_battery_and_the_sweep(tmp_path):
    code, _, standard_v, self_check, sweep_v, finish = _task_finished(
        "core", ["--self-check-experiments", str(tmp_path / "e.json")])
    assert code == 0
    standard_v.assert_not_called()
    self_check.assert_called_once_with("t1", str(tmp_path / "e.json"))
    sweep_v.assert_called_once_with("t1")
    finish.assert_called_once()


def test_a_standard_hand_in_ignores_sweep_equivalents(capsys):
    code, *_, sweep_v, finish = _task_finished(
        "standard", ["--no-new-guards", "--sweep-equivalent", "chela/app.py:3: log only"])
    assert code == 0
    sweep_v.assert_not_called()
    assert "sweep_equivalents" not in finish.call_args.kwargs
    assert "--sweep-equivalent is ignored" in capsys.readouterr().out


@pytest.mark.parametrize("standard, says", [
    ({"ok": False, "steps": [{"name": "lint", "cmd": "ruff check", "passed": False,
                              "detail": "exit 1\nF401 unused"}]}, "F401 unused"),
    ({"ok": False, "error": "no run with a worktree on record for t1"}, "could not run"),
])
def test_a_red_standard_hand_in_is_refused(standard, says, capsys):
    code, *_, finish = _task_finished("standard", ["--no-new-guards"], standard)
    assert code == 1
    finish.assert_not_called()
    assert says in capsys.readouterr().out


# --- hand_in_tier: the run's OWN worktree diff -------------------------------------------


def _wf_file(tmp_path: Path, extra: str = "") -> Path:
    p = tmp_path / "WORKFLOW.md"
    p.write_text("---\nproject_key: TEST\nworkspace:\n  base_branch: dev\n" + extra
                 + "---\nbody\n")
    return p


def _insert(task_id: str, worktree, workflow) -> None:
    with dispatcher._db() as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, window_name, "
            "worktree_path, branch_name, started_at, attempt, task_number) "
            "VALUES (?, ?, 't', 'running', 'w', ?, 'b', '2026-10-20T00:00:00+00:00', 1, 1)",
            (task_id, str(workflow) if workflow else None, str(worktree) if worktree else None))


@pytest.mark.parametrize("rel, extra, tier", [
    ("chela/app.py", "", "standard"),
    ("chela/dispatcher.py", "", "core"),
    ("chela/app.py", "merge:\n  core:\n    - \"chela/app.py\"\n", "core"),
])
def test_hand_in_tier_reads_the_runs_own_worktree(tmp_path, repo, rel, extra, tier):
    _git("checkout", "-b", "work", cwd=repo)
    (repo / rel).write_text("VALUE = 3\n")
    _git("commit", "-am", "work", cwd=repo)
    _insert("t1", repo, _wf_file(tmp_path, extra))
    _insert("t2", tmp_path / "nowhere", _wf_file(tmp_path, extra))   # another run's row
    assert dispatcher.hand_in_tier("t1") == tier


@pytest.mark.parametrize("case", ["no-run", "no-worktree", "no-base", "raises"])
def test_hand_in_tier_unknown_is_core(tmp_path, repo, case):
    _git("checkout", "-b", "work", cwd=repo)
    (repo / "chela" / "app.py").write_text("VALUE = 3\n")
    _git("commit", "-am", "work", cwd=repo)
    wf = _wf_file(tmp_path)
    if case == "no-worktree":
        _insert("t1", None, wf)
    elif case == "no-base":
        wf.write_text("---\nproject_key: TEST\nworkspace:\n  base_branch: nope\n---\nbody\n")
        _insert("t1", repo, wf)
    elif case != "no-run":
        _insert("t1", repo, wf)
    if case == "raises":
        with patch.object(judge, "_changed_files", side_effect=RuntimeError("boom")):
            assert dispatcher.hand_in_tier("t1") == risktier.CORE
    else:
        assert dispatcher.hand_in_tier("t1") == risktier.CORE


# --- verify_standard_hand_in: lint, then ONE suite, from the workflow --------------------


def _std_run(tmp_path: Path, lint: str | None, test_cmd: str | None, timeout: int = 77) -> Path:
    wt = tmp_path / "wt"
    wt.mkdir()
    lines = ["---", "project_key: TEST", "hand_in:", "  max_pr_lines: 1000"]
    if lint is not None:
        lines.append(f"  lint_cmd: '{lint}'")
    lines += ["judge:", f"  suite_timeout_seconds: {timeout}"]
    if test_cmd is not None:
        lines.append(f"  test_cmd: '{test_cmd}'")
    wf = tmp_path / "WORKFLOW.md"
    wf.write_text("\n".join(lines + ["---", "body", ""]))
    _insert("t1", wt, wf)
    return wt


def _recording(results):
    calls: list = []

    def run_suite(cmd, cwd, timeout=None, extra_env=None):
        calls.append((cmd, cwd, timeout))
        code = results[len(calls) - 1]
        return judge.SuiteResult(True, code, 0, 0, 0, f"tail-{len(calls)}")
    return calls, run_suite


def test_standard_runs_lint_then_the_suite_in_the_runs_worktree(tmp_path):
    wt = _std_run(tmp_path, "ruff check chela", "pytest -q")
    calls, fake = _recording([0, 0])
    with patch.object(judge, "run_suite", side_effect=fake):
        result = dispatcher.verify_standard_hand_in("t1")
    assert calls == [("ruff check chela", wt, 77.0), ("pytest -q", wt, 77.0)]
    assert result == {"ok": True, "steps": [
        {"name": "lint", "cmd": "ruff check chela", "passed": True, "detail": ""},
        {"name": "suite", "cmd": "pytest -q", "passed": True, "detail": ""}]}


def test_a_red_lint_stops_before_the_suite(tmp_path):
    _std_run(tmp_path, "ruff check chela", "pytest -q")
    calls, fake = _recording([1, 0])
    with patch.object(judge, "run_suite", side_effect=fake):
        result = dispatcher.verify_standard_hand_in("t1")
    assert [c[0] for c in calls] == ["ruff check chela"]
    assert result["ok"] is False
    assert result["steps"] == [{"name": "lint", "cmd": "ruff check chela", "passed": False,
                                "detail": "exit 1\ntail-1"}]


def test_a_red_suite_fails_the_hand_in(tmp_path):
    _std_run(tmp_path, "ruff check chela", "pytest -q")
    calls, fake = _recording([0, 2])
    with patch.object(judge, "run_suite", side_effect=fake):
        result = dispatcher.verify_standard_hand_in("t1")
    assert result["ok"] is False and result["steps"][-1]["passed"] is False
    assert result["steps"][-1]["detail"] == "exit 2\ntail-2"


def test_a_suite_that_could_not_run_fails_the_hand_in(tmp_path):
    _std_run(tmp_path, None, "pytest -q")
    with patch.object(judge, "run_suite", return_value=judge.SuiteResult(
            False, -1, 0, 0, 0, "", "the suite did not finish in 77s")):
        result = dispatcher.verify_standard_hand_in("t1")
    assert result["ok"] is False
    assert result["steps"][-1] == {"name": "suite", "cmd": "pytest -q", "passed": False,
                                   "detail": "the suite did not finish in 77s"}


def test_an_unset_lint_is_skipped_and_said(tmp_path):
    wt = _std_run(tmp_path, None, "pytest -q")
    calls, fake = _recording([0])
    with patch.object(judge, "run_suite", side_effect=fake):
        result = dispatcher.verify_standard_hand_in("t1")
    assert calls == [("pytest -q", wt, 77.0)]
    assert result["ok"] is True
    assert result["steps"][0] == {"name": "lint", "cmd": "", "passed": None,
                                  "detail": "the workflow sets no `hand_in.lint_cmd` — skipped"}


@pytest.mark.parametrize("lint", [None, "ruff check chela"])
def test_an_unset_suite_fails_closed(tmp_path, lint):
    """A standard PR gets no judge round: with no suite to run, nothing measured it."""
    _std_run(tmp_path, lint, None)
    calls, fake = _recording([0])
    with patch.object(judge, "run_suite", side_effect=fake):
        result = dispatcher.verify_standard_hand_in("t1")
    assert [c[0] for c in calls] == ([lint] if lint else [])
    assert result["ok"] is False
    assert result["steps"][-1] == {"name": "suite", "cmd": "", "passed": False,
                                   "detail": "the workflow sets no `judge.test_cmd` — nothing to run"}


def test_real_commands_really_run(tmp_path):
    """No fake: the real `run_suite`, a red lint command, and the run's own worktree."""
    script = tmp_path / "lint.py"
    script.write_text("import os, sys\nsys.exit(0 if os.path.exists('marker') else 3)\n")
    wt = _std_run(tmp_path, f"{sys.executable} {script}", f"{sys.executable} -c pass")
    assert dispatcher.verify_standard_hand_in("t1")["ok"] is False
    (wt / "marker").write_text("")
    assert dispatcher.verify_standard_hand_in("t1")["ok"] is True


@pytest.mark.parametrize("case", ["no-run", "bad-workflow"])
def test_verify_standard_hand_in_fails_closed(tmp_path, case):
    if case == "bad-workflow":
        _insert("t1", tmp_path, tmp_path / "missing" / "WORKFLOW.md")
    result = dispatcher.verify_standard_hand_in("t1")
    assert result["ok"] is False and result["error"]
