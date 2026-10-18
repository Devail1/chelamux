"""⏳ CMX-253 — a suite run that hits its timeout dies WHOLE, and says TIMEOUT.

On 2026-10-18 a sweep mutant sent a test past its stubs into the real ``gh``; the suite hit
its 1500s timeout, ``subprocess.run`` killed only the shell, and the ``uv``/pytest/xdist tree
under it sat idle for 75+ minutes. These tests start real process trees and check, by pid,
that nothing the suite started is alive afterwards.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from chela import judge, judge_select, procguard, sweep

# ``conftest``, not ``tests.conftest`` — see tests/test_isolation.py.
import conftest  # noqa: E402,I001


class Hung(Exception):
    pass


@pytest.fixture(autouse=True)
def _hang_is_a_failure():
    """A runner that never returns is a RED test, not a suite timeout: under a mutation that
    breaks the grace loop the run would otherwise hang until the judge's own timeout."""
    def hung(*_):
        raise Hung("the runner did not return within 60s")

    old = signal.signal(signal.SIGALRM, hung)
    signal.alarm(60)
    yield
    signal.alarm(0)
    signal.signal(signal.SIGALRM, old)


def _alive(pid: int) -> bool:
    return procguard._identity(pid) is not None


def _gone_soon(pids: list[int], seconds: float = 5.0) -> list[int]:
    """The pids still alive after ``seconds`` (an orphan is reaped by init, not at once)."""
    deadline = time.monotonic() + seconds
    while (alive := [p for p in pids if _alive(p)]) and time.monotonic() < deadline:
        time.sleep(0.05)
    return alive


@pytest.fixture
def pids(tmp_path):
    """Pid files the fake suite writes; every pid still alive at teardown is SIGKILLed so a
    RED run never leaks the very orphans it found."""
    files: list[Path] = []

    def read() -> list[int]:
        return [int(f.read_text()) for f in files if f.exists() and f.read_text().strip()]

    def new(name: str) -> Path:
        files.append(tmp_path / name)
        return files[-1]

    new.read = read
    yield new
    for pid in read():
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _bg(pidfile: Path, body: str = "exec sleep 300") -> str:
    """A shell fragment that starts ``body`` in the background, off the output pipes, and
    records its pid — the shape of an xdist worker the shell never waits for."""
    return f"( {body} ) >/dev/null 2>&1 & echo $! > {pidfile}; "


def test_a_timed_out_suite_has_its_whole_tree_killed_and_reports_TIMEOUT(tmp_path, pids):
    a, b = pids("a"), pids("b")
    cmd = _bg(a) + _bg(b) + "echo started-the-workers; wait"
    t0 = time.monotonic()
    res = judge.run_suite(cmd, tmp_path, timeout=1)

    assert time.monotonic() - t0 < 30
    assert len(pids.read()) == 2
    assert _gone_soon(pids.read()) == []                   # no descendant alive, by pid
    assert res.timed_out is True and res.ok is False and res.exit_code == -1
    assert res.detail == ("TIMEOUT — the suite did not finish in 1s; its process tree was "
                          "killed")
    assert "started-the-workers" in res.run["output"]     # what it printed is kept


def test_a_member_that_ignores_SIGTERM_is_SIGKILLed_after_the_grace(tmp_path, pids):
    stubborn = pids("stubborn")
    cmd = _bg(stubborn, "trap '' TERM; exec sleep 300") + "wait"
    t0 = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        procguard.run_shell_group(cmd, timeout=0.5, cwd=str(tmp_path), grace=0.5)
    assert time.monotonic() - t0 < 10
    assert _gone_soon(pids.read()) == []


def test_SIGTERM_comes_first_and_the_grace_lets_a_member_exit_cleanly(tmp_path, pids):
    """The group gets SIGTERM before SIGKILL: a member's TERM handler runs."""
    marker = tmp_path / "termed"
    cmd = _bg(pids("p"), f"trap 'echo x > {marker}; exit 0' TERM; sleep 300 & wait") + "wait"
    t0 = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        procguard.run_shell_group(cmd, timeout=0.5, cwd=str(tmp_path), grace=20)
    assert marker.exists()
    assert time.monotonic() - t0 < 10        # …and once the group is gone, no more waiting


def test_a_suite_that_exits_still_takes_its_background_children_with_it(tmp_path, pids):
    cp = procguard.run_shell_group(_bg(pids("left")) + "echo done", timeout=30,
                                   cwd=str(tmp_path))
    assert (cp.returncode, cp.stdout) == (0, "done\n")
    assert _gone_soon(pids.read()) == []


