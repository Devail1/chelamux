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
    ({"calc.py": BIG, "web/app.jsx": "export const a = 1;\n"}, COVERING, 3, "unknown",
     ["web/app.jsx"]),
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
    ("inspect/a.go", True), ("latest.rs", True),
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
    (root / "committed.cts").write_text("x\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "branch")
    (root / "wt_gone.rs").unlink()
    (root / "edited.go").write_text("y\n")
    (root / ".gitignore").write_text("ignored.go\n")
    for rel in ("untracked.kt", "ignored.go", "untracked_test.go"):
        (root / rel).write_text("x\n")
    files, err = sweep.unsupported_files(root, "main")
    assert (files, err) == (["New.java", "committed.cts", "edited.go", "untracked.kt"], "")


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


@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM, signal.SIGHUP])
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


# --- CMX-97 slice B: the kill guardian ---------------------------------------------------------


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
    root = _bare(tmp_path / "r", {"calc.py": BIG})
    # A guardian binary that does not exist: Popen raises FileNotFoundError (an OSError).
    monkeypatch.setattr(sweep, "_guardian_argv", lambda wt: [str(tmp_path / "no-such-python")])
    report = sweep.sweep(root, "main", f'"{sys.executable}" -m pytest -q', timeout=120,
                         provision=False)
    assert "the kill guardian could not start — a SIGKILL mid-mutation is only recovered by " \
           "the next sweep" in report.notes
    assert report.results and not report.cannot_verify


def test_the_guardian_is_started_once_and_stopped_even_when_the_sweep_raises(tmp_path,
                                                                             monkeypatch):
    root = _bare(tmp_path / "r", {"calc.py": BIG})
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
    assert not any("kill guardian" in n for n in report.notes)


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
    for bad in (["guard", "x", "/w"], ["guard", "1"], ["other", "1", "/w"], [],
                ["guard", "1", "/w", "extra"]):
        assert sweep.main(bad) == 2
    assert seen == [(123, tmp_path)]
    assert "usage: python -m chela.sweep guard <pid> <worktree>" in capsys.readouterr().err


