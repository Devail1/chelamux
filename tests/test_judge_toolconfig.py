"""⚖️🧰 CMX-259 — a survivor in tool config CI enforces is ADVISORY unless the PR claims it.

CMX-176 (#751): 4 judge rounds on single `eslint.config.js` / `.dependency-cruiser.cjs` /
`package.json` lines while CI stayed green. These pin chela/toolconfig.py and its wiring.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

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


def _repo(tmp_path: Path, pr_files: dict[str, str]) -> Path:
    """origin/dev = a green suite that never reads eslint.config.js; HEAD adds ``pr_files``."""
    origin, work = tmp_path / "origin.git", tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "dev", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True,
                   capture_output=True)
    for rel, text in {"feature.py": FEATURE, "eslint.config.js": ESLINT, "package.json": PKG,
                      "test_feature.py": "def test_ok():\n    assert True\n"}.items():
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
    assert "🧰 advisory" in body and "no test this PR changes names it" in body


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
    ("package.json", '"eslint src"', '"eslint src', False),  # breaks the JSON: unproven
    ("bad/package.json", "x", "y", False),               # not UTF-8: unproven, no crash
])
def test_classify_by_file_and_json_key(tmp_path, monkeypatch, file, before, after, advisory):
    monkeypatch.setattr(toolconfig, "changed_tests", lambda *a: {})
    (tmp_path / "package.json").write_text(PKG)
    (tmp_path / "bad").mkdir()
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


def test_changed_tests_is_none_without_a_base_or_a_resolvable_one(tmp_path):
    work = _repo(tmp_path, {"web/tests/a.ts": "x", "web/src/b.ts": "eslint.config.js"})
    (work / "test_untracked.py").write_text("u")
    assert toolconfig.changed_tests(work, "") is None
    assert toolconfig.changed_tests(work, "nope") is None
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
