"""⚖️🧹 CMX-97 — the standalone judge: ``chela judge sweep`` in ANY git repo, and the GitHub
Action built on it (``action.yml`` + ``scripts/judge-sweep-action.sh`` +
:mod:`chela.sweep_github`).

Every repo here is a throwaway ``git init`` with NO chela config — no WORKFLOW.md, no run
record, no tmux, no tracker — and ``gh`` is a fake on ``PATH`` that records its calls.
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
import yaml

from chela import sweep, sweep_github

REPO_ROOT = Path(__file__).resolve().parents[1]
ACTION_YML = REPO_ROOT / "action.yml"
ACTION_SH = REPO_ROOT / "scripts" / "judge-sweep-action.sh"
PY_BIN = str(Path(sys.executable).parent)

BASE_CALC = '''\
def clamp(x, lo):
    if x < lo:
        return lo
    return x
'''
BRANCH_CALC = BASE_CALC + '''

def is_big(x):
    if x > 100:
        return "big"
    return "small"
'''
BIG_LINE = 8                       # `    if x > 100:` in BRANCH_CALC
UNRELATED_TEST = "from calc import clamp\n\n\ndef test_clamp():\n    assert clamp(-1, 0) == 0\n"
COVERING_TEST = UNRELATED_TEST + '''

def test_is_big():
    from calc import is_big
    assert is_big(101) == "big"
    assert is_big(100) == "small"
'''


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *args],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def _bare(root: Path, branch: dict[str, str], *, test_body: str = UNRELATED_TEST,
          base_extra: dict[str, str] | None = None) -> Path:
    """A plain repo: ``main`` holds ``calc.py`` + a test; branch ``feature`` writes
    ``branch`` on top and commits it. No remote, no chela config of any kind."""
    root.mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "calc.py").write_text(BASE_CALC)
    (root / "tests" / "test_calc.py").write_text(UNRELATED_TEST)
    for rel, body in (base_extra or {}).items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    _git(root, "checkout", "-qb", "feature")
    (root / "tests" / "test_calc.py").write_text(test_body)
    for rel, body in branch.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "feature", "--allow-empty")
    return root


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    """A user's shell with NO chela anywhere: a fresh HOME, no CHELA_*; ``python`` resolves
    to this interpreter (it has pytest)."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CHELA_", "GITHUB_", "PYTEST_")) and k not in ("TMUX", "TMUX_PANE")}
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env.update(HOME=str(home), PATH=f"{PY_BIN}:{env.get('PATH', '')}")
    env.update(extra)
    return env


CHELA = [sys.executable, "-c", "import sys; from chela.main import main; main()"]


