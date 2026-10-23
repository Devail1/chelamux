"""⛔ CMX-162 — a killed ``judge self-check`` / ``judge run`` leaves no mutation on disk.

The judge's apply → measure → restore loop reuses the sweep's signal handlers and kill
guardian (:mod:`chela.sweep`, CMX-97/131). Every test runs against a throwaway git repo in
``tmp_path`` and signals only the ``self-check`` process it spawned itself.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from chela import judge, sweep

FEAT = '''\
def new(x):
    if x == 3:
        return "three"
    return "other"
'''
BEFORE = "    if x == 3:"
AFTER = "    if False and (x == 3):"

# Instant on the clean tree. With the mutation on disk it raises `flag`, then waits — up to a
# minute — for the file to be put back, so the test can kill the self-check at exactly the
# moment a mutation is live, and the suite never outlives a restored file.
SLOW_SUITE = '''\
import pathlib, sys, time
feat = pathlib.Path("feat.py")
if "False and" in feat.read_text():
    pathlib.Path(sys.argv[1]).write_text("mutated")
    deadline = time.monotonic() + 60
    while "False and" in feat.read_text() and time.monotonic() < deadline:
        time.sleep(0.1)
    sys.exit(1)
'''

# The negative control: the same CLI with the guardian switched off in the child only.
NO_GUARDIAN = "import chela.sweep as s; s._start_guardian = lambda worktree: None; "


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *args],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def _repo(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "feat.py").write_text(FEAT)
    _git(root, "init", "-q", "-b", "dev")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    return root


def _experiments(tmp_path: Path) -> Path:
    path = tmp_path / "experiments.json"       # outside the repo: never in its `git status`
    path.write_text(json.dumps({"experiments": [
        {"guard": "new(3) is three", "file": "feat.py", "before": BEFORE, "after": AFTER},
    ]}))
    return path


def _start(tmp_path: Path, root: Path, prelude: str = "") -> tuple[subprocess.Popen, Path]:
    """Spawn ``chela judge self-check`` and return once its mutation is on disk."""
    flag = tmp_path / "flag"
    script = tmp_path / "slow_suite.py"
    script.write_text(SLOW_SUITE)
    proc = subprocess.Popen(
        [sys.executable, "-c", prelude + "from chela.main import main; main()",
         "judge", "self-check", "--cwd", str(root), "--experiments", str(_experiments(tmp_path)),
         "--test-cmd", f'"{sys.executable}" "{script}" "{flag}"'],
        env={**os.environ, "CHELA_DIR": str(tmp_path / "chela-dir")},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True,
    )
    deadline = time.monotonic() + 60
    while not flag.exists():
        assert proc.poll() is None, proc.communicate()[0]
        assert time.monotonic() < deadline, "the self-check never reached a mutation"
        time.sleep(0.1)
    assert AFTER in (root / "feat.py").read_text()      # the kill lands mid-mutation
    return proc, flag


def _clean(root: Path, original: bytes) -> bool:
    return ((root / "feat.py").read_bytes() == original and not sweep.marker_path(root).exists()
            and _git(root, "diff") == "" and _git(root, "status", "--porcelain") == "")


def _wait_clean(root: Path, original: bytes, seconds: float = 10) -> None:
    deadline = time.monotonic() + seconds
    while not _clean(root, original):
        assert time.monotonic() < deadline, (
            f"still dirty {seconds:g}s after the kill:\n{_git(root, 'diff')}")
        time.sleep(0.1)


# --- the real thing: a self-check subprocess, killed ---------------------------------------


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGHUP, signal.SIGINT])
def test_a_signalled_self_check_restores_the_file_and_still_dies_by_the_signal(tmp_path, sig):
    """The process restores the file ITSELF before it dies — the guardian is switched off, so
    it cannot be what cleaned up."""
    root = _repo(tmp_path / "r")
    original = (root / "feat.py").read_bytes()
    proc, _ = _start(tmp_path, root, prelude=NO_GUARDIAN)
    proc.send_signal(sig)
    out, _ = proc.communicate(timeout=60)
    assert proc.returncode != 0, out
    if sig != signal.SIGINT:        # SIGINT may be inherited as ignored; TERM/HUP are not
        assert proc.returncode == -sig, out     # re-delivered: the process dies as it would have
    try:
        assert _clean(root, original), _git(root, "diff")
    finally:
        _git(root, "checkout", "--", "feat.py")      # lets the waiting suite exit on a failure
        sweep.marker_path(root).unlink(missing_ok=True)


def test_a_sigkilled_self_check_is_restored_by_its_guardian(tmp_path):
    root = _repo(tmp_path / "r")
    original = (root / "feat.py").read_bytes()
    proc, _ = _start(tmp_path, root)
    assert sweep.marker_path(root).exists()             # written BEFORE the mutation landed
    proc.kill()
    proc.wait(timeout=30)
    _wait_clean(root, original)


def test_without_the_guardian_a_sigkill_leaves_the_mutation_on_disk(tmp_path):
    """The negative control: proves the SIGKILL test can see a mutation left behind."""
    root = _repo(tmp_path / "r")
    proc, _ = _start(tmp_path, root, prelude=NO_GUARDIAN)
    proc.kill()
    proc.wait(timeout=30)
    time.sleep(3)                   # > 10× the guardian's poll interval
    try:
        assert AFTER in (root / "feat.py").read_text()
        assert sweep.marker_path(root).exists()
        assert _git(root, "diff") != ""
    finally:
        _git(root, "checkout", "--", "feat.py")      # lets the waiting suite exit too
        sweep.marker_path(root).unlink(missing_ok=True)


# --- the in-process paths -------------------------------------------------------------------

PASS_CMD = f'"{sys.executable}" -c "pass"'


def _self_check(root: Path, tmp_path: Path, test_cmd: str = PASS_CMD) -> judge.Report:
    return judge.self_check(root, test_cmd, json.loads(_experiments(tmp_path).read_text()),
                            timeout=60, select_tests=False)


def test_a_finished_self_check_leaves_no_marker_and_no_guardian(tmp_path, monkeypatch):
    root = _repo(tmp_path / "r")
    original = (root / "feat.py").read_bytes()
    started = []
    real = sweep._start_guardian
    monkeypatch.setattr(sweep, "_start_guardian",
                        lambda wt: started.append(real(wt)) or started[-1])
    report = _self_check(root, tmp_path)
    assert [o.verdict for o in report.outcomes] == [judge.SURVIVED]
    assert started and started[0] is not None
    assert started[0].args == [sys.executable, "-m", "chela.sweep", "guard", str(os.getpid()),
                               str(root)]
    assert started[0].poll() is not None                # stopped, not left running
    assert _clean(root, original)


def test_the_marker_holds_the_original_and_the_exact_mutation(tmp_path):
    root = _repo(tmp_path / "r")
    seen = tmp_path / "marker-seen.json"
    script = tmp_path / "copy_marker.py"
    script.write_text("import pathlib, shutil, sys\n"
                      "if 'False and' in pathlib.Path('feat.py').read_text():\n"
                      f"    shutil.copy({str(sweep.marker_path(root))!r}, sys.argv[1])\n")
    _self_check(root, tmp_path, f'"{sys.executable}" "{script}" "{seen}"')
    data = json.loads(seen.read_text())
    assert data == {"pid": os.getpid(), "file": "feat.py", "original": FEAT,
                    "mutated": FEAT.replace(BEFORE, AFTER, 1)}


def test_an_interrupt_whose_old_handler_returns_restores_and_is_cannot_verify(tmp_path,
                                                                             monkeypatch):
    """A daemon-style handler that only sets a flag: the signal reaches it AFTER the file is
    back, and the report is CANNOT VERIFY, never a verdict."""
    root = _repo(tmp_path / "r")
    original = (root / "feat.py").read_bytes()

    def interrupted(*_a, **_k):
        assert AFTER in (root / "feat.py").read_text()
        raise sweep._Interrupted(signal.SIGTERM)

    monkeypatch.setattr(judge, "_measure", interrupted)
    got: list = []

    def handler(signum, _frame):
        got.append((signum, (root / "feat.py").read_bytes() == original))

    old = signal.signal(signal.SIGTERM, handler)
    try:
        report = _self_check(root, tmp_path)
        after = signal.getsignal(signal.SIGTERM)
    finally:
        signal.signal(signal.SIGTERM, old)
    assert got == [(signal.SIGTERM, True)]               # delivered once, file already restored
    assert after is handler                              # the earlier handler is back
    assert report.state == judge.J_CANNOT_VERIFY
    assert report.cannot_verify == (f"interrupted by signal {signal.SIGTERM} — the experiments "
                                    "stopped; the mutated file was restored")
    assert _clean(root, original)


def test_an_interrupt_before_the_restoring_try_is_recovered_from_the_marker(tmp_path,
                                                                           monkeypatch):
    """The signal lands after the mutation's write but before the loop's `finally` covers it."""
    root = _repo(tmp_path / "r")
    original = (root / "feat.py").read_bytes()
    real_apply = judge.apply_mutation

    def apply_then_interrupted(*a, **k):
        real_apply(*a, **k)
        raise sweep._Interrupted(signal.SIGTERM)

    monkeypatch.setattr(judge, "apply_mutation", apply_then_interrupted)
    old = signal.signal(signal.SIGTERM, lambda *_: None)
    try:
        report = _self_check(root, tmp_path)
    finally:
        signal.signal(signal.SIGTERM, old)
    assert report.cannot_verify.endswith("; the mutated file was restored")
    assert _clean(root, original)


def test_an_interrupt_whose_recovery_fails_says_so(tmp_path, monkeypatch):
    root = _repo(tmp_path / "r")
    monkeypatch.setattr(judge, "_measure",
                        lambda *a, **k: (_ for _ in ()).throw(sweep._Interrupted(signal.SIGTERM)))
    calls = []
    real_recover = sweep.recover

    def recover(wt):
        calls.append(wt)
        return ("", "the file moved on") if len(calls) > 1 else real_recover(wt)

    monkeypatch.setattr(sweep, "recover", recover)
    old = signal.signal(signal.SIGTERM, lambda *_: None)
    try:
        report = _self_check(root, tmp_path)
    finally:
        signal.signal(signal.SIGTERM, old)
    assert report.cannot_verify == (f"interrupted by signal {signal.SIGTERM} — the experiments "
                                    "stopped, and the mutated file could NOT be recovered: "
                                    "the file moved on")


def test_a_killed_judges_leftover_mutation_is_recovered_before_the_next_run(tmp_path, caplog):
    root = _repo(tmp_path / "r")
    original = (root / "feat.py").read_bytes()
    mutated = FEAT.replace(BEFORE, AFTER, 1)
    (root / "feat.py").write_text(mutated)
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    sweep.marker_path(root).write_text(json.dumps(
        {"pid": dead.pid, "file": "feat.py", "original": FEAT, "mutated": mutated}))
    with caplog.at_level(logging.WARNING, logger=judge.log.name):
        report = _self_check(root, tmp_path)
    assert [o.verdict for o in report.outcomes] == [judge.SURVIVED]
    assert "restored feat.py from a killed sweep's recovery marker" in caplog.text
    assert _clean(root, original)


def test_a_live_runs_marker_refuses_the_experiments_and_touches_nothing(tmp_path):
    root = _repo(tmp_path / "r")
    mutated = FEAT.replace(BEFORE, AFTER, 1)
    (root / "feat.py").write_text(mutated)
    live = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        sweep.marker_path(root).write_text(json.dumps(
            {"pid": live.pid, "file": "feat.py", "original": FEAT, "mutated": mutated}))
        report = _self_check(root, tmp_path)
    finally:
        live.kill()
        live.wait()
    assert report.outcomes == []
    assert report.state == judge.J_CANNOT_VERIFY
    assert f"another sweep (pid {live.pid})" in report.cannot_verify
    assert (root / "feat.py").read_text() == mutated     # not ours to restore
    assert sweep.marker_path(root).exists()


def test_a_guardian_that_cannot_start_is_logged_and_the_experiment_still_runs(
        tmp_path, monkeypatch, caplog):
    root = _repo(tmp_path / "r")
    original = (root / "feat.py").read_bytes()
    monkeypatch.setattr(sweep, "_start_guardian", lambda wt: None)
    with caplog.at_level(logging.WARNING, logger=judge.log.name):
        report = _self_check(root, tmp_path)
    assert [o.verdict for o in report.outcomes] == [judge.SURVIVED]
    assert "the kill guardian could not start" in caplog.text
    assert _clean(root, original)


def test_a_marker_that_cannot_be_written_is_logged_and_the_experiment_still_runs(
        tmp_path, monkeypatch, caplog):
    root = _repo(tmp_path / "r")
    original = (root / "feat.py").read_bytes()

    def unwritable(*_a, **_k):
        raise PermissionError("read-only worktree")

    monkeypatch.setattr(sweep, "_write_marker", unwritable)
    with caplog.at_level(logging.WARNING, logger=judge.log.name):
        report = _self_check(root, tmp_path)
    assert [o.verdict for o in report.outcomes] == [judge.SURVIVED]
    assert "no recovery marker for feat.py (read-only worktree)" in caplog.text
    assert _clean(root, original)


def test_an_unreadable_file_is_not_marked_and_never_applied(tmp_path):
    root = _repo(tmp_path / "r")
    (root / "feat.py").write_bytes(b"\xff\xfe not utf-8 " + BEFORE.encode() + b"\n")
    report = _self_check(root, tmp_path)
    assert [o.verdict for o in report.outcomes] == [judge.INVALID]
    assert not sweep.marker_path(root).exists()


def test_run_experiments_the_judges_pr_pass_is_covered_too(tmp_path, monkeypatch):
    """`judge run` goes through the same `_apply_experiments`: the guardian starts there too."""
    root = _repo(tmp_path / "r")
    original = (root / "feat.py").read_bytes()
    started = []
    monkeypatch.setattr(sweep, "_start_guardian", lambda wt: started.append(wt))
    report = judge.run_experiments(root, PASS_CMD, json.loads(_experiments(tmp_path).read_text()),
                                   timeout=60, select_tests=False, held_out=False)
    assert report.outcomes and report.outcomes[0].verdict == judge.SURVIVED
    assert started == [root]
    assert _clean(root, original)


def test_a_mutation_refused_after_its_write_is_still_restored(tmp_path, monkeypatch):
    """`apply_mutation` can return `applied=False` AFTER writing (a read-back that raised, a
    replacement missing from the result). The file is restored all the same — dropping the
    marker without restoring would leave nothing able to undo it."""
    root = _repo(tmp_path / "r")
    original = (root / "feat.py").read_bytes()
    real_apply = judge.apply_mutation

    def written_then_refused(path, before, after):
        _, _, text = real_apply(path, before, after)
        return False, "the read-back raised", text

    monkeypatch.setattr(judge, "apply_mutation", written_then_refused)
    report = _self_check(root, tmp_path)
    assert [o.verdict for o in report.outcomes] == [judge.INVALID]
    assert _clean(root, original)


def test_a_signal_while_the_guardian_starts_still_restores_the_handlers(tmp_path, monkeypatch):
    root = _repo(tmp_path / "r")
    original = (root / "feat.py").read_bytes()

    def interrupted(_wt):
        raise sweep._Interrupted(signal.SIGTERM)

    monkeypatch.setattr(sweep, "_start_guardian", interrupted)
    got: list = []

    def handler(signum, _frame):
        got.append(signum)

    old = signal.signal(signal.SIGTERM, handler)
    try:
        report = _self_check(root, tmp_path)
        after = signal.getsignal(signal.SIGTERM)
    finally:
        signal.signal(signal.SIGTERM, old)
    assert got == [signal.SIGTERM]
    assert after is handler
    assert report.state == judge.J_CANNOT_VERIFY
    assert report.cannot_verify.startswith(f"interrupted by signal {signal.SIGTERM}")
    assert _clean(root, original)
