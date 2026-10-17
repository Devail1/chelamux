"""🧠 CMX-119: the per-suite memory ceiling, verified from inside its own scope."""
from __future__ import annotations

import shlex
import subprocess
import sys
import time
from pathlib import Path

import pytest

from chela import judge, suite_guard, userconfig

G = 1024 ** 3


@pytest.fixture
def guard_on(monkeypatch, tmp_path):
    monkeypatch.setattr(suite_guard.config, "CHELA_DIR", tmp_path)
    monkeypatch.delenv("CHELA_SUITE_MEMORY_MAX", raising=False)
    return tmp_path


# --- configuration: env > config.json > default ----------------------------------------

@pytest.mark.parametrize("env,stored,want", [
    ("1G", "2G", G), (None, "2G", 2 * G), (None, None, 5_476_083_300), ("0", None, 0)])
def test_ceiling_precedence(guard_on, monkeypatch, env, stored, want):
    if env is not None:
        monkeypatch.setenv("CHELA_SUITE_MEMORY_MAX", env)
    if stored is not None:
        userconfig.set_("suite_memory_max", stored)
    assert suite_guard.ceiling_bytes() == want


# --- the ceiling: verified from INSIDE the scope, never from the wrapper's exit --------

SCOPE = "0::/user.slice/user@1000.service/app.slice/chela-suite-abc.scope\n"


@pytest.mark.parametrize("cgroup,events,rc,want", [
    (SCOPE, "oom 0\noom_kill 0\n", 0, None),
    (SCOPE, "oom 1\noom_kill 1\n", 0, "suite exceeded 6.0G"),
    (SCOPE, None, 137, "suite exceeded 6.0G"),
    (SCOPE, None, -9, "suite exceeded 6.0G"),
    (SCOPE, None, 1, None),
    (SCOPE, "not a key value line\n", 137, "suite exceeded 6.0G"),
    (SCOPE, "oom_kill many\n", 1, None),
    ("0::/app.slice/tmux-spawn-1.scope\n", "oom_kill 0\n", 0, "ceiling was NOT applied"),
    ("0::/app.slice/chela-suite-abcd.scope\n", "oom_kill 0\n", 0, "ceiling was NOT applied"),
    (None, None, 0, "ceiling was NOT applied"),
])
def test_verdict(tmp_path, cgroup, events, rc, want):
    for name, text in (("cgroup", cgroup), ("events", events)):
        if text is not None:
            (tmp_path / name).write_text(text)
    got = suite_guard.verdict("chela-suite-abc", 6 * G, tmp_path, rc)
    assert got is None if want is None else want in got


def test_bounded_is_a_passthrough_when_the_ceiling_is_off(guard_on, monkeypatch):
    monkeypatch.setenv("CHELA_SUITE_MEMORY_MAX", "0")
    monkeypatch.setattr(suite_guard, "_scope_works", lambda: pytest.fail("probed"))
    with suite_guard.bounded("uv run pytest") as (cmd, check):
        assert (cmd, check(137)) == ("uv run pytest", None)


def test_bounded_is_a_passthrough_without_a_working_scope(guard_on, monkeypatch):
    monkeypatch.setattr(suite_guard, "_scope_works", lambda: False)
    with suite_guard.bounded("uv run pytest") as (cmd, check):
        assert (cmd, check(137)) == ("uv run pytest", None)


@pytest.mark.parametrize("budget,slice_arg", [(0, ""), (8 * G, " --slice=chela-agents.slice")])
def test_bounded_pins_the_full_scope_argv(guard_on, monkeypatch, budget, slice_arg):
    monkeypatch.setattr(suite_guard, "_scope_works", lambda: True)
    monkeypatch.setattr(suite_guard.config, "memory_slice_budget_bytes", lambda: budget)
    monkeypatch.setattr(suite_guard.uuid, "uuid4", lambda: type("U", (), {"hex": "f" * 32}))
    with suite_guard.bounded("uv run pytest") as (cmd, check):
        argv = shlex.split(cmd)
        d = argv[-1].split("cat /proc/self/cgroup > ", 1)[1].split("/cgroup;", 1)[0]
        assert argv == shlex.split(
            "systemd-run --user --scope --collect -q --unit=chela-suite-ffffffffffff"
            f"{slice_arg} -p MemoryMax=5476083300 -p MemorySwapMax=0 -p OOMPolicy=continue "
            "-- sh -c") + [
            f"cat /proc/self/cgroup > {d}/cgroup; sh -c 'uv run pytest'; rc=$?; "
            "cg=/sys/fs/cgroup$(cut -d: -f3 /proc/self/cgroup); "
            f'cat "$cg/memory.events" > {d}/events; exit $rc']
        Path(d, "cgroup").write_text("0::/app.slice/chela-suite-ffffffffffff.scope\n")
        assert check(0) is None