def _chela(root: Path, env: dict, *args: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run([*CHELA, "judge", "sweep", *args], cwd=str(root), env=env,
                          capture_output=True, text=True, timeout=300, **kw)


# --- 1. a bare repo, no chela config ---------------------------------------------------------


def test_a_bare_repo_with_no_chela_config_sweeps_with_a_detected_suite(tmp_path):
    root = _bare(tmp_path / "r", {"calc.py": BRANCH_CALC})
    env = _env(tmp_path)
    out = _chela(root, env, "--base", "main", "--json")
    assert out.returncode == 1, out.stdout + out.stderr
    d = json.loads(out.stdout)
    assert d["verdict"] == "survivors"
    assert d["test_cmd"] == "python -m pytest -q"
    assert d["test_cmd_source"] == "detected (tests/)"
    assert {r["key"] for r in d["survivors"]} == {f"calc.py:{BIG_LINE}"}
    assert d["unsupported"] == [] and d["base"] == "main"
    # --json keeps stdout pure JSON; progress went to stderr.
    assert f"calc.py:{BIG_LINE}" in out.stderr
    assert _git(root, "status", "--porcelain") == ""
    assert not (tmp_path / "home" / ".chela").exists()

    good = _bare(tmp_path / "g", {"calc.py": BRANCH_CALC}, test_body=COVERING_TEST)
    out = _chela(good, env, "--base", "main", "--json")
    assert out.returncode == 0, out.stdout + out.stderr
    d = json.loads(out.stdout)
    assert d["verdict"] == "clean" and d["survivors"] == [] and d["killed"] == d["mutations"] >= 2


def test_text_mode_names_the_detected_suite_and_exits_1_on_survivors(tmp_path):
    root = _bare(tmp_path / "r", {"calc.py": BRANCH_CALC})
    out = _chela(root, _env(tmp_path), "--base", "main")
    assert out.returncode == 1, out.stdout + out.stderr
    assert "suite: `python -m pytest -q` — detected (tests/)" in out.stdout
    assert f"[SURVIVED] calc.py:{BIG_LINE}" in out.stdout


def test_standalone_never_provisions_the_users_env(tmp_path):
    """A pip-managed repo with a pyproject.toml must not grow a uv `.venv` (or need uv)."""
    root = _bare(tmp_path / "r", {"calc.py": BRANCH_CALC},
                 base_extra={"pyproject.toml": "[project]\nname='x'\nversion='0'\n"})
    out = _chela(root, _env(tmp_path), "--base", "main", "--json")
    assert out.returncode == 1, out.stdout + out.stderr
    assert json.loads(out.stdout)["test_cmd_source"] == "detected (pyproject.toml)"
    assert not (root / ".venv").exists()


@pytest.mark.parametrize("provision", [True, False])
def test_sweep_provisions_only_when_asked(tmp_path, monkeypatch, provision):
    root = _bare(tmp_path / "r", {"calc.py": BRANCH_CALC})
    calls = []
    monkeypatch.setattr(sweep.judge, "provision_suite_env",
                        lambda wt: calls.append(wt) or "no uv here")
    report = sweep.sweep(root, "main", f'"{sys.executable}" -m pytest -q', timeout=120,
                         provision=provision)
    if provision:
        assert calls == [root.resolve()]
        assert "could not be PROVISIONED" in report.cannot_verify
    else:
        assert calls == []
        assert report.cannot_verify == ""
        assert {r.mutation.key for r in report.survivors} == {f"calc.py:{BIG_LINE}"}


# --- 2. unsupported languages are UNKNOWN, never a pass --------------------------------------


GO_FILE = "package main\n\nfunc big(x int) bool {\n\tif x > 100 {\n\t\treturn true\n\t}\n\treturn false\n}\n"


def test_only_an_unsupported_language_changed_is_unknown_exit_3_never_clean(tmp_path):
    root = _bare(tmp_path / "r", {"big.go": GO_FILE})
    out = _chela(root, _env(tmp_path), "--base", "main", "--json")
    assert out.returncode == 3, out.stdout + out.stderr
    d = json.loads(out.stdout)
    assert d["verdict"] == "unknown"
    assert d["unsupported"] == ["big.go"]
    assert d["mutations"] == 0 and d["survivors"] == []
    out = _chela(root, _env(tmp_path), "--base", "main")
    assert out.returncode == 3
    assert "[UNKNOWN ] big.go — not supported: only Python (and .js/.mjs, line-based) is " \
           "mutated" in out.stdout
    assert "This is not a pass." in out.stdout


def test_a_clean_python_change_next_to_an_unsupported_one_is_still_unknown(tmp_path):
    root = _bare(tmp_path / "r", {"calc.py": BRANCH_CALC, "web/app.ts": "export const a = 1;\n"},
                 test_body=COVERING_TEST)
    out = _chela(root, _env(tmp_path), "--base", "main", "--json")
    assert out.returncode == 3, out.stdout + out.stderr
    d = json.loads(out.stdout)
    assert d["verdict"] == "unknown" and d["killed"] >= 2 and d["unsupported"] == ["web/app.ts"]


def test_survivors_outrank_unknown(tmp_path):
    root = _bare(tmp_path / "r", {"calc.py": BRANCH_CALC, "lib.rs": "fn a() {}\n"})
    out = _chela(root, _env(tmp_path), "--base", "main", "--json")
    assert out.returncode == 1, out.stdout + out.stderr
    d = json.loads(out.stdout)
    assert d["verdict"] == "survivors" and d["unsupported"] == ["lib.rs"]


@pytest.mark.parametrize("rel, unsupported", [
    ("a.ts", True), ("a.TS", True), ("src/x.go", True), ("x.rs", True), ("A.java", True),
    ("x.cjs", True), ("x.jsx", True), ("run.sh", True), ("x.c", True), ("x.cpp", True),
    # tests in other languages are what a suite is measured BY
    ("x_test.go", False), ("FooTest.java", False), ("FooTests.cs", False), ("a.spec.ts", False),
    ("a.test.ts", False), ("src/__tests__/a.ts", False), ("test/a.rb", False),
    ("spec/a.rb", False), ("tests/a.rs", False), ("x_spec.rb", False),
    # supported, or not source at all
    ("a.py", False), ("a.js", False), ("a.mjs", False), ("a.md", False), ("a.json", False),
    ("Makefile", False), ("a.pyi", False), ("a.yml", False),
    # names that only LOOK like tests
    ("latest.go", True), ("contest.ts", True), ("Testing.java", True), ("attest/a.go", True),
])
def test_unsupported_classification(rel, unsupported):
    assert sweep._unsupported(rel) is unsupported


def test_unsupported_files_lists_changed_and_untracked_but_not_deleted_or_ignored(tmp_path):
    root = _bare(tmp_path / "r", {"new.go": "package x\n", "calc.py": BRANCH_CALC},
                 base_extra={"old.go": "package x\n", "keep.rs": "fn a() {}\n",
                             ".gitignore": "ignored.ts\n"})
    _git(root, "rm", "-q", "old.go")
    _git(root, "commit", "-qm", "rm old.go")
    (root / "keep.rs").write_text("fn b() {}\n")          # uncommitted edit
    (root / "untracked.ts").write_text("x\n")
    (root / "ignored.ts").write_text("x\n")
    files, err = sweep.unsupported_files(root, "main")
    assert err == ""
    assert files == ["keep.rs", "new.go", "untracked.ts"]


def test_unsupported_files_runs_exactly_these_git_commands(tmp_path, monkeypatch):
    seen = []

    def fake(wt, *args):
        seen.append(args)
        return subprocess.CompletedProcess(args, 0, "abc123\n" if args[0] == "merge-base" else "")

    monkeypatch.setattr(sweep, "_git", fake)
    assert sweep.unsupported_files(tmp_path, "origin/main") == ([], "")
    assert seen == [
        ("merge-base", "origin/main", "HEAD"),
        ("diff", "--name-only", "-z", "--no-renames", "--diff-filter=d", "abc123", "--"),
        ("ls-files", "--others", "--exclude-standard", "-z"),
    ]


@pytest.mark.parametrize("failing, needle", [
    ("merge-base", "does not resolve to a merge base"),
    ("diff", "git diff failed: boom"),
    ("ls-files", "git ls-files failed: boom"),
])
def test_a_failing_git_read_is_an_error_never_no_unsupported_files(tmp_path, monkeypatch,
                                                                    failing, needle):
    def fake(wt, *args):
        if args[0] == failing:
            return subprocess.CompletedProcess(args, 128, "", "boom")
        out = "abc\n" if args[0] == "merge-base" else "x.go\0"
        return subprocess.CompletedProcess(args, 0, out, "")

    monkeypatch.setattr(sweep, "_git", fake)
    files, err = sweep.unsupported_files(tmp_path, "main")
    assert files == [] and needle in err


def test_an_empty_merge_base_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(sweep, "_git",
                        lambda wt, *a: subprocess.CompletedProcess(a, 0, "  \n", ""))
    files, err = sweep.unsupported_files(tmp_path, "main")
    assert files == [] and "does not resolve" in err


def test_a_failing_unsupported_scan_makes_the_sweep_cannot_verify(tmp_path, monkeypatch):
    root = _bare(tmp_path / "r", {"calc.py": BRANCH_CALC})
    monkeypatch.setattr(sweep, "unsupported_files", lambda wt, base: ([], "git exploded"))
    report = sweep.sweep(root, "main", "true", timeout=60, provision=False)
    assert report.cannot_verify == "git exploded"
    assert report.as_dict()["verdict"] == "cannot_verify"


def test_unsupported_with_nothing_to_mutate_skips_the_suite_and_says_so(tmp_path, monkeypatch):
    root = _bare(tmp_path / "r", {"big.go": GO_FILE})
    monkeypatch.setattr(sweep.judge, "run_baseline",
                        lambda *a, **k: pytest.fail("no mutation, so no suite run"))
    report = sweep.sweep(root, "main", "true", timeout=60, provision=False)
    assert report.unsupported == ["big.go"]
    assert report.notes == [
        "1 changed file(s) NOT swept — only Python (and .js/.mjs, line-based) is mutated; "
        "each is UNKNOWN, not clean",
        "no changed conditional, comparison or return code vs `main` — nothing to sweep",
    ]


@pytest.mark.parametrize("result, want", [
    ({"ok": False, "error": "x"}, "error"),
    ({"ok": True, "cannot_verify": "x", "survivors": [1], "unsupported": ["a.go"]},
     "cannot_verify"),
    ({"ok": True, "cannot_verify": "", "survivors": [1], "unsupported": ["a.go"]}, "survivors"),
    ({"ok": True, "cannot_verify": "", "survivors": [], "unsupported": ["a.go"]}, "unknown"),
    ({"ok": True, "cannot_verify": "", "survivors": [], "unsupported": []}, "clean"),
    ({"ok": True}, "clean"),
])
def test_verdict_precedence(result, want):
    assert sweep.verdict(result) == want


def test_exit_codes_are_exactly_these():
    assert sweep.EXIT_CODES == {"clean": 0, "survivors": 1, "cannot_verify": 2, "error": 2,
                                "unknown": 3}


def test_as_dict_carries_verdict_base_suite_and_unsupported():
    r = sweep.SweepReport(unsupported=["a.go"], base="main", test_cmd="pytest")
    d = r.as_dict()
    assert (d["verdict"], d["unsupported"], d["base"], d["test_cmd"]) == (
        "unknown", ["a.go"], "main", "pytest")
    assert sweep.SweepReport().as_dict()["verdict"] == "clean"


def test_sweep_records_the_base_and_suite_it_ran(tmp_path, monkeypatch):
    monkeypatch.setattr(sweep, "_sweep", lambda *a, **k: sweep.SweepReport())
    r = sweep.sweep(tmp_path, "origin/x", "the-cmd")
    assert (r.base, r.test_cmd) == ("origin/x", "the-cmd")


# --- 3. which suite, which base ---------------------------------------------------------------


def _which(have: set[str]):
    return lambda name: f"/usr/bin/{name}" if name in have else None


@pytest.mark.parametrize("files, have, want", [
    ({"pytest.ini": ""}, {"python"}, ("python -m pytest -q", "pytest.ini")),
    ({"pyproject.toml": ""}, {"python"}, ("python -m pytest -q", "pyproject.toml")),
    ({"setup.py": ""}, {"python"}, ("python -m pytest -q", "setup.py")),
    ({"setup.cfg": ""}, {"python"}, ("python -m pytest -q", "setup.cfg")),
    ({"tox.ini": ""}, {"python"}, ("python -m pytest -q", "tox.ini")),
    ({"conftest.py": ""}, {"python"}, ("python -m pytest -q", "conftest.py")),
    ({"tests/test_a.py": ""}, {"python"}, ("python -m pytest -q", "tests/")),
    ({"test/sub/a_test.py": ""}, {"python"}, ("python -m pytest -q", "test/")),
    ({"tests/helper.py": ""}, {"python"}, ("", "")),          # no test file: not a marker
    ({"pytest.ini": ""}, set(), ("python3 -m pytest -q", "pytest.ini")),
    ({"pyproject.toml": "", "uv.lock": ""}, {"uv", "python"},
     ("uv run pytest -q", "pyproject.toml + uv.lock")),
    ({"pyproject.toml": "", "uv.lock": ""}, {"python"}, ("python -m pytest -q", "pyproject.toml")),
    ({"pyproject.toml": ""}, {"uv", "python"}, ("python -m pytest -q", "pyproject.toml")),
    ({"package.json": '{"scripts": {"test": "node --test"}}'}, set(),
     ("npm test", "package.json scripts.test")),
    ({"package.json": '{"scripts": {"test": "jest"}}', "pnpm-lock.yaml": ""}, set(),
     ("pnpm test", "package.json scripts.test")),
    ({"package.json": '{"scripts": {"test": "jest"}}', "yarn.lock": ""}, set(),
     ("yarn test", "package.json scripts.test")),
    ({"package.json": '{"scripts": {"test": "echo \\"Error: no test specified\\" && exit 1"}}'},
     set(), ("", "")),
    ({"package.json": '{"scripts": {"test": "   "}}'}, set(), ("", "")),
    ({"package.json": '{"scripts": {"lint": "x"}}'}, set(), ("", "")),
    ({"package.json": '{"scripts": ["test"]}'}, set(), ("", "")),
    ({"package.json": '{"scripts": {"test": 5}}'}, set(), ("", "")),
    ({"package.json": "[1]"}, set(), ("", "")),
    ({"package.json": "{not json"}, set(), ("", "")),
    ({"package.json": "{not json", "go.mod": ""}, set(), ("go test ./...", "go.mod")),
    ({"go.mod": ""}, set(), ("go test ./...", "go.mod")),
    ({"Cargo.toml": ""}, set(), ("cargo test", "Cargo.toml")),
    ({}, {"python", "uv"}, ("", "")),
    # precedence: Python > package.json > go.mod > Cargo.toml
    ({"pytest.ini": "", "package.json": '{"scripts": {"test": "x"}}', "go.mod": ""}, {"python"},
     ("python -m pytest -q", "pytest.ini")),
    ({"package.json": '{"scripts": {"test": "x"}}', "go.mod": "", "Cargo.toml": ""}, set(),
     ("npm test", "package.json scripts.test")),
    ({"go.mod": "", "Cargo.toml": ""}, set(), ("go test ./...", "go.mod")),
])
def test_detect_test_cmd_table(tmp_path, monkeypatch, files, have, want):
    for rel, body in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(body)
    monkeypatch.setattr(sweep.shutil, "which", _which(have))
    assert sweep.detect_test_cmd(tmp_path) == want


def test_a_marker_that_is_a_directory_is_not_a_marker(tmp_path, monkeypatch):
    for name in ("pytest.ini", "uv.lock", "go.mod", "Cargo.toml", "package.json"):
        (tmp_path / name).mkdir()
    monkeypatch.setattr(sweep.shutil, "which", _which({"python", "uv"}))
    assert sweep.detect_test_cmd(tmp_path) == ("", "")
    (tmp_path / "setup.py").write_text("")
    assert sweep.detect_test_cmd(tmp_path) == ("python -m pytest -q", "setup.py")


def test_an_unreadable_package_json_is_not_detected(tmp_path, monkeypatch):
    (tmp_path / "package.json").write_bytes(b"\xff\xfe{")
    monkeypatch.setattr(sweep.shutil, "which", _which(set()))
    assert sweep.detect_test_cmd(tmp_path) == ("", "")


def test_default_base_is_origin_head_else_origin_dev(tmp_path):
    root = _bare(tmp_path / "r", {})
    assert sweep.default_base(root) == "origin/dev"
    _git(root, "update-ref", "refs/remotes/origin/trunk", "main")
    _git(root, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
    assert sweep.default_base(root) == "origin/trunk"


def test_default_base_argv_and_a_blank_answer(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(sweep, "_git", lambda wt, *a: seen.append((wt, a)) or
                        subprocess.CompletedProcess(a, 0, "  \n", ""))
    assert sweep.default_base(tmp_path) == "origin/dev"
    assert seen == [(tmp_path, ("symbolic-ref", "--quiet", "--short",
                                "refs/remotes/origin/HEAD"))]


def _wf(path: Path, *, cmd: str | None = None, base: str | None = None) -> Path:
    head = ["---", "project_key: TEST"]
    if base:
        head += ["workspace:", f"  base_branch: {base}"]
    if cmd:
        head += ["judge:", f"  test_cmd: {json.dumps(cmd)}"]
    path.write_text("\n".join(head + ["---", "body", ""]))
    return path


@pytest.mark.parametrize("flag, wf_cmd, detected, want_cmd, want_source", [
    ("flag-cmd", "wf-cmd", True, "flag-cmd", "--test-cmd"),
    (None, "wf-cmd", True, "wf-cmd", "WF judge.test_cmd"),
    (None, None, True, "python -m pytest -q", "detected (pytest.ini)"),
    ("  ", None, True, "python -m pytest -q", "detected (pytest.ini)"),
    ("flag-cmd", None, False, "flag-cmd", "--test-cmd"),
])
@pytest.mark.parametrize("with_wf", [True, False])
def test_run_sweep_suite_precedence_and_source(tmp_path, monkeypatch, flag, wf_cmd, detected,
                                               want_cmd, want_source, with_wf):
    if wf_cmd and not with_wf:
        pytest.skip("a workflow command needs a workflow")
    root = tmp_path / "r"
    root.mkdir()
    if detected:
        (root / "pytest.ini").write_text("")
    monkeypatch.setattr(sweep.shutil, "which", _which({"python"}))
    wf = _wf(tmp_path / "WORKFLOW.md", cmd=wf_cmd) if with_wf else None
    calls = []
    monkeypatch.setattr(sweep, "sweep", lambda *a, **kw: calls.append((a, kw)) or
                        sweep.SweepReport())
    d = sweep.run_sweep(root, base="main", workflow_path=wf, test_cmd=flag)
    (args, kw), = calls
    assert args == (root, "main", want_cmd)
    assert d["test_cmd_source"] == want_source.replace("WF", str(wf))
    assert kw["provision"] is with_wf


@pytest.mark.parametrize("with_wf", [True, False])
def test_run_sweep_with_nothing_to_detect_is_an_error(tmp_path, monkeypatch, with_wf):
    monkeypatch.setattr(sweep, "sweep", lambda *a, **kw: pytest.fail("must not sweep"))
    wf = _wf(tmp_path / "WORKFLOW.md") if with_wf else None
    root = tmp_path / "r"
    root.mkdir()
    d = sweep.run_sweep(root, workflow_path=wf)
    assert d["ok"] is False
    assert d["error"].startswith("no suite to run — none detected")
    assert sweep.verdict(d) == "error"


@pytest.mark.parametrize("flag, wf_base, origin_head, want", [
    ("x", "wfb", True, "x"),
    (None, "wfb", True, "origin/wfb"),
    (None, None, True, "origin/trunk"),
    (None, None, False, "origin/dev"),
])
def test_run_sweep_base_precedence(tmp_path, monkeypatch, flag, wf_base, origin_head, want):
    root = _bare(tmp_path / "r", {})
    if origin_head:
        _git(root, "update-ref", "refs/remotes/origin/trunk", "main")
        _git(root, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
    wf = _wf(tmp_path / "WORKFLOW.md", base=wf_base) if wf_base else None
    calls = []
    monkeypatch.setattr(sweep, "sweep", lambda *a, **kw: calls.append(a) or sweep.SweepReport())
    sweep.run_sweep(root, base=flag, workflow_path=wf, test_cmd="c")
    assert calls[0][1] == want


# --- 4. the kill path ---------------------------------------------------------------------------

# Instant on the clean tree; hangs while a mutation is on disk, so the test can kill the
# sweep at exactly the moment a mutation is live.
SLOW_SUITE = '''\
import pathlib, sys, time
if "False and" in pathlib.Path("calc.py").read_text():
    pathlib.Path(sys.argv[1]).write_text("mutated")
    time.sleep(120)
'''


def _start_mid_mutation(tmp_path: Path, root: Path) -> subprocess.Popen:
    flag = tmp_path / "flag"
    script = tmp_path / "slow_suite.py"
    script.write_text(SLOW_SUITE)
    proc = subprocess.Popen(
        [*CHELA, "judge", "sweep", "--base", "main", "--test-cmd",
         f'"{sys.executable}" "{script}" "{flag}"'],
        cwd=str(root), env=_env(tmp_path), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, start_new_session=True)
    deadline = time.monotonic() + 60
    while not flag.exists():
        assert proc.poll() is None, proc.communicate()[0]
        assert time.monotonic() < deadline, "the sweep never reached a mutation"
        time.sleep(0.1)
    return proc


def _reap(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM, signal.SIGHUP])
def test_an_interrupted_run_leaves_git_diff_empty(tmp_path, sig):
    root = _bare(tmp_path / "r", {"calc.py": BRANCH_CALC})
    proc = _start_mid_mutation(tmp_path, root)
    assert _git(root, "diff") != ""                   # the mutation really is live
    proc.send_signal(sig)
    out, _ = proc.communicate(timeout=60)
    _reap(proc)
    assert proc.returncode == 2, out
    assert f"interrupted by signal {int(sig)}" in out
    assert _git(root, "diff") == "" and _git(root, "status", "--porcelain") == ""


def test_a_sigkilled_run_leaves_git_diff_empty(tmp_path):
    root = _bare(tmp_path / "r", {"calc.py": BRANCH_CALC})
    proc = _start_mid_mutation(tmp_path, root)
    assert _git(root, "diff") != ""
    os.killpg(proc.pid, signal.SIGKILL)               # the sweep AND its suite, no finally
    proc.wait(timeout=30)
    deadline = time.monotonic() + 30
    while _git(root, "status", "--porcelain") != "":
        assert time.monotonic() < deadline, _git(root, "status", "--porcelain")
        time.sleep(0.1)
    assert _git(root, "diff") == ""


def test_the_guardian_argv_and_popen_kwargs(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(sweep.subprocess, "Popen",
                        lambda argv, **kw: seen.append((argv, kw)) or "proc")
    assert sweep._start_guardian(tmp_path) == "proc"
    assert seen == [([sys.executable, "-m", "chela.sweep", "guard", str(os.getpid()),
                      str(tmp_path)],
                     {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                      "stderr": subprocess.DEVNULL, "start_new_session": True})]


def test_a_guardian_that_cannot_start_is_a_note_and_the_sweep_goes_on(tmp_path, monkeypatch):
    root = _bare(tmp_path / "r", {"calc.py": BRANCH_CALC})

    # A guardian binary that does not exist: Popen raises FileNotFoundError (an OSError).
    monkeypatch.setattr(sweep, "_guardian_argv", lambda wt: [str(tmp_path / "no-such-python")])
    report = sweep.sweep(root, "main", f'"{sys.executable}" -m pytest -q', timeout=120,
                         provision=False)
    assert "the kill guardian could not start — a SIGKILL mid-mutation is only recovered by " \
           "the next sweep" in report.notes
    assert {r.mutation.key for r in report.survivors} == {f"calc.py:{BIG_LINE}"}


def test_the_guardian_is_started_once_and_stopped_even_when_the_sweep_raises(tmp_path,
                                                                             monkeypatch):
    root = _bare(tmp_path / "r", {"calc.py": BRANCH_CALC})
    events = []
    monkeypatch.setattr(sweep, "_start_guardian", lambda wt: events.append(("start", wt)) or "g")
    monkeypatch.setattr(sweep, "_stop_guardian", lambda g: events.append(("stop", g)))

    def boom(*a, **kw):
        raise sweep._Interrupted(15)

    monkeypatch.setattr(sweep, "_run_one", boom)
    report = sweep.sweep(root, "main", f'"{sys.executable}" -m pytest -q', timeout=120,
                         provision=False)
    assert "interrupted by signal 15" in report.cannot_verify
    assert events == [("start", root.resolve()), ("stop", "g")]


def test_stop_guardian_terminates_then_kills_a_stuck_one():
    assert sweep._stop_guardian(None) is None
    calls = []

    class P:
        def __init__(self, stuck):
            self.stuck = stuck

        def terminate(self):
            calls.append("terminate")

        def kill(self):
            calls.append("kill")

        def wait(self, timeout):
            calls.append(("wait", timeout))
            if self.stuck and "kill" not in calls:
                raise subprocess.TimeoutExpired("g", timeout)

    sweep._stop_guardian(P(stuck=False))
    assert calls == ["terminate", ("wait", 5)]
    calls.clear()
    sweep._stop_guardian(P(stuck=True))
    assert calls == ["terminate", ("wait", 5), "kill", ("wait", 5)]


def test_guard_waits_for_the_pid_then_recovers(tmp_path, monkeypatch):
    alive = iter([True, True, False])
    sleeps = []
    monkeypatch.setattr(sweep, "_pid_alive", lambda pid: next(alive))
    monkeypatch.setattr(sweep.time, "sleep", sleeps.append)
    seen = []
    monkeypatch.setattr(sweep, "recover", lambda wt: seen.append(wt) or ("", ""))
    assert sweep.guard(42, tmp_path, poll=0.5) == 0
    assert sleeps == [0.5, 0.5] and seen == [tmp_path]
    monkeypatch.setattr(sweep, "_pid_alive", lambda pid: False)
    monkeypatch.setattr(sweep, "recover", lambda wt: ("", "edited since"))
    assert sweep.guard(42, tmp_path) == 1


def test_the_guard_poll_interval_is_a_fifth_of_a_second():
    assert sweep.GUARD_POLL_SECONDS == 0.2


def test_guard_main_parses_its_argv(tmp_path, monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(sweep, "guard", lambda pid, wt: seen.append((pid, wt)) or 7)
    assert sweep.main(["guard", "123", str(tmp_path)]) == 7
    assert seen == [(123, tmp_path)]
    for bad in (["guard", "x", "/w"], ["guard", "1"], ["other", "1", "/w"], []):
        assert sweep.main(bad) == 2
    assert "usage: python -m chela.sweep guard <pid> <worktree>" in capsys.readouterr().err


def test_the_guardian_module_entry_point_restores_a_dead_sweeps_mutation(tmp_path):
    root = _bare(tmp_path / "r", {"calc.py": BRANCH_CALC})
    dead = subprocess.Popen(["true"])
    dead.wait()
    (root / "calc.py").write_text("mutated\n")
    marker = sweep.marker_path(root)
    marker.write_text(json.dumps({"pid": dead.pid, "file": "calc.py", "original": BRANCH_CALC,
                                  "mutated": "mutated\n"}))
    out = subprocess.run([sys.executable, "-m", "chela.sweep", "guard", str(dead.pid),
                          str(root)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert (root / "calc.py").read_text() == BRANCH_CALC and not marker.exists()


def test_a_zombie_is_dead():
    child = subprocess.Popen(["true"])
    deadline = time.monotonic() + 10
    while not sweep._is_zombie(child.pid):
        assert time.monotonic() < deadline, "the child never became a zombie"
        time.sleep(0.02)
    assert sweep._pid_alive(child.pid) is False
    child.wait()
    assert sweep._is_zombie(child.pid) is False
    assert sweep._is_zombie(os.getpid()) is False
    assert sweep._pid_alive(os.getppid()) is True


def test_a_zombie_we_may_not_signal_is_dead(monkeypatch):
    def denied(pid, sig):
        raise PermissionError

    monkeypatch.setattr(sweep.os, "kill", denied)
    monkeypatch.setattr(sweep, "_is_zombie", lambda pid: True)
    assert sweep._pid_alive(4242) is False
    monkeypatch.setattr(sweep, "_is_zombie", lambda pid: False)
    assert sweep._pid_alive(4242) is True


@pytest.mark.parametrize("stat, zombie", [
    ("123 (a) Z 1 2", True), ("123 (a b) c) Z 1", True), ("123 (a) S 1 2", False),
    ("123 (Z) S 1", False), ("123 (a)", False),
])
def test_is_zombie_reads_the_state_after_the_last_paren(monkeypatch, stat, zombie):
    monkeypatch.setattr(sweep.Path, "read_text", lambda self: stat)
    assert sweep._is_zombie(123) is zombie


def test_sigint_is_handled_too(tmp_path, monkeypatch):
    before = signal.getsignal(signal.SIGINT)
    seen = {}
    monkeypatch.setattr(sweep, "_sweep",
                        lambda *a, **k: seen.update(h=signal.getsignal(signal.SIGINT)) or
                        sweep.SweepReport())
    sweep.sweep(tmp_path, "main", "true")
    assert seen["h"] is not before
    with pytest.raises(sweep._Interrupted):
        seen["h"](signal.SIGINT, None)
    assert signal.getsignal(signal.SIGINT) is before


# --- 5. the CLI: --json, exit codes, --github ----------------------------------------------------


def _cli_main(*argv: str):
    from chela import main

    with patch.object(sys, "argv", ["chela", "judge", "sweep", *argv]):
        try:
            main.main()
        except SystemExit as e:
            return e.code
    return None


def _result(**kw) -> dict:
    d = {"ok": True, "mutations": 2, "survivors": [], "killed": 2, "unparseable": [],
         "not_applied": [], "invalid": [], "cannot_verify": "", "recovered": "", "notes": [],
         "selection": "", "seconds": 1.0, "unsupported": [], "base": "main",
         "test_cmd": "pytest", "test_cmd_source": "--test-cmd"}
    d.update(kw)
    return d


SURVIVOR = {"key": "calc.py:8", "mutation": "if-false: `if x:` → `if False and (x):`"}


@pytest.mark.parametrize("result, code", [
    (_result(), 0),
    (_result(survivors=[SURVIVOR]), 1),
    (_result(cannot_verify="red"), 2),
    ({"ok": False, "error": "no suite"}, 2),
    (_result(unsupported=["a.go"]), 3),
])
def test_json_mode_prints_the_result_and_exits_by_verdict(tmp_path, monkeypatch, capsys,
                                                          result, code):
    monkeypatch.setattr(sweep, "run_sweep", lambda cwd, **kw: result)
    assert (_cli_main("--cwd", str(tmp_path), "--json") or 0) == code
    assert json.loads(capsys.readouterr().out) == result


@pytest.mark.parametrize("result, code, needle", [
    (_result(), None, "sweep: 2 mutation(s) — 2 killed, 0 survived, 0 skipped"),
    (_result(survivors=[SURVIVOR]), 1, "[SURVIVED] calc.py:8"),
    (_result(cannot_verify="red"), 2, "CANNOT VERIFY — red"),
    ({"ok": False, "error": "no suite"}, 2, "sweep: no suite"),
    (_result(unsupported=["a.go"]), 3, "[UNKNOWN ] a.go"),
])
def test_text_mode_exits_by_verdict(tmp_path, monkeypatch, capsys, result, code, needle):
    monkeypatch.setattr(sweep, "run_sweep", lambda cwd, **kw: result)
    assert _cli_main("--cwd", str(tmp_path)) == code
    assert needle in capsys.readouterr().out


@pytest.mark.parametrize("with_wf", [True, False])
def test_the_task_finished_hint_is_shown_only_for_a_chela_managed_run(tmp_path, monkeypatch,
                                                                      capsys, with_wf):
    """Standalone there is no `chela task-finished`, so its --sweep-equivalent escape hatch is
    not offered; with a WORKFLOW.md it is."""
    if with_wf:
        (tmp_path / "WORKFLOW.md").write_text("---\n---\n")
    monkeypatch.setattr(sweep, "run_sweep", lambda cwd, **kw: _result(survivors=[SURVIVOR]))
    assert _cli_main("--cwd", str(tmp_path)) == 1
    out = capsys.readouterr().out
    assert "Add a test that goes RED under that mutation" in out
    assert ("`chela task-finished` accepts --sweep-equivalent" in out) is with_wf


@pytest.mark.parametrize("source, shown", [
    ("detected (go.mod)", True), ("--test-cmd", False), ("WORKFLOW.md judge.test_cmd", False),
])
def test_text_mode_names_the_suite_only_when_it_was_detected(tmp_path, monkeypatch, capsys,
                                                             source, shown):
    monkeypatch.setattr(sweep, "run_sweep",
                        lambda cwd, **kw: _result(test_cmd_source=source, test_cmd="go test"))
    _cli_main("--cwd", str(tmp_path))
    assert ("suite: `go test` — " + source in capsys.readouterr().out) is shown


@pytest.mark.parametrize("extra, want_comment", [((), True), (("--no-comment",), False)])
@pytest.mark.parametrize("json_mode", [True, False])
def test_github_mode_publishes_and_never_changes_the_exit_code(tmp_path, monkeypatch, capsys,
                                                               extra, want_comment, json_mode):
    result = _result(survivors=[SURVIVOR])
    monkeypatch.setattr(sweep, "run_sweep", lambda cwd, **kw: result)
    seen = []
    monkeypatch.setattr(sweep_github, "publish",
                        lambda r, comment: seen.append((r, comment)) or ["gh is gone"])
    argv = ["--cwd", str(tmp_path), "--github", *extra] + (["--json"] if json_mode else [])
    assert _cli_main(*argv) == 1
    assert seen == [(result, want_comment)]
    cap = capsys.readouterr()
    stream = cap.err if json_mode else cap.out
    assert "::warning::chela judge sweep: gh is gone" in stream


def test_without_github_nothing_is_published(tmp_path, monkeypatch):
    monkeypatch.setattr(sweep, "run_sweep", lambda cwd, **kw: _result())
    monkeypatch.setattr(sweep_github, "publish", lambda *a, **k: pytest.fail("published"))
    assert _cli_main("--cwd", str(tmp_path), "--json") == 0


def test_json_progress_goes_to_stderr_and_text_progress_to_stdout(tmp_path, monkeypatch, capsys):
    mut = sweep.Mutation("a.py", 3, "if-false", "if x:", "if False and (x):")

    def fake(cwd, **kw):
        kw["progress"](0, 1, mut)
        return _result()

    monkeypatch.setattr(sweep, "run_sweep", fake)
    _cli_main("--cwd", str(tmp_path), "--json")
    cap = capsys.readouterr()
    assert "[1/1] a.py:3 if-false" in cap.err and "[1/1]" not in cap.out
    _cli_main("--cwd", str(tmp_path))
    cap = capsys.readouterr()
    assert "[1/1] a.py:3 if-false" in cap.out and "[1/1]" not in cap.err


# --- 6. sweep_github: comment, summary, outputs ---------------------------------------------------


@pytest.mark.parametrize("result, headline", [
    (_result(), "### ⚖️🧹 chela judge sweep — ✅ no survivors"),
    (_result(survivors=[SURVIVOR, SURVIVOR]), "### ⚖️🧹 chela judge sweep — ❌ 2 survivor(s)"),
    (_result(unsupported=["a.go"]), "### ⚖️🧹 chela judge sweep — ⚠️ UNKNOWN — changed files "
                                    "in an unsupported language were not swept"),
    (_result(cannot_verify="red"), "### ⚖️🧹 chela judge sweep — ⚠️ cannot verify"),
    ({"ok": False, "error": "no suite"}, "### ⚖️🧹 chela judge sweep — ⚠️ the sweep could not run"),
])
def test_the_comment_headline_per_verdict(result, headline):
    body = sweep_github.render_comment(result)
    lines = body.split("\n")
    assert lines[0] == "<!-- chela-judge-sweep -->"
    assert lines[1] == headline


def test_the_comment_body_for_survivors_and_unknown():
    body = sweep_github.render_comment(_result(
        survivors=[SURVIVOR], killed=1, unsupported=["web/a.ts"],
        unparseable=[{}], not_applied=[{}, {}]))
    assert ("2 mutation(s) of the lines this PR changed vs `main`: 1 killed, 1 survived, "
            "3 skipped · suite `pytest` · 1.0s") in body
    assert "- `calc.py:8` `` if-false: `if x:` → `if False and (x):` ``" in body
    assert ("**Not swept — UNKNOWN, not clean** (only Python (and .js/.mjs, line-based) is "
            "mutated):") in body
    assert "- `web/a.ts`" in body
    assert body.endswith("no LLM, no API key.</sub>\n")


def test_the_comment_for_cannot_verify_and_error():
    assert "**Cannot verify:** the suite is red" in sweep_github.render_comment(
        _result(cannot_verify="the suite\nis red"))
    body = sweep_github.render_comment({"ok": False, "error": "no suite\nat all"})
    assert "`chela judge sweep` could not run: no suite at all" in body
    assert "mutation(s)" not in body


def test_a_clean_comment_lists_nothing():
    body = sweep_github.render_comment(_result())
    assert "survived**" not in body and "Not swept" not in body and "Cannot verify" not in body


def test_long_lists_are_capped():
    many = [{"key": f"a.py:{i}", "mutation": "m"} for i in range(sweep_github.MAX_LISTED + 3)]
    body = sweep_github.render_comment(_result(survivors=many))
    assert body.count("- `a.py:") == 50
    assert "- …and 3 more (see the job log)" in body
    exact = sweep_github.render_comment(_result(survivors=many[:50]))
    assert "more (see the job log)" not in exact


def test_max_listed_is_fifty():
    assert sweep_github.MAX_LISTED == 50


@pytest.mark.parametrize("text, want", [
    ("plain", "`plain`"), ("a`b", "``a`b``"), ("`x`", "`` `x` ``"), ("a``b", "```a``b```"),
    ("one\ntwo", "`one two`"), ("x`", "`` x` ``"),
])
def test_code_spans_survive_backticks_and_newlines(text, want):
    assert sweep_github._code(text) == want


@pytest.mark.parametrize("event, want", [
    ({"pull_request": {"number": 12}}, 12),
    ({"pull_request": {"number": 0}}, None),
    ({"pull_request": {"number": "12"}}, None),
    ({"pull_request": {"number": True}}, None),
    ({"pull_request": None}, None),
    ({"push": {}}, None),
    ([1], None),
])
def test_pr_number(tmp_path, event, want):
    path = tmp_path / "event.json"
    path.write_text(json.dumps(event))
    assert sweep_github.pr_number(str(path)) == want


def test_pr_number_unreadable_or_absent(tmp_path):
    assert sweep_github.pr_number(None) is None
    assert sweep_github.pr_number("") is None
    assert sweep_github.pr_number(str(tmp_path / "missing.json")) is None
    bad = tmp_path / "bad.json"
    bad.write_text("{nope")
    assert sweep_github.pr_number(str(bad)) is None


class FakeGh:
    """A ``subprocess.run`` stand-in that answers ``gh`` calls in order."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append((argv, kw))
        a = self.answers.pop(0)
        if isinstance(a, BaseException):
            raise a
        return subprocess.CompletedProcess(argv, a[0], a[1], a[2] if len(a) > 2 else "")


LIST_ARGV = ["gh", "api", "--paginate", "repos/o/r/issues/7/comments", "--jq",
             '.[] | select(.body | contains("<!-- chela-judge-sweep -->")) | .id']
GH_KW = dict(capture_output=True, text=True, timeout=60)


def test_upsert_edits_the_last_marked_comment_in_place():
    gh = FakeGh((0, "11\n22\n"), (0, "{}"))
    assert sweep_github.upsert_comment("o/r", 7, "BODY", runner=gh) == ""
    assert gh.calls == [
        (LIST_ARGV, {"input": None, **GH_KW}),
        (["gh", "api", "--method", "PATCH", "repos/o/r/issues/comments/22", "--input", "-"],
         {"input": json.dumps({"body": "BODY"}), **GH_KW}),
    ]


def test_upsert_posts_a_new_comment_when_none_is_marked():
    gh = FakeGh((0, ""), (0, "{}"))
    assert sweep_github.upsert_comment("o/r", 7, "BODY", runner=gh) == ""
    assert gh.calls[1] == (
        ["gh", "api", "--method", "POST", "repos/o/r/issues/7/comments", "--input", "-"],
        {"input": json.dumps({"body": "BODY"}), **GH_KW})


def test_upsert_without_post_new_never_creates_one_but_still_edits():
    gh = FakeGh((0, "\n"))
    assert sweep_github.upsert_comment("o/r", 7, "B", post_new=False, runner=gh) == ""
    assert len(gh.calls) == 1
    gh = FakeGh((0, "5\n"), (0, ""))
    assert sweep_github.upsert_comment("o/r", 7, "B", post_new=False, runner=gh) == ""
    assert gh.calls[1][0][3] == "PATCH"


def test_an_unreadable_comment_list_is_an_error_never_a_duplicate_post():
    gh = FakeGh((1, "", "HTTP 403: Resource not accessible by integration"))
    err = sweep_github.upsert_comment("o/r", 7, "B", runner=gh)
    assert err == ("`gh api --paginate repos/o/r/issues/7/comments` exited 1: HTTP 403: "
                   "Resource not accessible by integration")
    assert len(gh.calls) == 1


@pytest.mark.parametrize("exc", [FileNotFoundError("gh"), subprocess.TimeoutExpired("gh", 60)])
def test_gh_that_cannot_run_is_an_error(exc):
    gh = FakeGh(exc)
    assert sweep_github.upsert_comment("o/r", 7, "B", runner=gh).startswith(
        "`gh api` could not run:")


@pytest.mark.parametrize("step", ["PATCH", "POST"])
def test_a_failing_write_is_reported(step):
    gh = FakeGh((0, "9\n" if step == "PATCH" else ""), (1, "", "boom\nlast line"))
    err = sweep_github.upsert_comment("o/r", 7, "B", runner=gh)
    assert err.startswith(f"`gh api --method {step}`") and err.endswith("exited 1: last line")


def _gh_env(tmp_path: Path, number: int | None = 7) -> dict:
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": number}} if number else {}))
    return {"GITHUB_REPOSITORY": "o/r", "GITHUB_EVENT_PATH": str(event),
            "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
            "GITHUB_OUTPUT": str(tmp_path / "output")}


def test_publish_writes_summary_outputs_and_posts(tmp_path):
    env = _gh_env(tmp_path)
    gh = FakeGh((0, ""), (0, ""))
    result = _result(survivors=[SURVIVOR])
    assert sweep_github.publish(result, environ=env, runner=gh) == []
    body = sweep_github.render_comment(result)
    assert (tmp_path / "summary.md").read_text() == body
    assert (tmp_path / "output").read_text() == "verdict=survivors\nsurvivors=1\n"
    assert gh.calls[1][0][3] == "POST" and json.loads(gh.calls[1][1]["input"])["body"] == body


@pytest.mark.parametrize("result, posts_new", [
    (_result(), False), (_result(unsupported=["a.go"]), True),
    (_result(cannot_verify="x"), True), ({"ok": False, "error": "x"}, True),
])
def test_publish_posts_a_new_comment_only_when_not_clean(tmp_path, result, posts_new):
    gh = FakeGh((0, ""), (0, ""))
    assert sweep_github.publish(result, environ=_gh_env(tmp_path), runner=gh) == []
    assert len(gh.calls) == (2 if posts_new else 1)


def test_publish_without_comment_never_calls_gh(tmp_path):
    gh = FakeGh()
    assert sweep_github.publish(_result(), comment=False, environ=_gh_env(tmp_path),
                                runner=gh) == []
    assert gh.calls == []
    assert (tmp_path / "output").read_text() == "verdict=clean\nsurvivors=0\n"


@pytest.mark.parametrize("drop", ["GITHUB_REPOSITORY", "event"])
def test_publish_outside_a_pull_request_warns_and_posts_nothing(tmp_path, drop):
    env = _gh_env(tmp_path, number=None if drop == "event" else 7)
    if drop == "GITHUB_REPOSITORY":
        del env["GITHUB_REPOSITORY"]
    gh = FakeGh()
    assert sweep_github.publish(_result(), environ=env, runner=gh) == [
        "not a pull_request run — no PR comment posted"]
    assert gh.calls == []


def test_publish_reports_a_failed_comment_as_a_warning(tmp_path):
    gh = FakeGh((1, "", "denied"))
    warnings = sweep_github.publish(_result(survivors=[SURVIVOR]), environ=_gh_env(tmp_path),
                                    runner=gh)
    assert warnings == ["the PR comment could not be posted: `gh api --paginate "
                        "repos/o/r/issues/7/comments` exited 1: denied"]


def test_publish_with_unwritable_files_warns(tmp_path):
    env = {"GITHUB_STEP_SUMMARY": str(tmp_path / "no" / "s.md"),
           "GITHUB_OUTPUT": str(tmp_path / "no" / "o")}
    warnings = sweep_github.publish(_result(), comment=False, environ=env, runner=FakeGh())
    assert [w.split(":")[0] for w in warnings] == [
        "the step summary could not be written", "the step output could not be written"]


def test_publish_with_no_github_files_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert sweep_github.publish(_result(), comment=False, environ={}, runner=FakeGh()) == []
    assert list(tmp_path.iterdir()) == []


def test_publish_reads_os_environ_by_default(tmp_path, monkeypatch):
    for k, v in _gh_env(tmp_path).items():
        monkeypatch.setenv(k, v)
    sweep_github.publish(_result(), comment=False, runner=FakeGh())
    assert (tmp_path / "output").read_text() == "verdict=clean\nsurvivors=0\n"


# --- 7. the action: action.yml + its run script ----------------------------------------------------


def test_action_yml_is_a_composite_that_installs_chela_and_runs_the_script():
    action = yaml.safe_load(ACTION_YML.read_text())
    assert action["runs"]["using"] == "composite"
    install, run = action["runs"]["steps"]
    assert '-m pip install --quiet --disable-pip-version-check "$GITHUB_ACTION_PATH"' \
        in install["run"]
    assert 'echo "CHELA_SWEEP_BIN=$venv/bin/chela" >> "$GITHUB_ENV"' in install["run"]
    assert run["id"] == "sweep"
    assert run["run"] == 'bash "$GITHUB_ACTION_PATH/scripts/judge-sweep-action.sh"'
    assert run["env"] == {
        "GH_TOKEN": "${{ inputs.github-token }}", "INPUT_BASE": "${{ inputs.base }}",
        "INPUT_TEST_CMD": "${{ inputs.test-cmd }}", "INPUT_COMMENT": "${{ inputs.comment }}",
        "PR_BASE_SHA": "${{ github.event.pull_request.base.sha }}"}
    assert action["inputs"]["github-token"]["default"] == "${{ github.token }}"
    assert action["inputs"]["comment"]["default"] == "true"
    assert action["outputs"]["verdict"]["value"] == "${{ steps.sweep.outputs.verdict }}"
    assert action["outputs"]["survivors"]["value"] == "${{ steps.sweep.outputs.survivors }}"
    # ⛔ No input is ever interpolated into a shell script (script injection): env only.
    for step in action["runs"]["steps"]:
        assert "${{" not in step["run"], step["name"]
    # And no Claude / API key anywhere: GITHUB_TOKEN is all it needs.
    assert "ANTHROPIC" not in ACTION_YML.read_text() and "CLAUDE" not in ACTION_YML.read_text()


FAKE_CHELA = '''#!/usr/bin/env bash
printf '%s\\n' "$@" > "$ARGV_LOG"
exit "${FAKE_EXIT:-0}"
'''


def _run_action(tmp_path: Path, repo: Path, **env: str) -> subprocess.CompletedProcess:
    fake = tmp_path / "fake-chela"
    if not fake.exists():
        fake.write_text(FAKE_CHELA)
        fake.chmod(0o755)
    full = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "ARGV_LOG": str(tmp_path / "argv"),
            "CHELA_SWEEP_BIN": str(fake), **env}
    return subprocess.run(["bash", str(ACTION_SH)], cwd=str(repo), env=full,
                          capture_output=True, text=True, timeout=60)


def _argv(tmp_path: Path) -> list[str]:
    return (tmp_path / "argv").read_text().splitlines()


@pytest.mark.parametrize("code", [0, 1, 2, 3])
def test_the_action_script_exits_with_the_sweeps_own_code(tmp_path, code):
    repo = _bare(tmp_path / "r", {})
    out = _run_action(tmp_path, repo, PR_BASE_SHA="main", FAKE_EXIT=str(code))
    assert out.returncode == code, out.stderr


@pytest.mark.parametrize("env, want", [
    ({"PR_BASE_SHA": "main"},
     ["judge", "sweep", "--base", "main", "--github"]),
    ({"PR_BASE_SHA": "main", "INPUT_BASE": "feature~0"},
     ["judge", "sweep", "--base", "feature~0", "--github"]),
    ({"PR_BASE_SHA": "main", "INPUT_BASE": ""},
     ["judge", "sweep", "--base", "main", "--github"]),
    ({"PR_BASE_SHA": "main", "INPUT_TEST_CMD": "make test; echo $HOME"},
     ["judge", "sweep", "--base", "main", "--github", "--test-cmd", "make test; echo $HOME"]),
    ({"PR_BASE_SHA": "main", "INPUT_COMMENT": "false"},
     ["judge", "sweep", "--base", "main", "--github", "--no-comment"]),
    ({"PR_BASE_SHA": "main", "INPUT_COMMENT": "true"},
     ["judge", "sweep", "--base", "main", "--github"]),
])
def test_the_action_script_argv(tmp_path, env, want):
    repo = _bare(tmp_path / "r", {})
    out = _run_action(tmp_path, repo, **env)
    assert out.returncode == 0, out.stderr
    assert _argv(tmp_path) == want


def test_the_action_script_refuses_without_a_base(tmp_path):
    repo = _bare(tmp_path / "r", {})
    out = _run_action(tmp_path, repo)
    assert out.returncode == 2
    assert "::error::chela judge sweep: no base to diff against" in out.stdout
    assert not (tmp_path / "argv").exists()


def test_the_action_script_fetches_a_missing_base_and_deepens_a_shallow_clone(tmp_path):
    upstream = _bare(tmp_path / "up", {"calc.py": BRANCH_CALC})
    base_sha = _git(upstream, "rev-parse", "main")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", "--depth", "1", "--branch", "feature",
                    f"file://{upstream}", str(clone)], check=True, capture_output=True)
    assert _git(clone, "rev-parse", "--is-shallow-repository") == "true"
    out = _run_action(tmp_path, clone, PR_BASE_SHA=base_sha)
    assert out.returncode == 0, out.stderr
    assert _git(clone, "rev-parse", "--is-shallow-repository") == "false"
    assert _git(clone, "merge-base", base_sha, "HEAD") == base_sha


def test_the_action_script_fetches_a_base_the_clone_does_not_have(tmp_path):
    upstream = _bare(tmp_path / "up", {"calc.py": BRANCH_CALC})
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", f"file://{upstream}", str(clone)], check=True,
                   capture_output=True)
    _git(upstream, "checkout", "-qb", "later", "main")
    (upstream / "later.py").write_text("x = 1\n")
    _git(upstream, "add", "-A")
    _git(upstream, "commit", "-qm", "later")
    later = _git(upstream, "rev-parse", "HEAD")
    out = _run_action(tmp_path, clone, PR_BASE_SHA=later)
    assert out.returncode == 0, out.stderr
    assert _git(clone, "cat-file", "-t", later) == "commit"


def test_the_action_end_to_end_comments_on_survivors_and_fails_the_job(tmp_path):
    """The real wiring: the action's script → the real `chela judge sweep --github` → a fake
    `gh` on PATH. Survivors ⇒ exit 1, a POSTed comment naming them, verdict output."""
    repo = _bare(tmp_path / "r", {"calc.py": BRANCH_CALC})
    bindir = tmp_path / "bin"
    bindir.mkdir()
    gh_log = tmp_path / "gh.jsonl"
    (bindir / "gh").write_text(
        f"#!{sys.executable}\nimport json, sys\n"
        f"open({str(gh_log)!r}, 'a').write(json.dumps({{'argv': sys.argv[1:], "
        "'stdin': sys.stdin.read()}) + '\\n')\n")
    (bindir / "gh").chmod(0o755)
    chela_bin = bindir / "chela"
    chela_bin.write_text(f'#!/usr/bin/env bash\nexec "{sys.executable}" -c '
                         '"import sys; from chela.main import main; sys.argv[0]=\'chela\'; '
                         'main()" "$@"\n')
    chela_bin.chmod(0o755)
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": 7}}))
    env = _env(tmp_path, PATH=f"{bindir}:{PY_BIN}:{os.environ['PATH']}",
               CHELA_SWEEP_BIN=str(chela_bin), PR_BASE_SHA="main", INPUT_TEST_CMD="",
               INPUT_COMMENT="true", GITHUB_REPOSITORY="o/r", GITHUB_EVENT_PATH=str(event),
               GITHUB_OUTPUT=str(tmp_path / "output"),
               GITHUB_STEP_SUMMARY=str(tmp_path / "summary.md"))
    out = subprocess.run(["bash", str(ACTION_SH)], cwd=str(repo), env=env, capture_output=True,
                         text=True, timeout=300)
    assert out.returncode == 1, out.stdout + out.stderr
    calls = [json.loads(line) for line in gh_log.read_text().splitlines()]
    assert [c["argv"][:4] for c in calls] == [
        ["api", "--paginate", "repos/o/r/issues/7/comments", "--jq"],
        ["api", "--method", "POST", "repos/o/r/issues/7/comments"]]
    body = json.loads(calls[1]["stdin"])["body"]
    assert body.startswith("<!-- chela-judge-sweep -->\n### ⚖️🧹 chela judge sweep — ❌")
    assert f"`calc.py:{BIG_LINE}`" in body
    assert "verdict=survivors\n" in (tmp_path / "output").read_text()
    assert (tmp_path / "summary.md").read_text() == body
    assert _git(repo, "status", "--porcelain") == ""


def test_readme_documents_the_action_snippet():
    readme = (REPO_ROOT / "README.md").read_text()
    assert "uses: Devail1/chelamux@main" in readme
    assert "chela judge sweep --base origin/main" in readme
