"""Daemons must shut down on a signal WITHOUT a traceback.

Real incident, 2026-07-13: every `pm2 restart chela-daemon` left a KeyboardInterrupt
traceback in the error log, pointing at whatever line the 30s loop happened to be on
when the signal landed — which was the inbox tick. Reading `↺4` restarts plus a stack
trace at the new inbox code, the obvious conclusion was "the inbox is crash-looping".
It wasn't; those were the operator's own restarts. Noise that masks real errors is a
bug in its own right, and this one actively blamed innocent code.

So the contract is behavioural, not cosmetic, and the last test here checks it the only
way that counts: it runs the REAL `chela run` daemon as a subprocess, signals it like
pm2 does, and demands exit 0, a clean line, and no traceback. (Green unit tests are what
let the original bug ship.)

No tmux is touched: the daemon is pointed at a scratch session name it only ever READS.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time

import pytest

from chela.main import GracefulShutdown


@pytest.fixture(autouse=True)
def restore_signal_handlers():
    """Install/restore around each test — never leave pytest's own handlers clobbered."""
    saved = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    yield
    for s, handler in saved.items():
        signal.signal(s, handler)


# --- the primitive -------------------------------------------------------------

@pytest.mark.parametrize("sig,name", [(signal.SIGTERM, "SIGTERM"), (signal.SIGINT, "SIGINT")])
def test_a_signal_sets_the_flag_and_raises_nothing(sig, name):
    # The whole point: the signal must RAISE NOTHING. Python's default SIGINT handler
    # raises KeyboardInterrupt wherever the interpreter happens to be — that is the
    # traceback. Ours sets an Event instead.
    stop = GracefulShutdown("test").install()
    assert stop.stopping is False

    os.kill(os.getpid(), sig)      # would be a KeyboardInterrupt under the default handler
    time.sleep(0.05)               # let the handler run

    assert stop.stopping is True
    assert stop.signame == name


def test_wait_returns_immediately_once_a_signal_arrives():
    # `wait` replaces `time.sleep`, so a daemon signalled during its 30s nap leaves NOW
    # rather than sleeping out the rest and being SIGKILLed by pm2's grace timer.
    stop = GracefulShutdown("test").install()
    os.kill(os.getpid(), signal.SIGTERM)

    started = time.monotonic()
    assert stop.wait(30) is True                      # the nap the daemon would be in
    assert time.monotonic() - started < 1.0           # ...returned at once, not in 30s


def test_wait_sleeps_normally_when_no_signal_arrives():
    # No signal → `wait` is a plain sleep: it blocks for the whole nap and reports False
    # ("nobody woke me"). Asserted WITHOUT timing the sleep on purpose. The old version
    # slept 0.2s and asserted `monotonic() - started >= 0.15`, which failed ~1 run in 5
    # on a loaded box: `Event.wait` derives its deadline from the SYSTEM clock while the
    # test measured the MONOTONIC one, so a resync between the two makes a full sleep
    # measure short. Park a long nap in a thread and prove it is still parked instead —
    # the margin is then the entire 30s nap rather than 50ms of scheduler jitter.
    stop = GracefulShutdown("test").install()
    assert stop.wait(0.01) is False

    naps: list[bool] = []
    napper = threading.Thread(target=lambda: naps.append(stop.wait(30)), daemon=True)
    napper.start()

    napper.join(0.2)
    assert napper.is_alive(), "wait() returned early — it did not sleep"
    assert stop.stopping is False

    os.kill(os.getpid(), signal.SIGTERM)              # release it the way a daemon is released
    napper.join(5)
    assert not napper.is_alive(), "wait() slept through its wake-up signal"
    assert naps == [True]


def test_a_second_signal_does_not_raise():
    # A double Ctrl-C must not resurrect the traceback on the way out the door.
    stop = GracefulShutdown("test").install()
    os.kill(os.getpid(), signal.SIGINT)
    os.kill(os.getpid(), signal.SIGINT)
    time.sleep(0.05)
    assert stop.stopping is True


# --- the real daemon, signalled the way pm2 signals it -------------------------

@pytest.mark.parametrize("sig,name", [(signal.SIGTERM, "SIGTERM"), (signal.SIGINT, "SIGINT")])
def test_the_real_daemon_exits_cleanly_with_no_traceback(tmp_path, sig, name):
    env = {
        **os.environ,
        "CHELA_DIR": str(tmp_path),
        "CHELA_TMUX_SESSION": "shutdown-test-scratch",  # read-only; never created
        "CHELA_INBOX_ENABLED": "false",                 # keep the tick cheap
        "CHELA_SCHEDULER_POLL_INTERVAL": "30",          # a long nap to be interrupted
        "PYTHONUNBUFFERED": "1",
    }
    env.pop("CHELA_NOTIFY_URL", None)
    proc = subprocess.Popen(
        [sys.executable, "-m", "chela.main", "run"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        # Wait until it is actually up (and its handlers are installed) before signalling.
        deadline = time.time() + 20
        while time.time() < deadline:
            if proc.poll() is not None:
                break
            time.sleep(0.2)
            if time.time() > deadline - 17:   # ~3s of startup is plenty
                break

        proc.send_signal(sig)                 # exactly what `pm2 restart` does
        out, _ = proc.communicate(timeout=15) # must exit on its own, well inside the
    except subprocess.TimeoutExpired:         # grace period, or pm2 SIGKILLs it
        proc.kill()
        raise AssertionError("daemon did not exit on signal — pm2 would SIGKILL it")
    finally:
        if proc.poll() is None:
            proc.kill()

    assert "Traceback" not in out, f"traceback on shutdown:\n{out}"
    assert "KeyboardInterrupt" not in out
    assert f"shutting down ({name})" in out, f"no clean shutdown line:\n{out}"
    assert proc.returncode == 0, f"exit {proc.returncode}, expected 0:\n{out}"