def test_scope_probe_pins_its_argv_and_fails_closed(monkeypatch):
    calls = []
    monkeypatch.setattr(suite_guard.memcap, "available", lambda: True)
    monkeypatch.setattr(suite_guard.subprocess, "run", lambda *a, **k: calls.append((a, k)))
    assert suite_guard._scope_works() is True
    assert calls == [((["systemd-run", "--user", "--scope", "--collect", "-q", "--", "true"],),
                      {"check": True, "capture_output": True, "timeout": 10})]
    for exc in (subprocess.CalledProcessError(1, "x"), subprocess.TimeoutExpired("x", 10),
                FileNotFoundError("systemd-run")):
        monkeypatch.setattr(suite_guard.subprocess, "run",
                            lambda *a, _e=exc, **k: (_ for _ in ()).throw(_e))
        assert suite_guard._scope_works() is False
    monkeypatch.setattr(suite_guard.memcap, "available", lambda: False)
    assert suite_guard._scope_works() is False


def test_a_suite_over_its_ceiling_is_cannot_verify_never_a_pass(guard_on, monkeypatch):
    import contextlib

    @contextlib.contextmanager
    def breached(test_cmd):
        yield test_cmd, lambda rc: "suite exceeded 6.0G (its memory ceiling, CMX-119)"
    monkeypatch.setattr(judge.suite_guard, "bounded", breached)
    result = judge.run_suite("true", guard_on)
    assert (result.ok, result.green, result.exit_code) == (False, False, 0)
    assert result.detail == "suite exceeded 6.0G (its memory ceiling, CMX-119)"


@pytest.mark.skipif(not suite_guard._scope_works(), reason="needs a systemd --user session")
def test_the_real_scope_kills_a_runaway_and_names_itself(guard_on, monkeypatch):
    monkeypatch.setenv("CHELA_SUITE_MEMORY_MAX", "100M")
    hog = f"{sys.executable} -c 'b=[]\nwhile 1: b.append(bytearray(1 << 24))'"
    over = judge.run_suite(hog, guard_on, timeout=60)
    assert not over.ok and "suite exceeded 100.0M" in over.detail
    ok = judge.run_suite("exit 0", guard_on, timeout=60)
    assert ok.green and ok.detail == ""


@pytest.mark.skipif(not suite_guard._scope_works(), reason="needs a systemd --user session")
def test_the_real_scope_reaps_what_the_suite_leaked(guard_on, tmp_path):
    pidfile = tmp_path / "leak.pid"
    assert judge.run_suite(f"sleep 300 >/dev/null 2>&1 & echo $! > {pidfile}", guard_on).green
    pid = int(pidfile.read_text())
    deadline = time.time() + 5
    while Path(f"/proc/{pid}").exists() and time.time() < deadline:
        time.sleep(0.05)
    assert not Path(f"/proc/{pid}").exists()


def test_reap_pins_its_argv_and_never_raises(monkeypatch, caplog):
    calls = []
    monkeypatch.setattr(suite_guard.subprocess, "run", lambda *a, **k: calls.append((a, k)))
    suite_guard._reap("chela-suite-abc")
    assert calls == [((["systemctl", "--user", "kill", "--signal=SIGKILL",
                        "chela-suite-abc.scope"],), {"capture_output": True, "timeout": 10})]
    monkeypatch.setattr(suite_guard.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("x", 10)))
    suite_guard._reap("chela-suite-abc")
    assert "could not reap chela-suite-abc.scope" in caplog.text