def test_the_guardian_module_entry_point_restores_a_dead_sweeps_mutation(tmp_path):
    root = _bare(tmp_path / "r", {"calc.py": BIG})
    dead = subprocess.Popen(["true"])
    dead.wait()
    (root / "calc.py").write_text("mutated\n")
    marker = sweep.marker_path(root)
    marker.write_text(json.dumps({"pid": dead.pid, "file": "calc.py", "original": BIG,
                                  "mutated": "mutated\n"}))
    out = subprocess.run([sys.executable, "-m", "chela.sweep", "guard", str(dead.pid),
                          str(root)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert (root / "calc.py").read_text() == BIG and not marker.exists()


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


@pytest.mark.parametrize("exc", [FileNotFoundError, PermissionError,
                                 UnicodeDecodeError("utf-8", b"", 0, 1, "x")])
def test_an_unreadable_stat_is_not_a_zombie(monkeypatch, exc):
    def boom(self):
        raise exc

    monkeypatch.setattr(sweep.Path, "read_text", boom)
    assert sweep._is_zombie(123) is False


# --- CMX-97 category pass: every gate / filter / wiring point is pinned ----------------------


@pytest.mark.parametrize("provision", [True, False])
def test_provisioning_runs_iff_provision_is_true(tmp_path, monkeypatch, provision):
    """🔴 GUARD (wiring): a standalone sweep (provision=False) NEVER provisions the repo's env;
    a chela-managed one does, and a provisioning failure is CANNOT VERIFY."""
    root = _bare(tmp_path / "r", {"calc.py": BIG})
    calls = []
    monkeypatch.setattr(sweep.judge, "provision_suite_env",
                        lambda wt: calls.append(Path(wt)) or "boom")
    report = sweep.sweep(root, "main", "true", timeout=60, provision=provision)
    assert bool(calls) is provision
    if provision:
        assert "PROVISIONED" in report.cannot_verify and "boom" in report.cannot_verify
    else:
        assert report.cannot_verify == "" and report.results


def test_provision_defaults_to_true(tmp_path, monkeypatch):
    root = _bare(tmp_path / "r", {"calc.py": BIG})
    calls = []
    monkeypatch.setattr(sweep.judge, "provision_suite_env",
                        lambda wt: calls.append(wt) or "boom")
    sweep.sweep(root, "main", "true", timeout=60)
    assert len(calls) == 1


def test_provisioning_is_skipped_when_there_is_nothing_to_sweep(tmp_path, monkeypatch):
    root = _bare(tmp_path / "r", {"README.md": "hi\n"})
    monkeypatch.setattr(sweep.judge, "provision_suite_env",
                        lambda wt: pytest.fail("provisioned with no mutation"))
    report = sweep.sweep(root, "main", "true", timeout=60, provision=True)
    assert report.results == [] and any("nothing to sweep" in n for n in report.notes)


@pytest.mark.parametrize("rel, unsupported", [
    # each test-file alternative on its own (is_test_path does NOT match these)
    ("x/a_spec.rb", False), ("x/a_spec.go", False), ("x/a_test.go", False),
    ("x/FooTest.java", False), ("x/FooTests.cs", False), ("x/a.spec.ts", False),
    ("x/__tests__/a.ts", False), ("x/tests/a.go", False), ("x/test/a.java", False),
    ("x/spec/a.rb", False), ("tests/a.go", False), ("test/a.go", False), ("spec/a.rb", False),
    ("__tests__/a.ts", False),
    # is_test_path's own patterns (the foreign regex does NOT match these)
    ("x/test_a.go", False), ("x/a.test.go", False),
    # near misses stay UNKNOWN: the dir must be a whole path segment, the suffix a whole token
    ("xtests/a.go", True), ("x/mytest/a.go", True), ("x/specs/a.rb", True),
    ("x/contest.go", True), ("x/a_testing.go", True), ("x/spec.go", True),
    ("x/aspec.rb", True), ("x/Testing.java", True),
])
def test_foreign_test_file_patterns_are_never_unknown_source(rel, unsupported):
    """Deterministic (pure-function) pin of each foreign test pattern — no git, no order."""
    assert sweep._unsupported(rel) is unsupported


@pytest.mark.parametrize("suffix", [
    ".mts", ".cts", ".jsx", ".cjs", ".go", ".rs", ".java", ".kt", ".kts",
    ".scala", ".rb", ".php", ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".cs", ".swift",
    ".m", ".mm", ".lua", ".ex", ".exs", ".erl", ".hs", ".ml", ".clj", ".dart", ".sh",
    ".bash"])
def test_every_unsupported_suffix_is_unknown(suffix):
    """A LITERAL list (not derived from the set under test) — dropping a suffix goes red."""
    assert suffix in sweep.UNSUPPORTED_SUFFIXES and sweep._unsupported(f"src/thing{suffix}")
    assert sweep._unsupported(f"src/thing{suffix}") is True


def test_a_foreign_spec_file_in_a_real_repo_is_not_unknown(tmp_path):
    root = _bare(tmp_path / "r", {"calc.py": BIG, "x/a_spec.rb": "x\n", "y/b.spec.ts": "x\n",
                                  "z/c_test.go": "x\n", "real.go": "x\n"})
    assert sweep.unsupported_files(root, "main") == (["real.go"], "")


def _fake_result(**over) -> dict:
    d = {"ok": True, "mutations": 1, "killed": 1, "survivors": [], "unrun": [], "notes": [],
         "unparseable": [], "not_applied": [], "invalid": [], "unsupported": [],
         "cannot_verify": "", "recovered": "", "seconds": 0.1, "test_cmd": "t",
         "test_cmd_source": "--test-cmd"}
    d.update(over)
    return d


def _run_cli(monkeypatch, capsys, result, *argv, cwd=None, progress=False):
    from chela import main
    seen = {}

    def fake(cwd_, **kw):
        seen.update(cwd=cwd_, **kw)
        if progress:
            kw["progress"](0, 2, sweep.Mutation("a.py", 3, "flip", "a", "b"))
        return result

    monkeypatch.setattr(sweep, "run_sweep", fake)
    monkeypatch.setattr(sys, "argv", ["chela", "judge", "sweep", "--cwd", str(cwd or "."), *argv])
    code = 0
    try:
        main.main()
    except SystemExit as e:
        code = e.code or 0
    out = capsys.readouterr()
    return code, out.out, out.err, seen


@pytest.mark.parametrize("result, code", [
    (_fake_result(), 0),
    (_fake_result(survivors=[{"key": "a.py:3", "mutation": "m"}]), 1),
    (_fake_result(cannot_verify="red baseline"), 2),
    (_fake_result(unrun=[{"key": "a.py:3", "mutation": "m", "reason": "r"}]), 2),
    (_fake_result(unsupported=["a.go"]), 3),
    (_fake_result(survivors=[{"key": "a.py:3", "mutation": "m"}], unsupported=["a.go"]), 1),
    ({"ok": False, "error": "nope"}, 2),
])
@pytest.mark.parametrize("as_json", [True, False])
def test_cli_exit_code_is_the_verdict_in_both_output_modes(monkeypatch, capsys, result, code,
                                                          as_json):
    for r in (result.get("survivors") or []) + (result.get("unrun") or []):
        r.setdefault("status", "x")
    got, out, err, _ = _run_cli(monkeypatch, capsys, result, *(["--json"] if as_json else []))
    assert got == code
    if as_json:
        assert json.loads(out) == result
    else:
        assert not out.lstrip().startswith("{")


def test_cli_json_keeps_stdout_pure_and_progress_on_stderr(monkeypatch, capsys):
    code, out, err, _ = _run_cli(monkeypatch, capsys, _fake_result(), "--json", progress=True)
    assert json.loads(out) and "[1/2] a.py:3 flip" in err and "[1/2]" not in out


def test_cli_text_progress_goes_to_stdout(monkeypatch, capsys):
    code, out, err, _ = _run_cli(monkeypatch, capsys, _fake_result(), progress=True)
    assert "[1/2] a.py:3 flip" in out and "[1/2]" not in err


@pytest.mark.parametrize("source, shown", [("detected (tests/)", True), ("--test-cmd", False),
                                           ("WORKFLOW.md judge.test_cmd", False)])
def test_cli_names_the_suite_only_when_it_was_detected(monkeypatch, capsys, source, shown):
    _, out, _, _ = _run_cli(monkeypatch, capsys, _fake_result(test_cmd_source=source))
    assert ("suite: `t` — detected (tests/)" in out) is shown


def test_cli_text_banners_are_exclusive(monkeypatch, capsys):
    unk = _fake_result(unsupported=["a.go"])
    _, out, _, _ = _run_cli(monkeypatch, capsys, unk)
    assert "[UNKNOWN ] a.go — not supported: " + sweep.SUPPORTED_NOTE in out
    assert "⚠ UNKNOWN — 1 changed source file(s)" in out and "SURVIVED" not in out
    assert "never ran" not in out
    unrun = _fake_result(unrun=[{"key": "a.py:3", "mutation": "m", "reason": "r"}],
                         exhausted="budget")
    _, out, _, _ = _run_cli(monkeypatch, capsys, unrun)
    assert "mutation(s) never ran" in out and "⚠ UNKNOWN —" not in out
    _, out, _, _ = _run_cli(monkeypatch, capsys, _fake_result(cannot_verify="red"))
    assert "⚠ CANNOT VERIFY — red" in out and "sweep: 1 mutation(s)" not in out
    _, out, _, _ = _run_cli(monkeypatch, capsys, _fake_result())
    assert "sweep: 1 mutation(s) — 1 killed" in out and "⚠" not in out


def test_cli_passes_its_arguments_through(monkeypatch, capsys, tmp_path):
    _, _, _, seen = _run_cli(monkeypatch, capsys, _fake_result(), "--base", "b", "--test-cmd",
                             "tc", "--budget-seconds", "7", "--max-mutations", "3",
                             cwd=tmp_path)
    assert (seen["cwd"], seen["base"], seen["workflow_path"], seen["test_cmd"],
            seen["budget_seconds"], seen["max_mutations"]) == (str(tmp_path), "b", None, "tc",
                                                               7.0, 3)


def test_cli_workflow_is_auto_detected_in_cwd_and_explicit_wins(monkeypatch, capsys, tmp_path):
    (tmp_path / "WORKFLOW.md").write_text("x")
    _, _, _, seen = _run_cli(monkeypatch, capsys, _fake_result(), cwd=tmp_path)
    assert seen["workflow_path"] == str(tmp_path / "WORKFLOW.md")
    _, _, _, seen = _run_cli(monkeypatch, capsys, _fake_result(), "--workflow", "/e/W.md",
                             cwd=tmp_path)
    assert seen["workflow_path"] == "/e/W.md"


def test_cli_recovered_marker_is_printed(monkeypatch, capsys):
    _, out, _, _ = _run_cli(monkeypatch, capsys, _fake_result(recovered="restored x"))
    assert "⚠ restored x" in out


@pytest.mark.parametrize("with_wf, source_has", [(True, "judge.test_cmd"), (False, "detected")])
def test_run_sweep_reports_where_the_suite_came_from(tmp_path, monkeypatch, with_wf, source_has):
    (tmp_path / "pytest.ini").write_text("")
    monkeypatch.setattr(sweep.shutil, "which", lambda n: "/bin/python")
    wf = None
    if with_wf:
        wf = tmp_path / "WORKFLOW.md"
        wf.write_text("---\nproject_key: TEST\njudge:\n  test_cmd: wfcmd\n---\n")
    monkeypatch.setattr(sweep, "sweep", lambda *a, **kw: sweep.SweepReport())
    assert source_has in sweep.run_sweep(tmp_path, workflow_path=wf)["test_cmd_source"]
    d = sweep.run_sweep(tmp_path, workflow_path=wf, test_cmd=" flag ")
    assert d["test_cmd_source"] == "--test-cmd"


def test_run_sweep_base_defaults(tmp_path, monkeypatch):
    """No workflow, no --base -> origin/dev; a workflow's base_branch -> origin/<it>; --base wins."""
    calls = []
    monkeypatch.setattr(sweep, "sweep", lambda wt, base, cmd, **kw: calls.append(base) or
                        sweep.SweepReport())
    sweep.run_sweep(tmp_path, test_cmd="t")
    wf = tmp_path / "WORKFLOW.md"
    wf.write_text("---\nproject_key: TEST\nworkspace:\n  base_branch: trunk\n---\nbody\n")
    sweep.run_sweep(tmp_path, workflow_path=wf, test_cmd="t")
    sweep.run_sweep(tmp_path, workflow_path=wf, test_cmd="t", base="mine")
    assert calls == ["origin/dev", "origin/trunk", "mine"]


def test_run_sweep_without_a_workflow_or_base_uses_default_base(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(sweep, "default_base", lambda wt: calls.append(wt) or "origin/trunk")
    monkeypatch.setattr(sweep, "sweep", lambda wt, base, cmd, **kw: calls.append(base) or
                        sweep.SweepReport())
    sweep.run_sweep(tmp_path, test_cmd="t")
    sweep.run_sweep(tmp_path, test_cmd="t", base="mine")
    assert calls == [tmp_path, "origin/trunk", "mine"]


def test_default_base_is_origin_head_else_origin_dev(tmp_path):
    root = _bare(tmp_path / "r", {"calc.py": BIG})
    assert sweep.default_base(root) == "origin/dev"
    _git(root, "update-ref", "refs/remotes/origin/trunk", "main")
    _git(root, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
    assert sweep.default_base(root) == "origin/trunk"


@pytest.mark.parametrize("answer", [
    subprocess.CompletedProcess([], 0, "  \n", ""),         # blank answer
    subprocess.CompletedProcess([], 1, "origin/x\n", ""),   # failed read: never trusted
    sweep.GitHung("hung"),
])
def test_default_base_argv_and_an_unusable_answer(tmp_path, monkeypatch, answer):
    seen = []

    def fake(wt, *a):
        seen.append((wt, a))
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(sweep, "_git", fake)
    assert sweep.default_base(tmp_path) == "origin/dev"
    assert seen == [(tmp_path, ("symbolic-ref", "--quiet", "--short",
                                "refs/remotes/origin/HEAD"))]


@pytest.mark.parametrize("with_wf", [True, False])
def test_the_task_finished_hint_is_shown_only_for_a_chela_managed_run(monkeypatch, capsys,
                                                                      tmp_path, with_wf):
    """Standalone there is no `chela task-finished`, so its --sweep-equivalent escape hatch is
    not offered; with a WORKFLOW.md it is."""
    if with_wf:
        (tmp_path / "WORKFLOW.md").write_text("---\n---\n")
    code, out, _, _ = _run_cli(monkeypatch, capsys,
                               _fake_result(survivors=[{"key": "a.py:3", "mutation": "m",
                                                        "status": "x"}]), cwd=tmp_path)
    assert code == 1
    assert "Add a test that goes RED under that mutation" in out
    assert ("`chela task-finished` accepts --sweep-equivalent" in out) is with_wf


def test_run_sweep_unreadable_workflow_and_no_suite_are_errors(tmp_path):
    bad = tmp_path / "WORKFLOW.md"
    bad.write_text("no front matter, not a workflow")
    d = sweep.run_sweep(tmp_path, workflow_path=bad)
    assert d["ok"] is False and "no suite" in d["error"] or "could not be read" in d["error"]
    assert sweep.run_sweep(tmp_path / "empty_dir_missing", test_cmd=None)["ok"] is False


def test_run_sweep_budget_flags_override_the_workflow(tmp_path, monkeypatch):
    wf = tmp_path / "WORKFLOW.md"
    wf.write_text("---\nproject_key: TEST\njudge:\n  test_cmd: c\n  sweep_budget_seconds: 11\n"
                  "  sweep_max_mutations: 5\n---\nbody\n")
    seen = []
    monkeypatch.setattr(sweep, "sweep", lambda *a, **kw: seen.append(kw["budget"]) or
                        sweep.SweepReport())
    sweep.run_sweep(tmp_path, workflow_path=wf)
    sweep.run_sweep(tmp_path, workflow_path=wf, budget_seconds=2, max_mutations=1)
    assert [(b.seconds, b.max_mutations) for b in seen] == [(11.0, 5), (2.0, 1)]


def test_report_records_base_and_test_cmd(tmp_path):
    root = _bare(tmp_path / "r", {"README.md": "x\n"})
    d = sweep.sweep(root, "main", "true", provision=False).as_dict()
    assert (d["base"], d["test_cmd"], d["verdict"]) == ("main", "true", "clean")
    assert d["unsupported"] == []


def test_unsupported_note_and_untracked_unknown_end_to_end(tmp_path):
    root = _bare(tmp_path / "r", {"calc.py": BIG}, COVERING)
    (root / "later.go").write_text("x\n")                    # untracked, never committed
    got, d = _chela(root, tmp_path)
    assert (got, d["verdict"], d["unsupported"]) == (3, "unknown", ["later.go"])
    (root / "later.go").unlink()
    got, d = _chela(root, tmp_path)
    assert (got, d["verdict"]) == (0, "clean")