def test_an_interrupted_run_kills_the_group_before_re_raising(tmp_path, pids):
    """SIGTERM to a sweep arrives as an exception (``sweep._Interrupted``) mid-communicate."""
    real = subprocess.Popen.communicate
    calls = []

    def interrupted(self, *a, **kw):
        calls.append(kw.get("timeout"))
        if len(calls) == 1:
            time.sleep(0.3)                      # let the shell start its child first
            raise KeyboardInterrupt
        return real(self, *a, **kw)

    with patch.object(subprocess.Popen, "communicate", interrupted), \
            pytest.raises(KeyboardInterrupt):
        procguard.run_shell_group(_bg(pids("c")) + "wait", timeout=30, cwd=str(tmp_path),
                                  grace=2)
    assert _gone_soon(pids.read()) == []


def test_a_process_that_escaped_the_group_cannot_wedge_the_drain(tmp_path, pids):
    """``setsid`` leaves the group but keeps the pipes: the read after the kill is bounded."""
    escaped = pids("escaped")
    cmd = f"setsid sleep 30 & echo $! > {escaped}; wait"
    t0 = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        procguard.run_shell_group(cmd, timeout=0.5, cwd=str(tmp_path), grace=0.5)
    assert time.monotonic() - t0 < 10


def test_group_alive_reads_each_answer_of_a_signal_0_probe(monkeypatch):
    def probe(err):
        def killpg(pgid, sig):
            assert sig == 0
            if err:
                raise err
        return killpg

    monkeypatch.setattr(procguard.os, "killpg", probe(None))
    assert procguard._group_alive(4242) is True
    monkeypatch.setattr(procguard.os, "killpg", probe(ProcessLookupError()))
    assert procguard._group_alive(4242) is False
    monkeypatch.setattr(procguard.os, "killpg", probe(PermissionError()))
    assert procguard._group_alive(4242) is True       # a member exists, not ours to signal


def test_run_suite_hands_the_runner_the_suite_env_cwd_and_timeout(tmp_path, monkeypatch):
    seen = {}

    def fake(cmd, **kw):
        seen.update(kw, cmd=cmd)
        return subprocess.CompletedProcess(cmd, 0, "1 passed\n", "")

    monkeypatch.setattr(procguard, "run_shell_group", fake)
    res = judge.run_suite("pytest -q", tmp_path, timeout=42, extra_env={"X_CMX253": "1"})
    assert seen["cmd"] == "pytest -q" and seen["cwd"] == str(tmp_path)
    assert seen["timeout"] == 42 and seen["env"]["X_CMX253"] == "1"
    assert "CHELA_DIR" in seen["env"]
    assert (res.green, res.timed_out, res.passed) == (True, False, 1)


# --- a timed-out mutant is TIMEOUT (unknown), never KILLED and never silently skipped ------

_EXP = judge.Experiment(guard="g", file="f.py", before="a", after="b")
_TIMED_OUT = judge.SuiteResult(False, -1, 0, 0, 0, "", "TIMEOUT — the suite did not finish "
                               "in 9s; its process tree was killed", None, timed_out=True)


def test_adjudicate_calls_a_timed_out_suite_TIMEOUT_unknown():
    base = judge.SuiteResult(True, 0, 5, 0, 0, "")
    out = judge.adjudicate(_EXP, True, "", True, "", base, _TIMED_OUT)
    assert out.verdict == judge.INVALID
    assert out.reason == ("TIMEOUT (unknown) under the mutation: TIMEOUT — the suite did not "
                          "finish in 9s; its process tree was killed")
    crashed = judge.SuiteResult(False, -1, 0, 0, 0, "", "could not be run: boom")
    assert judge.adjudicate(_EXP, True, "", True, "", base, crashed).reason.startswith(
        "the suite could not be run under the mutation: ")


def test_a_timed_out_subset_is_final_and_never_re_run_on_the_full_suite(monkeypatch):
    calls = []
    monkeypatch.setattr(judge, "run_suite", lambda cmd, *a, **k: calls.append(cmd) or _TIMED_OUT)

    class OneFile:
        def select(self, file):
            return judge_select.Selection(["tests/test_f.py"], 3, "imports f.py")

    base = judge.SuiteResult(True, 0, 5, 0, 0, "")
    out = judge._measure(Path("."), "pytest -q", _EXP, "", True, "", base, 9, OneFile())
    assert calls == [judge_select.extend("pytest -q", ["tests/test_f.py"])]
    assert out.verdict == judge.INVALID and out.reason.startswith("TIMEOUT (unknown)")
    assert not out.confirmed_full


