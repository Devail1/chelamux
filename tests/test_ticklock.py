"""CMX-95: a restart of chela-daemon waits for the CURRENT tick, never for the fleet.

With real processes and a real `flock`: a restart requested mid-tick waits, then SIGKILLs
the "daemon" (PM2's worst case) and finds its run row COMPLETE; a tick past the bound, or an
unreadable lock, is a refusal — never "safe to restart".
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from chela import config, main, ticklock, update

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _lockdir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path / "chela")
    monkeypatch.setattr(ticklock, "_held_here", False)
    return tmp_path / "chela"


def _wait_for(path: Path, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not path.exists():
        assert time.monotonic() < deadline, f"{path} never appeared"
        time.sleep(0.01)


def _py(code: str, *args, **kw) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", textwrap.dedent(code), *map(str, args)],
                            cwd=REPO, **kw)


# A stand-in daemon: ONE tick writing a run row in two steps with a gap between them.
_DAEMON = """
    import json, sys, time
    from pathlib import Path
    from chela import config, ticklock
    config.CHELA_DIR = Path(sys.argv[1])
    row, in_tick, gap = Path(sys.argv[2]), Path(sys.argv[3]), float(sys.argv[4])
    fd = ticklock.begin_tick(lambda s: (time.sleep(s), False)[1])
    row.write_text(json.dumps({"task_id": "CMX-1", "status": "claimed"}))
    in_tick.touch()
    time.sleep(gap)
    row.write_text(json.dumps({"task_id": "CMX-1", "status": "running", "window": "@7"}))
    ticklock.end_tick(fd)
    time.sleep(60)
