"""⚖️🧰 CMX-259 — a survivor in tool config CI enforces is ADVISORY unless the PR claims it.

CMX-176 (#751): 4 judge rounds on single `eslint.config.js` / `.dependency-cruiser.cjs` /
`package.json` lines while CI stayed green. These pin chela/toolconfig.py and its wiring.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from unittest.mock import patch

import pytest

from chela import judge, toolconfig

TEST_CMD = f'"{sys.executable}" -m pytest -q'
ESLINT = 'export default [{ rules: { "no-console": "error" } }];\n'
FEATURE = "def add(a, b):\n    return a + b\n"
PKG = '{\n  "type": "module",\n  "scripts": {\n    "lint": "eslint src"\n  }\n}\n'
LINT_EXP = {"guard": "no-console is an error", "file": "eslint.config.js",
            "before": '"no-console": "error"', "after": '"no-console": "off"'}
CODE_EXP = {"guard": "add adds", "file": "feature.py",
            "before": "return a + b", "after": "return a - b"}


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), "-c", "user.email=t@t", "-c", "user.name=t",
                           "-c", "commit.gpgsign=false", *args],
                          check=True, capture_output=True, text=True).stdout


def _repo(tmp_path: Path, pr_files: dict[str, str], work: Path | None = None) -> Path:
    """origin/dev = a green suite that never reads eslint.config.js; HEAD adds ``pr_files``."""
    origin, work = tmp_path / "origin.git", work or tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "dev", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True,
                   capture_output=True)
    for rel, text in {"feature.py": FEATURE, "eslint.config.js": ESLINT, "package.json": PKG,
                      "web/lint.rules.js": ESLINT, "test_feature.py": "def test_ok():\n    assert True\n"}.items():
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        (work / rel).write_text(text)
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "base")
    _git(work, "push", "-q", "origin", "HEAD:dev")
    _git(work, "fetch", "-q", "origin")
    for rel, text in pr_files.items():
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        (work / rel).write_text(text)
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "pr")
    return work


def _run(work: Path, *exps: dict) -> judge.Report:
    return judge.run_experiments(work, TEST_CMD, {"experiments": list(exps)}, timeout=120,
                                 base_branch="dev")


def test_an_unclaimed_eslint_survivor_is_advisory_and_the_verdict_is_clean(tmp_path):
    work = _repo(tmp_path, {"eslint.config.js": ESLINT + "// tweak\n"})
    report = _run(work, LINT_EXP)
    [o] = report.outcomes
    assert (o.verdict, o.blocking, report.state) == (judge.SURVIVED, False, judge.J_CLEAN)
    assert "`eslint.config.js` is tool config (`eslint.config.*`)" in o.advisory
    body = judge.comment_body(report, None, TEST_CMD)
    assert "| **SURVIVED** 🧰 advisory |" in body and "no test this PR changes names it" in body


def test_the_same_survivor_blocks_when_a_changed_test_claims_the_file(tmp_path):
    work = _repo(tmp_path, {"tests/test_lint_gate.py":
                            "# runs eslint with eslint.config.js on a failing fixture\n"
                            "def test_gate():\n    assert True\n"})
    report = _run(work, LINT_EXP)
    [o] = report.outcomes
    assert (o.advisory, report.state) == ("", judge.J_BLOCKED)
    assert "`tests/test_lint_gate.py` names this tool-config file" in o.reason
    assert "🧰 advisory" not in judge.block_body(report, None, TEST_CMD)


def test_a_code_survivor_still_blocks_and_runs_before_config(tmp_path):
    work = _repo(tmp_path, {"feature.py": FEATURE + "\n"})
    report = _run(work, LINT_EXP, CODE_EXP)
    assert [(o.experiment.file, o.blocking) for o in report.outcomes] == [
        ("feature.py", True), ("eslint.config.js", False)]
    assert report.state == judge.J_BLOCKED
    assert "### 🧰 advisory" in judge.block_body(report, None, TEST_CMD)


def test_a_flaky_advisory_survivor_is_not_an_unknown(tmp_path, monkeypatch):
    work = _repo(tmp_path, {"eslint.config.js": ESLINT + "// tweak\n"})

    def flip(worktree, cmd, items, outcomes, *a, **k):
        outcomes[0].flaky = True
        return ""
    monkeypatch.setattr(judge, "_check_consistency", flip)
    report = judge.run_experiments(work, TEST_CMD, {"experiments": [LINT_EXP]}, timeout=120,
                                   base_branch="dev", consistency_sample=1)
    assert (report.state, report.cannot_verify) == (judge.J_CLEAN, "")


def _survivor(file: str, before: str = "x", after: str = "y") -> judge.Outcome:
    return judge.Outcome(judge.Experiment("g", file, before, after), judge.SURVIVED, "green")


@pytest.mark.parametrize("file, before, after, advisory", [
    ("src/app.ts", "x", "y", False),                       # code — as today
    ("frontend/eslint.config.js", "x", "y", True),         # basename glob at any depth
    ("tsconfig.app.json", "x", "y", True),
    (".github/workflows/ci.yml", "x", "y", True),
    ("src/.github/workflows/ci.yml", "x", "y", False),     # a path glob is anchored
    ("package.json", '"eslint src"', '"true"', True),     # inside `scripts`
    ("package.json", '"module"', '"commonjs"', False),    # outside `scripts`
    ("package.json", '"module",\n  "scripts": {\n    "lint": "eslint src"',
     '"commonjs",\n  "scripts": {\n    "lint": "true"', False),   # `scripts` AND another key
    ("package.json", '"eslint src"', '"eslint src', False),  # breaks the JSON: unproven
    ("bad/package.json", "x", "y", False),
    ("arr/package.json", '"eslint src"', '"true"', False),  # not a JSON object
])
def test_classify_by_file_and_json_key(tmp_path, monkeypatch, file, before, after, advisory):
    monkeypatch.setattr(toolconfig, "changed_tests", lambda *a: {})
    (tmp_path / "package.json").write_text(PKG)
    (tmp_path / "bad").mkdir()
    (tmp_path / "arr").mkdir()
    (tmp_path / "arr" / "package.json").write_text('["eslint src"]')
    (tmp_path / "bad" / "package.json").write_bytes(b'{"scripts": "\xff"}')
    o = _survivor(file, before, after)
    toolconfig.classify([o], tmp_path, "dev", toolconfig.DEFAULT_GLOBS)
    assert (bool(o.advisory), o.blocking) == (advisory, not advisory)


def test_classify_fails_closed_when_the_changed_tests_cannot_be_read(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(toolconfig, "changed_tests", lambda *a: calls.append(a))
    o, killed = _survivor("eslint.config.js"), _survivor("eslint.config.js")
    killed.verdict = judge.KILLED
    toolconfig.classify([o, killed], tmp_path, "dev", toolconfig.DEFAULT_GLOBS)
    assert (o.advisory, o.blocking, killed.advisory, calls) == ("", True, "", [(tmp_path, "dev")])
    assert o.reason.endswith("could not be read to rule out a claim")


def test_a_killed_tool_config_experiment_is_never_advisory(tmp_path, monkeypatch):
    monkeypatch.setattr(toolconfig, "changed_tests", lambda *a: {})
    o = _survivor("eslint.config.js")
    o.verdict = judge.KILLED
    toolconfig.classify([o], tmp_path, "dev", toolconfig.DEFAULT_GLOBS)
    assert (o.advisory, o.reason) == ("", "green")


def test_changed_tests_is_none_without_a_base_or_a_resolvable_one(tmp_path, monkeypatch):
    work = _repo(tmp_path, {"web/tests/a.ts": "x", "web/src/b.ts": "eslint.config.js"})
    (work / "test_untracked.py").write_text("u")
    assert toolconfig.changed_tests(work, "") is None
    assert toolconfig.changed_tests(work, "nope") is None
    with monkeypatch.context() as m:
        m.setattr(toolconfig.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(
            subprocess.TimeoutExpired("git", 60)))
        assert toolconfig.changed_tests(work, "dev") is None
    assert toolconfig.changed_tests(work, "dev") == {"web/tests/a.ts": "x",
                                                     "test_untracked.py": "u"}


@pytest.mark.parametrize("value, expected", [
    (None, toolconfig.DEFAULT_GLOBS), ("eslint.*", toolconfig.DEFAULT_GLOBS),
    ([" a.cfg "], ("a.cfg",)), ([], ()), (["a", 3], toolconfig.DEFAULT_GLOBS),
])
def test_tool_config_from_workflow_reaches_the_self_check(tmp_path, monkeypatch, value,
                                                          expected):
    extra = "" if value is None else f"  tool_config: {json.dumps(value)}\n"
    wf = tmp_path / "WORKFLOW.md"
    wf.write_text("---\nproject_key: TEST\nworkspace:\n  base_branch: dev\njudge:\n"
                  "  test_cmd: x\n" + extra + "---\n")
    (tmp_path / "e.json").write_text(json.dumps({"experiments": [LINT_EXP]}))
    seen = {}
    monkeypatch.setattr(judge, "self_check", lambda *a, **k: seen.update(k) or judge.Report())
    judge.run_self_check(tmp_path, tmp_path / "e.json", workflow_path=wf)
    assert (seen["tool_config"], seen["base_branch"]) == (expected, "dev")


def test_self_check_runs_code_first_so_a_budget_starves_config(tmp_path, monkeypatch):
    monkeypatch.setattr(judge, "provision_suite_env", lambda wt: "")
    monkeypatch.setattr(judge, "run_baseline", lambda *a, **k: (
        judge.SuiteResult(True, 0, 1, 0, 0, "1 passed"), None, "off"))
    monkeypatch.setattr(judge, "_apply_experiments", lambda wt, cmd, items, *a: (
        [_survivor(items[0]["file"])], ""))
    from chela import sweep
    report = judge.self_check(tmp_path, "t", {"experiments": [LINT_EXP, CODE_EXP]}, timeout=1,
                              budget=sweep.SweepBudget(900.0, 1), base_branch="dev")
    assert [o.experiment.file for o in report.outcomes] == ["feature.py"]
    assert [o.experiment.file for o in report.unrun] == ["eslint.config.js"]


# --- END TO END: WORKFLOW.md `judge.tool_config` → the real judge round / self-check ------
# A custom glob the defaults do NOT cover, so dropping `tool_config=` anywhere on the way
# (the defaults take over) or the classify call itself turns the verdict BLOCKED.

NESTED = {"guard": "no-console is an error", "file": "web/lint.rules.js",
          "before": '"no-console": "error"', "after": '"no-console": "off"'}
CLAIM = {"web/tests/lint.test.ts": "// runs eslint -c lint.rules.js on a failing fixture\n"}


def _workflow(tmp_path: Path) -> Path:
    wf = tmp_path / "WORKFLOW.md"
    wf.write_text("---\nproject_key: TEST\ntracker:\n  kind: markdown\n  path: TODO.md\n"
                  f"workspace:\n  root: {tmp_path / 'wts'}\n  base_branch: dev\n"
                  f"judge:\n  test_cmd: {json.dumps(TEST_CMD)}\n  suite_timeout_seconds: 120\n"
                  '  tool_config: ["lint.rules.js"]\n---\n\ndo it: {{task_title}}\n')
    return wf


def _judge_round(tmp_path: Path, monkeypatch, pr_files: dict[str, str]):
    from chela import dispatcher

    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    wf = _workflow(tmp_path)
    _repo(tmp_path, pr_files, work=tmp_path / "wts" / "judge-t259")
    with dispatcher._db() as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, window_name, branch_name,"
            " worktree_path, started_at, attempt, task_number, pr_url, pr_state, pr_checks,"
            " pr_head_sha, rework_count) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("t259", str(wf), "t", "awaiting_review", "w", "b", str(tmp_path), "2026-10-18",
             1, 1, "https://github.com/o/r/pull/9", "open", dispatcher.CI_PASSING, "cafe", 0))
        conn.commit()
    exp = tmp_path / "e.json"
    exp.write_text(json.dumps({"experiments": [NESTED]}))
    with patch.object(dispatcher, "_post_pr_comment", return_value=(True, "")):
        result = judge.judge_run("t259", exp, cleanup=False)
    return result


def test_judge_round_reads_tool_config_and_an_unclaimed_survivor_is_advisory(tmp_path,
                                                                             monkeypatch):
    result = _judge_round(tmp_path, monkeypatch, {"web/lint.rules.js": ESLINT + "// t\n"})
    [o] = result["outcomes"]
    assert (result["state"], result["blocking"]) == (judge.J_CLEAN, 0)
    assert (o["verdict"], o["advisory"]) == (
        judge.SURVIVED, "`web/lint.rules.js` is tool config (`lint.rules.js`) that CI "
        "enforces, and no test this PR changes names it")


def test_judge_round_blocks_when_a_nested_test_claims_the_file_by_basename(tmp_path,
                                                                          monkeypatch):
    result = _judge_round(tmp_path, monkeypatch, CLAIM)
    [o] = result["outcomes"]
    assert (result["state"], result["blocking"], o["advisory"]) == (judge.J_BLOCKED, 1, "")


@pytest.mark.parametrize("pr_files, state", [
    ({"web/lint.rules.js": ESLINT + "// t\n"}, judge.J_CLEAN), (CLAIM, judge.J_BLOCKED)])
def test_self_check_reads_tool_config_and_classifies_end_to_end(tmp_path, pr_files, state):
    work = _repo(tmp_path, pr_files)
    (tmp_path / "e.json").write_text(json.dumps({"experiments": [NESTED]}))
    out = judge.run_self_check(work, tmp_path / "e.json", workflow_path=_workflow(tmp_path))
    [o] = out["outcomes"]
    assert (out["state"], o["verdict"], bool(o["advisory"])) == (
        state, judge.SURVIVED, state == judge.J_CLEAN)