def test_a_subset_that_merely_failed_to_run_is_still_confirmed_on_the_full_suite(monkeypatch):
    crashed = judge.SuiteResult(False, -1, 0, 0, 0, "", "could not be run: boom")
    calls = []
    monkeypatch.setattr(judge, "run_suite", lambda cmd, *a, **k: calls.append(cmd) or crashed)

    class OneFile:
        def select(self, file):
            return judge_select.Selection(["tests/test_f.py"], 3, "imports f.py")

    base = judge.SuiteResult(True, 0, 5, 0, 0, "")
    judge._measure(Path("."), "pytest -q", _EXP, "", True, "", base, 9, OneFile())
    assert calls[-1] == "pytest -q" and len(calls) == 2


def test_the_sweep_reports_a_timed_out_mutation_as_TIMEOUT(tmp_path, monkeypatch):
    (tmp_path / "f.py").write_text("def f(x):\n    if x:\n        return 1\n    return 0\n")
    mut = sweep.Mutation("f.py", 2, "if-false", "    if x:", "    if False and (x):")
    monkeypatch.setattr(judge, "run_suite", lambda *a, **k: _TIMED_OUT)
    base = judge.SuiteResult(True, 0, 5, 0, 0, "")
    res = sweep._apply_and_measure(tmp_path, "pytest -q", mut, mut.before, mut.after, base, 9,
                                   None)
    assert res.status == sweep.TIMEOUT == "TIMEOUT"
    crashed = judge.SuiteResult(False, -1, 0, 0, 0, "", "boom")
    monkeypatch.setattr(judge, "run_suite", lambda *a, **k: crashed)
    (tmp_path / "f.py").write_text("def f(x):\n    if x:\n        return 1\n    return 0\n")
    res = sweep._apply_and_measure(tmp_path, "pytest -q", mut, mut.before, mut.after, base, 9,
                                   None)
    assert res.status == judge.INVALID


def test_a_sweep_with_a_TIMEOUT_is_cannot_verify_never_clean():
    rep = sweep.SweepReport(results=[sweep.SweepResult(
        sweep.Mutation("f.py", 2, "if-false", "a", "b"), sweep.TIMEOUT, "hung")])
    d = rep.as_dict()
    assert [r["key"] for r in d["timeout"]] == ["f.py:2"] and d["killed"] == 0
    assert d["invalid"] == [] and d["verdict"] == sweep.V_CANNOT_VERIFY
    assert sweep.EXIT_CODES[d["verdict"]] == 2


_ONE_TIMEOUT = {
    "ok": True, "cannot_verify": "", "recovered": "", "notes": [], "mutations": 2,
    "measured": 2, "killed": 1, "survivors": [], "unparseable": [], "not_applied": [],
    "invalid": [], "unrun": [], "seconds": 4.0, "exhausted": "",
    "timeout": [{"key": "chela/overrides.py:9", "mutation": "return-none: `a` → `b`",
                 "reason": "TIMEOUT (unknown) under the mutation"}],
}


def test_cli_sweep_prints_TIMEOUT_and_exits_cannot_verify(monkeypatch, capsys):
    from chela import main

    monkeypatch.setattr(sweep, "run_sweep", lambda cwd, **kw: dict(_ONE_TIMEOUT))
    with patch.object(sys, "argv", ["chela", "judge", "sweep", "--cwd", "."]), \
            pytest.raises(SystemExit) as e:
        main.main()
    out = capsys.readouterr().out
    assert e.value.code == 2
    assert ("  [TIMEOUT ] chela/overrides.py:9 return-none: `a` → `b` — UNKNOWN, not killed: "
            "TIMEOUT (unknown) under the mutation") in out
    assert "sweep: 2 mutation(s) — 1 killed, 0 survived, 0 skipped, 1 TIMEOUT (4.0s)" in out
    assert ("⚠ CANNOT VERIFY — 1 mutation(s) hit the suite timeout; their lines are unknown, "
            "not killed.") in out
    assert "[SKIPPED ]" not in out