"""


def _start_daemon(tmp_path, lockdir, gap):
    row, in_tick = tmp_path / "row.json", tmp_path / "in-tick"
    proc = _py(_DAEMON, lockdir, row, in_tick, gap)
    _wait_for(in_tick)
    return proc, row


def test_a_restart_requested_mid_tick_waits_and_leaves_a_consistent_run_row(tmp_path, _lockdir):
    proc, row = _start_daemon(tmp_path, _lockdir, gap=1.0)
    try:
        t0 = time.monotonic()
        with ticklock.between_ticks(timeout=10) as refused:
            assert refused is None
            waited = time.monotonic() - t0
            proc.send_signal(signal.SIGKILL)
            proc.wait(timeout=5)
        assert json.loads(row.read_text()) == {"task_id": "CMX-1", "status": "running",
                                               "window": "@7"}, "restart landed mid-tick"
        assert 0.5 <= waited < 9, waited
    finally:
        proc.kill()


def test_a_tick_that_outlives_the_bound_is_refused_and_holds_nothing(tmp_path, _lockdir):
    proc, row = _start_daemon(tmp_path, _lockdir, gap=3.0)
    try:
        with ticklock.between_ticks(timeout=1) as refused:
            assert refused == ("chela-daemon is still mid-tick after 1s — not restarting it; "
                               "try again shortly")
        deadline = time.monotonic() + 10     # the refusal did not wedge the tick
        while json.loads(row.read_text())["status"] != "running":
            assert time.monotonic() < deadline
            time.sleep(0.05)
    finally:
        proc.kill()
        proc.wait(timeout=5)


def test_an_unreadable_lock_refuses_the_restart_but_the_daemon_still_ticks(tmp_path, monkeypatch):
    """A CHELA_DIR that is a FILE: mkdir raises FileExistsError, an OSError subclass."""
    (tmp_path / "f").write_text("")
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path / "f")
    with ticklock.between_ticks(timeout=1) as refused:
        assert refused.startswith("cannot tell whether chela-daemon is mid-tick (")
        assert refused.endswith(") — not restarting it")
    assert ticklock.begin_tick(lambda s: False) == -1
    ticklock.end_tick(-1)


def test_begin_tick_waits_for_a_restart_and_leaves_on_shutdown(_lockdir):
    cm = ticklock.between_ticks(timeout=1)
    assert cm.__enter__() is None
    waits, got = [], []
    # In a thread, bounded: a begin_tick that ignores `wait` would spin on the lock forever.
    t = threading.Thread(target=lambda: got.append(
        ticklock.begin_tick(lambda s: (waits.append(s), True)[1])), daemon=True)
    t.start()
    t.join(timeout=2)
    if t.is_alive():
        cm.__exit__(None, None, None)
        pytest.fail("begin_tick never consulted the shutdown-aware wait")
    assert got == [None] and waits == [0.1] and ticklock._held_here is False

    def wait(s):           # no shutdown: keep polling until the restart releases
        waits.append(s)
        if len(waits) == 3:
            cm.__exit__(None, None, None)
        return False

    fd = ticklock.begin_tick(wait)
    assert fd >= 0 and len(waits) == 3 and ticklock._held_here is True
    ticklock.end_tick(fd)
    assert ticklock._held_here is False


def test_the_daemon_restarting_itself_from_its_own_tick_does_not_wait_on_itself(_lockdir):
    fd = ticklock.begin_tick(lambda s: False)
    try:
        t0 = time.monotonic()
        with ticklock.between_ticks(timeout=2) as refused:
            assert refused is None
        assert time.monotonic() - t0 < 1
    finally:
        ticklock.end_tick(fd)


# --- agents and judges are NOT the daemon's to interrupt (why CMX-199's refusal went) ---
def test_a_detached_judge_survives_the_daemon_being_killed(tmp_path):
    pidfile = tmp_path / "judge.pid"
    spawner = _py("""
        import sys, time
        from pathlib import Path
        from chela import judge
        Path(sys.argv[1]).write_text(str(judge.spawn_detached(["sleep", "30"], Path(sys.argv[2]))))
        time.sleep(60)
    """, pidfile, tmp_path / "judge.log", start_new_session=True)
    try:
        _wait_for(pidfile)
        os.killpg(spawner.pid, signal.SIGKILL)   # pm2's worst case, whole group
        spawner.wait(timeout=5)
        time.sleep(0.2)
        # a killed judge is reaped (stat gone) or a zombie (state Z) — either fails here
        assert (Path("/proc") / pidfile.read_text() / "stat").read_text().rsplit(") ", 1)[1][0] != "Z"
    finally:
        spawner.kill()
        with contextlib.suppress(OSError, ValueError):
            os.kill(int(pidfile.read_text()), signal.SIGKILL)


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed")
def test_a_tmux_agent_survives_the_daemon_being_killed(tmp_path):
    sock = f"cmx95-{os.getpid()}"
    spawner = _py("""
        import subprocess, sys, time
        from pathlib import Path
        subprocess.run(["tmux", "-L", sys.argv[1], "new-session", "-d", "-s", "a", "sleep 30"], check=True)
        Path(sys.argv[2]).touch()
        time.sleep(60)
    """, sock, tmp_path / "up", start_new_session=True)
    try:
        _wait_for(tmp_path / "up")
        os.killpg(spawner.pid, signal.SIGKILL)
        spawner.wait(timeout=5)
        time.sleep(0.2)
        cp = subprocess.run(["tmux", "-L", sock, "has-session", "-t", "a"],
                            capture_output=True, timeout=10)
        assert cp.returncode == 0, "the agent's tmux session died with the daemon"
    finally:
        subprocess.run(["tmux", "-L", sock, "kill-server"], capture_output=True, timeout=10)


def _lock_is_free() -> bool:
    p = _py("""
        import fcntl, os, sys
        from pathlib import Path
        p = Path(sys.argv[1]); p.parent.mkdir(parents=True, exist_ok=True)
        try:
            fcntl.flock(os.open(p, os.O_RDWR | os.O_CREAT), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            sys.exit(3)
    """, ticklock.lock_path())
    return p.wait(timeout=30) == 0


def _record_sh(monkeypatch):
    calls = []

    def fake_sh(args, cwd, timeout=update._SHELL_TIMEOUT_SECONDS):
        calls.append({"args": list(args), "cwd": cwd, "timeout": timeout,
                      "lock_free": _lock_is_free()})
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(update, "_sh", fake_sh)
    monkeypatch.setattr(update, "_share_notice", lambda services: "")
    return calls


def test_restarting_the_daemon_holds_the_tick_lock_through_pm2(monkeypatch, tmp_path):
    calls = _record_sh(monkeypatch)
    cp, notice = update._restart(tmp_path, ["chela-daemon", "chela-telegram"], None)
    assert cp.returncode == 0 and notice == ""
    assert calls == [{"args": ["pm2", "restart", "chela-daemon", "chela-telegram"],
                      "cwd": tmp_path, "timeout": 300, "lock_free": False}]
    assert _lock_is_free(), "the tick lock was not released after the restart"
    assert ticklock.DAEMON_TICK_WAIT_SECONDS == 60


def test_restarting_other_services_never_waits_for_the_daemon(monkeypatch, tmp_path):
    calls = _record_sh(monkeypatch)
    monkeypatch.setattr(ticklock, "between_ticks", lambda *a, **k: pytest.fail("waited"))
    cp, _ = update._restart(tmp_path, ["chela-telegram", "chela-collab"], None)
    assert cp.returncode == 0
    assert [c["args"] for c in calls] == [["pm2", "restart", "chela-telegram", "chela-collab"]]


def test_a_refused_tick_wait_never_runs_pm2_and_fails_legibly(monkeypatch, tmp_path):
    calls = _record_sh(monkeypatch)
    msg = "chela-daemon is still mid-tick after 60s — not restarting it; try again shortly"
    monkeypatch.setattr(ticklock, "between_ticks",
                        contextlib.contextmanager(lambda *a, **k: (yield msg)))
    cp, _ = update._restart(tmp_path, ["chela-daemon"], None)
    assert calls == []
    assert (cp.returncode, cp.args, cp.stderr) == (1, ["pm2", "restart", "chela-daemon"], msg)


def _inert_daemon(monkeypatch, tick):
    from tests.test_main_dispatch_log import _stub_daemon_loop_inert
    _stub_daemon_loop_inert(monkeypatch)
    monkeypatch.setattr(main, "DISPATCH_WORKFLOWS", [])
    monkeypatch.setattr(main.scheduler, "tick", tick)


def test_the_daemon_loop_holds_the_tick_lock_while_it_ticks(monkeypatch):
    seen = []
    _inert_daemon(monkeypatch, lambda: (seen.append((ticklock._held_here, _lock_is_free())), 0)[1])
    main.cmd_run(SimpleNamespace())
    assert seen == [(True, False)], "the tick ran without holding the tick lock"
    assert ticklock._held_here is False and _lock_is_free(), "the lock outlived the tick"


def test_the_daemon_releases_the_lock_even_when_a_tick_raises(monkeypatch):
    _inert_daemon(monkeypatch, lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    main.cmd_run(SimpleNamespace())
    assert _lock_is_free()


def test_the_daemon_leaves_without_ticking_when_a_restart_holds_the_lock(monkeypatch):
    ticked = []
    _inert_daemon(monkeypatch, lambda: (ticked.append(1), 0)[1])
    monkeypatch.setattr(ticklock, "begin_tick", lambda wait: None)
    main.cmd_run(SimpleNamespace())
    assert ticked == [], "the daemon started a tick under a restart"
