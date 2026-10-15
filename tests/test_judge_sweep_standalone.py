"""⚖️🧹 CMX-97 — ``chela judge sweep`` in ANY git repo: a throwaway ``git init``, no chela config."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from chela import sweep

CALC = "def clamp(x, lo):\n    if x < lo:\n        return lo\n    return x\n"
BIG = CALC + '\n\ndef is_big(x):\n    if x > 100:\n        return "big"\n    return "small"\n'
TEST = "from calc import clamp\n\n\ndef test_clamp():\n    assert clamp(-1, 0) == 0\n"
COVERING = TEST + '\n\ndef test_big():\n    from calc import is_big\n    assert is_big(101) == "big"\n' \
                  '    assert is_big(100) == "small"\n'
CHELA = [sys.executable, "-c", "import sys; from chela.main import main; main()"]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                           *args], check=True, capture_output=True, text=True).stdout.strip()


def _bare(root: Path, branch: dict[str, str], test_body: str = TEST) -> Path:
    """``main`` holds calc.py + a test; branch ``feature`` commits ``branch`` on top."""
    (root / "tests").mkdir(parents=True)
    (root / "calc.py").write_text(CALC)
    (root / "tests" / "test_calc.py").write_text(TEST)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    _git(root, "checkout", "-qb", "feature")
    (root / "tests" / "test_calc.py").write_text(test_body)
    for rel, body in branch.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "feature")
    return root


def _env(tmp_path: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CHELA_", "PYTEST_"))}
    (tmp_path / "home").mkdir(exist_ok=True)
    env.update(HOME=str(tmp_path / "home"), PATH=f"{Path(sys.executable).parent}:{env['PATH']}")
    return env


def _chela(root: Path, tmp_path: Path, *args: str) -> tuple[int, dict]:
    out = subprocess.run([*CHELA, "judge", "sweep", "--base", "main", "--json", *args],
                         cwd=str(root), env=_env(tmp_path), capture_output=True, text=True,
                         timeout=300)
    return out.returncode, json.loads(out.stdout)


@pytest.mark.parametrize("branch, test_body, code, verdict, unsupported", [
    ({"calc.py": BIG}, TEST, 1, "survivors", []),
    ({"calc.py": BIG}, COVERING, 0, "clean", []),
    ({"big.go": "package main\n"}, TEST, 3, "unknown", ["big.go"]),
    ({"calc.py": BIG, "web/app.ts": "export const a = 1;\n"}, COVERING, 3, "unknown",
     ["web/app.ts"]),
    ({"calc.py": BIG, "lib.rs": "fn a() {}\n"}, TEST, 1, "survivors", ["lib.rs"]),
])
def test_a_bare_repo_with_no_chela_config(tmp_path, branch, test_body, code, verdict,
                                          unsupported):
    root = _bare(tmp_path / "r", branch, test_body)
    got, d = _chela(root, tmp_path)
    assert (got, d["verdict"], d["unsupported"]) == (code, verdict, unsupported)
    assert d["test_cmd_source"] == "detected (tests/)"
    assert d["base"] == "main" and d["test_cmd"].endswith(" -m pytest -q")
    assert any("NOT swept" in n for n in d["notes"]) is bool(unsupported)
    assert _git(root, "status", "--porcelain") == "" and not (root / ".venv").exists()


@pytest.mark.parametrize("branch, test_body, code, banner", [
    ({"big.go": "package main\n"}, TEST, 3, "⚠ UNKNOWN"),
    ({"calc.py": BIG, "big.go": "package main\n"}, TEST, 1, "⚖️ Each SURVIVED"),
    ({"calc.py": BIG}, COVERING, 0, None),
])
def test_text_mode_exit_and_banner_match_the_json_verdict(tmp_path, branch, test_body, code,
                                                         banner):
    """Text mode exits on the same verdict as --json: survivors outrank UNKNOWN."""
    root = _bare(tmp_path / "r", branch, test_body)
    out = subprocess.run([*CHELA, "judge", "sweep", "--base", "main"], cwd=str(root),
                         env=_env(tmp_path), capture_output=True, text=True, timeout=300)
    assert out.returncode == code, out.stdout + out.stderr
    assert "suite: `python -m pytest -q` — detected (tests/)" in out.stdout
    assert ("[UNKNOWN ] big.go — not supported" in out.stdout) is ("big.go" in branch)
    for b in ("⚠ UNKNOWN", "⚖️ Each SURVIVED"):
        assert (b in out.stdout) is (b == banner), out.stdout


@pytest.mark.parametrize("args, code, verdict, budget", [
    (("--max-mutations", "1"), 2, "cannot_verify", {"seconds": 900.0, "max_mutations": 1}),
    (("--budget-seconds", "77"), 0, "clean", {"seconds": 77.0, "max_mutations": 60}),
    ((), 0, "clean", {"seconds": 900.0, "max_mutations": 60}),
])
def test_the_cmx111_budget_applies_in_standalone_mode(tmp_path, args, code, verdict, budget):
    """🔴 GUARD: with no WORKFLOW.md the budget flags still reach the sweep, and a sweep the
    budget cut short is CANNOT VERIFY (exit 2) in BOTH output modes — never a clean pass."""
    root = _bare(tmp_path / "r", {"calc.py": BIG}, COVERING)
    got, d = _chela(root, tmp_path, *args)
    assert (got, d["verdict"], d["budget"]) == (code, verdict, budget)
    assert bool(d["unrun"]) is (verdict == "cannot_verify") and d["measured"] >= 1
    out = subprocess.run([*CHELA, "judge", "sweep", "--base", "main", *args], cwd=str(root),
                         env=_env(tmp_path), capture_output=True, text=True, timeout=300)
    assert out.returncode == code, out.stdout + out.stderr
    assert ("mutation(s) never ran" in out.stdout) is (verdict == "cannot_verify")
    assert _git(root, "status", "--porcelain") == ""


@pytest.mark.parametrize("rel, unsupported", [
    ("a.go", True), ("src/A.java", True), ("x.TS", True), ("a.cjs", True), ("a.py", False),
    ("a.js", False), ("README.md", False), ("a_test.go", False), ("FooTest.java", False),
    ("src/a.spec.ts", False), ("__tests__/a.ts", False), ("spec/a.rb", False),
    ("inspect/a.go", True), ("latest.ts", True),
])
def test_unsupported_classification(rel, unsupported):
    assert sweep._unsupported(rel) is unsupported


def test_unsupported_files_is_exactly_the_live_foreign_source_vs_the_merge_base(tmp_path):
    """Committed, uncommitted and UNTRACKED foreign files are UNKNOWN; a deleted one (committed
    or only in the working tree), the old side of a rename, an ignored file and a foreign test
    file are not. The exact set is asserted, so dropping any source or filter goes red."""
    root = _bare(tmp_path / "r", {"calc.py": BIG})
    _git(root, "checkout", "-q", "main")
    for rel in ("gone.go", "wt_gone.rs", "old.java", "edited.go", "same.go"):
        (root / rel).write_text("x\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "foreign base")
    _git(root, "checkout", "-q", "feature")
    _git(root, "merge", "-q", "--no-edit", "main")
    _git(root, "rm", "-q", "gone.go")
    _git(root, "mv", "old.java", "New.java")
    (root / "committed.ts").write_text("x\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "branch")
    (root / "wt_gone.rs").unlink()
    (root / "edited.go").write_text("y\n")
    (root / ".gitignore").write_text("ignored.go\n")
    for rel in ("untracked.kt", "ignored.go", "untracked_test.go"):
        (root / rel).write_text("x\n")
    files, err = sweep.unsupported_files(root, "main")
    assert (files, err) == (["New.java", "committed.ts", "edited.go", "untracked.kt"], "")


@pytest.mark.parametrize("failing", ["merge-base", "diff", "ls-files"])
def test_a_failing_git_read_is_cannot_verify_never_no_unsupported_files(tmp_path, monkeypatch,
                                                                        failing):
    def fake(wt, *args):
        ok = args[0] != failing
        return subprocess.CompletedProcess(args, 0 if ok else 128,
                                           "abc\n" if args[0] == "merge-base" else "x.go\0", "")
    monkeypatch.setattr(sweep, "_git", fake)
    files, err = sweep.unsupported_files(tmp_path, "main")
    assert files == [] and err
    monkeypatch.undo()
    root = _bare(tmp_path / "r", {"calc.py": BIG})
    monkeypatch.setattr(sweep, "unsupported_files", lambda wt, base: ([], "git exploded"))
    report = sweep.sweep(root, "main", "true", timeout=60, provision=False)
    assert report.cannot_verify == "git exploded"


@pytest.mark.parametrize("result, want", [
    ({"ok": False}, "error"),
    ({"ok": True, "cannot_verify": "x", "survivors": [1], "unsupported": ["a.go"]},
     "cannot_verify"),
    ({"ok": True, "survivors": [1], "unsupported": ["a.go"]}, "survivors"),
    ({"ok": True, "survivors": [1], "unrun": [1], "unsupported": ["a.go"]}, "survivors"),
    ({"ok": True, "survivors": [], "unrun": [1], "unsupported": ["a.go"]}, "cannot_verify"),
    ({"ok": True, "survivors": [], "unrun": [1], "unsupported": []}, "cannot_verify"),
    ({"ok": True, "survivors": [], "unrun": [], "unsupported": ["a.go"]}, "unknown"),
    ({"ok": True, "survivors": [], "unsupported": []}, "clean"),
])
def test_verdict_precedence_and_exit_codes(result, want):
    assert sweep.verdict(result) == want
    assert sweep.EXIT_CODES[want] == {"clean": 0, "survivors": 1, "cannot_verify": 2,
                                      "error": 2, "unknown": 3}[want]


@pytest.mark.parametrize("files, have, want", [
    ({"pyproject.toml": ""}, {"python"}, ("python -m pytest -q", "pyproject.toml")),
    ({"tests/test_a.py": ""}, set(), ("python3 -m pytest -q", "tests/")),
    ({"tests/helper.py": ""}, {"python"}, ("", "")),
    ({"pytest.ini": "", "uv.lock": ""}, {"uv", "python"}, ("uv run pytest -q", "pytest.ini + uv.lock")),
    ({"package.json": '{"scripts": {"test": "jest"}}'}, set(), ("npm test", "package.json scripts.test")),
    ({"package.json": '{"scripts": {"test": "echo \\"Error: no test specified\\""}}'}, set(), ("", "")),
    ({"package.json": "{not json", "go.mod": ""}, set(), ("go test ./...", "go.mod")),
    ({"Cargo.toml": ""}, set(), ("cargo test", "Cargo.toml")),
    ({}, {"python"}, ("", "")),
])
def test_detect_test_cmd(tmp_path, monkeypatch, files, have, want):
    for rel, body in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(body)
    monkeypatch.setattr(sweep.shutil, "which", lambda n: f"/bin/{n}" if n in have else None)
    assert sweep.detect_test_cmd(tmp_path) == want


_ALL_TOOLS = {"uv", "python", "python3", "npm", "go", "cargo"}
_PJ = '{"scripts": {"test": "jest"}}'


@pytest.mark.parametrize("files, have, want", [
    # uv: BOTH uv.lock AND uv on PATH — either alone is plain pytest
    ({"pyproject.toml": "", "uv.lock": ""}, {"uv"}, ("uv run pytest -q", "pyproject.toml + uv.lock")),
    ({"pyproject.toml": "", "uv.lock": ""}, {"python"}, ("python -m pytest -q", "pyproject.toml")),
    ({"pyproject.toml": "", "uv.lock": ""}, set(), ("python3 -m pytest -q", "pyproject.toml")),
    ({"pyproject.toml": ""}, {"uv", "python"}, ("python -m pytest -q", "pyproject.toml")),
    ({"pyproject.toml": "", "uv.lock/x": ""}, {"uv", "python"}, ("python -m pytest -q", "pyproject.toml")),
    # every python marker counts on its own, and only as a FILE
    *[({m: ""}, {"python"}, ("python -m pytest -q", m)) for m in
      ("pytest.ini", "pyproject.toml", "setup.py", "setup.cfg", "tox.ini", "conftest.py")],
    ({"pyproject.toml/x": ""}, {"python"}, ("", "")),
    # marker order: the first of _PY_MARKERS present is the one named
    ({"conftest.py": "", "setup.cfg": "", "pytest.ini": ""}, {"python"},
     ("python -m pytest -q", "pytest.ini")),
    ({"tox.ini": "", "setup.py": ""}, {"python"}, ("python -m pytest -q", "setup.py")),
    # a tests/ or test/ dir counts only when it holds a test_*.py (nested too); tests/ first
    ({"test/test_a.py": ""}, {"python"}, ("python -m pytest -q", "test/")),
    ({"tests/sub/test_a.py": ""}, {"python"}, ("python -m pytest -q", "tests/")),
    ({"tests/test_a.py": "", "test/test_b.py": ""}, {"python"}, ("python -m pytest -q", "tests/")),
    ({"src/test_a.py": ""}, _ALL_TOOLS, ("", "")),
    ({"setup.cfg": "", "tests/test_a.py": ""}, {"python"}, ("python -m pytest -q", "setup.cfg")),
    # precedence python > npm > go > cargo, whatever binaries exist
    ({"pyproject.toml": "", "package.json": _PJ, "go.mod": "", "Cargo.toml": ""}, _ALL_TOOLS - {"uv"},
     ("python -m pytest -q", "pyproject.toml")),
    ({"package.json": _PJ, "go.mod": "", "Cargo.toml": ""}, _ALL_TOOLS,
     ("npm test", "package.json scripts.test")),
    ({"go.mod": "", "Cargo.toml": ""}, _ALL_TOOLS, ("go test ./...", "go.mod")),
    ({"Cargo.toml": ""}, _ALL_TOOLS, ("cargo test", "Cargo.toml")),
    ({"go.mod": ""}, set(), ("go test ./...", "go.mod")),
    ({"package.json": _PJ}, _ALL_TOOLS, ("npm test", "package.json scripts.test")),
    # npm only for a real scripts.test string; anything else falls through to the next marker
    *[({"package.json": body, "Cargo.toml": ""}, _ALL_TOOLS, ("cargo test", "Cargo.toml")) for body in (
        '{"scripts": {"test": ""}}', '{"scripts": {"test": "   "}}', '{"scripts": {"test": 1}}',
        '{"scripts": {"build": "x"}}', '{"scripts": ["test"]}', '{"name": "x"}', '["scripts"]',
        '{"scripts": {"test": "echo \\"Error: no test specified\\" && exit 1"}}', "{bad")],
    ({"package.json": '{"scripts": {"test": ""}}'}, _ALL_TOOLS, ("", "")),
    ({"package.json/x": "", "go.mod/x": "", "Cargo.toml/x": ""}, _ALL_TOOLS, ("", "")),
])
def test_detect_test_cmd_pins_every_branch(tmp_path, monkeypatch, files, have, want):
    """🔴 GUARD: every suite-detection condition — uv.lock AND uv, each marker, file-not-dir,
    marker order, the tests/ dir glob, python vs python3, npm's scripts.test rules, and the
    python > npm > go > cargo precedence — is pinned, so flipping any one goes red."""
    for rel, body in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(body)
    monkeypatch.setattr(sweep.shutil, "which", lambda n: f"/bin/{n}" if n in have else None)
    assert sweep.detect_test_cmd(tmp_path) == want


@pytest.mark.parametrize("flag, wf_cmd, want_cmd", [
    ("flag-cmd", "wf-cmd", "flag-cmd"), (None, "wf-cmd", "wf-cmd"),
    (None, None, "python -m pytest -q"),
])
@pytest.mark.parametrize("with_wf", [True, False])
def test_suite_precedence_and_provisioning(tmp_path, monkeypatch, flag, wf_cmd, want_cmd,
                                           with_wf):
    """--test-cmd > the workflow's judge.test_cmd > detected; only a chela-managed run (one
    with a workflow) lets the judge provision the env."""
    if wf_cmd and not with_wf:
        pytest.skip("a workflow command needs a workflow")
    (tmp_path / "pytest.ini").write_text("")
    monkeypatch.setattr(sweep.shutil, "which", lambda n: "/bin/python")
    wf = None
    if with_wf:
        wf = tmp_path / "WORKFLOW.md"
        judge = f"judge:\n  test_cmd: {wf_cmd}\n" if wf_cmd else ""
        wf.write_text(f"---\nproject_key: TEST\n{judge}---\n")
    calls = []
    monkeypatch.setattr(sweep, "sweep", lambda *a, **kw: calls.append((a, kw)) or
                        sweep.SweepReport())
    sweep.run_sweep(tmp_path, base="main", workflow_path=wf, test_cmd=flag)
    assert calls[0][0] == (tmp_path, "main", want_cmd) and calls[0][1]["provision"] is with_wf


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGHUP])
def test_an_interrupted_run_leaves_git_diff_empty(tmp_path, sig):
    root = _bare(tmp_path / "r", {"calc.py": BIG})
    flag, script = tmp_path / "flag", tmp_path / "slow_suite.py"
    script.write_text('import pathlib, sys, time\nif "False and" in pathlib.Path("calc.py")'
                      '.read_text():\n    pathlib.Path(sys.argv[1]).write_text("x")\n'
                      '    time.sleep(120)\n')
    proc = subprocess.Popen([*CHELA, "judge", "sweep", "--base", "main", "--test-cmd",
                             f'"{sys.executable}" "{script}" "{flag}"'], cwd=str(root),
                            env=_env(tmp_path), stdout=subprocess.PIPE, text=True)
    deadline = time.monotonic() + 60
    while not flag.exists():
        assert proc.poll() is None and time.monotonic() < deadline
        time.sleep(0.1)
    assert _git(root, "diff") != ""                   # the mutation really is live
    proc.send_signal(sig)
    out, _ = proc.communicate(timeout=60)
    assert proc.returncode == 2 and f"interrupted by signal {int(sig)}" in out
    assert _git(root, "diff") == "" and _git(root, "status", "--porcelain") == ""