def test_task_finished_names_a_TIMEOUT_and_never_says_pass(capsys):
    from chela import dispatcher, main

    with patch.object(dispatcher, "verify_sweep", return_value=dict(_ONE_TIMEOUT)), \
         patch.object(dispatcher, "check_no_new_guards", return_value=False), \
         patch.object(dispatcher, "request_task_finished",
                      return_value={"ok": True, "task_id": "cmx-88"}), \
         patch.object(sys, "argv", ["chela", "task-finished", "cmx-88", "--no-new-guards"]):
        main.main()
    out = capsys.readouterr().out
    assert "  [TIMEOUT ] chela/overrides.py:9" in out
    assert ("0 survivor(s) declared equivalent; 0 UNRUN, 1 TIMEOUT = CANNOT VERIFY, not "
            "passed") in out
    assert "✓ sweep" not in out


# --- the gh fence --------------------------------------------------------------------------


def test_a_test_merging_through_the_real_gh_fails_at_once_and_never_runs_it(tmp_path,
                                                                           monkeypatch):
    """The real ``gh`` here is a stand-in that would leave a mark if it ever ran."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    ran = tmp_path / "gh-ran"
    (bindir / "gh").write_text(f"#!/bin/sh\ntouch {ran}\n")
    (bindir / "gh").chmod(0o755)
    monkeypatch.setattr(conftest, "_REAL_GH_REALPATH", str(bindir / "gh"))
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    t0 = time.monotonic()
    with pytest.raises(conftest.LiveGhEscape) as e:
        subprocess.run(["gh", "pr", "merge", "7", "--squash"], timeout=30)
    assert time.monotonic() - t0 < 5 and not ran.exists()
    assert str(e.value).startswith("chela tests: the real `gh` is fenced off (CMX-253)")
    with pytest.raises(conftest.LiveGhEscape):
        subprocess.run("gh api -X PUT repos/o/r/pulls/7/merge", shell=True, timeout=30)
    with pytest.raises(conftest.LiveGhEscape):
        subprocess.run(["gh", "api", "-X", "POST", "repos/o/r/merges"], timeout=30)
    assert not ran.exists()
    subprocess.run(["gh", "pr", "view", "7"], timeout=30)        # a read is not a merge…
    assert ran.exists()                                          # …so only the PATH stub fences it


def test_a_tests_own_fake_gh_still_runs(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "gh").write_text("#!/bin/sh\necho fake-gh \"$@\"\n")
    (bindir / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    out = subprocess.run(["gh", "pr", "view"], capture_output=True, text=True, timeout=30)
    assert out.stdout == "fake-gh pr view\n"
    assert conftest._gh_violation(["gh", "pr", "merge", "7"], None) is None   # its own fake
    assert conftest._gh_violation(["git", "merge", "x"], None) is None
    assert conftest._gh_violation([], None) is None


def test_any_gh_a_test_runs_by_name_meets_the_failing_stub():
    """A read in-process, or anything a child process (``sh`` here; a spawned ``chela`` in
    real life) runs: ``gh`` resolves to the stub, which fails loudly at once — and no token
    is left to use."""
    out = subprocess.run(["gh", "pr", "view", "7"], capture_output=True, text=True, timeout=30)
    assert out.returncode == 97 and conftest.GH_FENCE_MESSAGE in out.stderr
    out = subprocess.run(["sh", "-c", "gh pr merge 7"], capture_output=True, text=True,
                         timeout=30)
    assert out.returncode == 97
    assert conftest.GH_FENCE_MESSAGE in out.stderr
    assert os.environ["PATH"].split(os.pathsep)[0] == str(conftest.GH_STUB_DIR)
    assert not {"GH_TOKEN", "GITHUB_TOKEN"} & set(os.environ)
    assert os.environ["GH_CONFIG_DIR"] == str(conftest.GH_STUB_DIR / "config")


def test_an_explicit_env_without_PATH_is_resolved_against_the_default_path(tmp_path,
                                                                            monkeypatch):
    """``Popen(env={...})`` with no PATH execs against ``os.defpath`` — so that is where the
    fence must look, not at this process's PATH (where a test's fake would hide the real)."""
    fake = tmp_path / "gh"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(conftest, "_REAL_GH_REALPATH", str(fake))
    monkeypatch.setattr(conftest.os, "defpath", str(tmp_path))
    assert conftest._gh_violation(["gh", "pr", "merge", "1"], {"HOME": "/x"}) is not None
    monkeypatch.setattr(conftest.os, "defpath", "/nonexistent-cmx253")
    assert conftest._gh_violation(["gh", "pr", "merge", "1"], {"HOME": "/x"}) is None
    assert conftest._gh_violation(["gh", "pr", "merge", "1"], None) is not None
