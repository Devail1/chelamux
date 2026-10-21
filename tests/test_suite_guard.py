"""🧠 CMX-119: the per-suite memory ceiling, verified from inside its own scope."""
from __future__ import annotations

import json
import os
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
    ("1G", "2G", G), (None, "2G", 2 * G), (None, None, 8_864_821_248), ("0", None, 0)])
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
            f"{slice_arg} -p MemoryMax=8864821248 -p MemorySwapMax=0 -p OOMPolicy=continue "
            "-- sh -c") + [
            f"cat /proc/self/cgroup > {d}/cgroup; sh -c 'uv run pytest'; rc=$?; "
            "cg=/sys/fs/cgroup$(cut -d: -f3 /proc/self/cgroup); "
            f'cat "$cg/memory.events" > {d}/events; exit $rc']
        Path(d, "cgroup").write_text("0::/app.slice/chela-suite-ffffffffffff.scope\n")
        assert check(0) is None


def test_scope_probe_pins_its_argv_and_fails_closed(monkeypatch):
    calls = []
    monkeypatch.setattr(suite_guard, "_scope_ok", False)
    monkeypatch.setattr(suite_guard.memcap, "available", lambda: True)
    monkeypatch.setattr(suite_guard.subprocess, "run", lambda *a, **k: calls.append((a, k)))
    assert suite_guard._scope_works() is True
    assert calls == [((["systemd-run", "--user", "--scope", "--collect", "-q", "--", "true"],),
                      {"check": True, "capture_output": True, "timeout": 10})]
    assert suite_guard._scope_works() is True and len(calls) == 1     # remembered
    for exc in (subprocess.CalledProcessError(1, "x"), subprocess.TimeoutExpired("x", 10),
                FileNotFoundError("systemd-run")):
        monkeypatch.setattr(suite_guard, "_scope_ok", False)
        monkeypatch.setattr(suite_guard.subprocess, "run",
                            lambda *a, _e=exc, **k: (_ for _ in ()).throw(_e))
        assert suite_guard._scope_works() is False
    monkeypatch.setattr(suite_guard.memcap, "available", lambda: False)
    monkeypatch.setattr(suite_guard.subprocess, "run", lambda *a, **k: pytest.fail("probed"))
    assert suite_guard._scope_works() is False       # no systemd-run: not even probed
    assert suite_guard._scope_works() is False       # a failure is not remembered as success


def test_a_threads_wait_is_credited_to_its_own_worktree_only(slots_on, monkeypatch):
    monkeypatch.setitem(suite_guard._thread_waited,
                        (__import__("threading").get_ident(), "/wt-a"), 1200.0)
    assert suite_guard._Ledger("/wt-a").waited == 1200.0
    assert suite_guard._Ledger("/wt-b").waited == 0.0


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



# --- admission control: at most K suites at once, judges AND agents --------------------

@pytest.fixture
def slots_on(guard_on, monkeypatch):
    monkeypatch.setattr(suite_guard, "_waited", 0.0)
    monkeypatch.setattr(suite_guard, "_thread_waited", {})
    return guard_on


@pytest.mark.parametrize("env,stored,want", [
    ("3", "2", 3), (None, 2, 2), (None, "5", 5), ("0", 2, 0), (None, 0, 0),
    ("lots", 2, 2), ("-1", None, 7), (None, True, 7), (None, 1.5, 7), (None, None, 7)])
def test_slot_precedence(slots_on, monkeypatch, env, stored, want):
    monkeypatch.delenv("CHELA_SUITE_SLOTS", raising=False)
    monkeypatch.setattr(suite_guard, "default_slots", lambda: 7)
    if env is not None:
        monkeypatch.setenv("CHELA_SUITE_SLOTS", env)
    if stored is not None:
        userconfig.set_("suite_slots", stored)
    assert suite_guard.slots() == want


@pytest.mark.parametrize("total,want", [
    (16 * G, 2), (17 * G, 3), (64 * G, 4), (1024 * G, 4), (2 * G, 1), (None, 1),
    (0, 1)])
def test_default_slots_from_ram_and_the_measured_peak(monkeypatch, total, want):
    monkeypatch.setattr(suite_guard, "_mem_total_bytes", lambda: total)
    assert suite_guard.default_slots() == want


def test_mem_total_reads_meminfo_and_fails_closed(tmp_path):
    f = tmp_path / "meminfo"
    f.write_text("MemFree:  10 kB\nMemTotal:       16375676 kB\n")
    assert suite_guard._mem_total_bytes(f) == 16375676 * 1024
    f.write_text("MemTotal: lots kB\n")
    assert suite_guard._mem_total_bytes(f) is None
    f.write_text("MemFree: 10 kB\n")
    assert suite_guard._mem_total_bytes(f) is None
    assert suite_guard._mem_total_bytes(tmp_path / "missing") is None


def _holder(k_inside, entered, release, errors, **kw):
    def body():
        try:
            with suite_guard.slot("/wt", poll=0.01, **kw):
                k_inside.append(1)
                entered.set()
                release.wait(10)
                k_inside.pop()
        except BaseException as e:   # noqa: BLE001 — reported by the test
            errors.append(e)
    return body


def test_the_semaphore_admits_k_and_queues_the_rest(slots_on, monkeypatch, capsys):
    import threading
    monkeypatch.setenv("CHELA_SUITE_SLOTS", "2")
    inside, errors, release = [], [], threading.Event()
    gates = [threading.Event() for _ in range(3)]
    waits = []
    threads = [threading.Thread(target=_holder(inside, g, release, errors,
                                               on_wait=waits.append)) for g in gates]
    for t, g in zip(threads[:2], gates[:2]):
        t.start()
        assert g.wait(5)
    threads[2].start()
    time.sleep(0.3)
    assert len(inside) == 2 and not gates[2].is_set()
    [(path, rec)] = [lr for lr in suite_guard._ledgers()]
    assert rec["waiting_since"] is not None and rec["cwd"] == "/wt"
    assert "waiting for a suite slot — all 2 busy: wt (pid" in capsys.readouterr().err
    release.set()
    for t in threads:
        t.join(10)
    assert gates[2].is_set() and not errors
    assert len(waits) == 2 and waits[0] is not None and waits[1] is None
    assert json.loads(path.read_text())["waiting_since"] is None
    assert json.loads(path.read_text())["waited"] > 0.2


def test_slots_off_is_a_passthrough(slots_on, monkeypatch):
    monkeypatch.setenv("CHELA_SUITE_SLOTS", "0")
    monkeypatch.setattr(suite_guard, "_take", lambda *a: pytest.fail("took a slot"))
    with suite_guard.slot("/wt"):
        pass
    assert not (slots_on / "suite-slots").exists()


def test_a_slot_is_written_with_its_holder_and_freed_on_exit(slots_on, monkeypatch):
    import fcntl
    monkeypatch.setenv("CHELA_SUITE_SLOTS", "1")
    lock = slots_on / "suite-slots" / "slot-0"
    with suite_guard.slot("/some/wt"):
        rec = json.loads(lock.read_text())
        assert (rec["pid"], rec["cwd"]) == (os.getpid(), "/some/wt")
        fd = os.open(lock, os.O_RDWR)
        with pytest.raises(BlockingIOError):
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.close(fd)
    fd = os.open(lock, os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)      # free again
    os.close(fd)


def test_a_wait_past_max_wait_raises_slot_timeout(slots_on, monkeypatch):
    import threading
    monkeypatch.setenv("CHELA_SUITE_SLOTS", "1")
    inside, errors, release, gate = [], [], threading.Event(), threading.Event()
    t = threading.Thread(target=_holder(inside, gate, release, errors))
    t.start()
    assert gate.wait(5)
    try:
        with pytest.raises(suite_guard.SlotTimeout, match="no suite slot came free in 0s"):
            with suite_guard.slot("/wt", max_wait=0.2, poll=0.01):
                pytest.fail("admitted past a held slot")
        assert isinstance(suite_guard.SlotTimeout("x"), OSError)
    finally:
        release.set()
        t.join(10)


def test_a_waiter_goes_before_a_newcomer(slots_on, monkeypatch, tmp_path):
    """FIFO: a run already queued is not overtaken by one that just freed its slot."""
    monkeypatch.setenv("CHELA_SUITE_SLOTS", "1")
    waits = slots_on / "suite-slots" / "waits"
    waits.mkdir(parents=True)
    start = suite_guard._proc_stat(os.getpid())[1]
    older = waits / "1-1.json"
    older.write_text(json.dumps({"pid": os.getpid(), "start": start, "cwd": "/x",
                                 "waiting_since": 10.0, "waited": 0}))
    me = waits / "1-2.json"
    assert suite_guard._must_queue(me, 20.0) is True
    assert suite_guard._must_queue(me, 5.0) is False            # I am the older one
    assert suite_guard._must_queue(older, 20.0) is False         # never behind myself
    older.write_text(json.dumps({"pid": os.getpid(), "start": start, "cwd": "/x",
                                 "waiting_since": None, "waited": 3}))
    assert suite_guard._must_queue(me, 20.0) is False            # it holds a slot now
    with pytest.raises(suite_guard.SlotTimeout):
        older.write_text(json.dumps({"pid": os.getpid(), "start": start, "cwd": "/x",
                                     "waiting_since": 0.0, "waited": 0}))
        with suite_guard.slot("/wt", max_wait=0.1, poll=0.01):  # a free slot, but queued
            pytest.fail("overtook an older waiter")


def test_a_stopped_waiter_does_not_hold_the_line(slots_on, monkeypatch):
    waits = slots_on / "suite-slots" / "waits"
    waits.mkdir(parents=True)
    (waits / "9-9.json").write_text(json.dumps({"pid": 9, "start": "s", "cwd": "/x",
                                                "waiting_since": 1.0, "waited": 0}))
    for state, queued in (("T", False), ("t", False), ("S", True), ("R", True)):
        monkeypatch.setattr(suite_guard, "_proc_stat", lambda pid, _s=state: (_s, "s"))
        assert suite_guard._must_queue(waits / "me.json", 2.0) is queued


def test_ledgers_prune_the_dead_and_the_reused_and_skip_garbage(slots_on, monkeypatch):
    waits = slots_on / "suite-slots" / "waits"
    waits.mkdir(parents=True)
    rec = {"cwd": "/x", "waiting_since": 1.0, "waited": 0}
    (waits / "dead.json").write_text(json.dumps({**rec, "pid": 1, "start": "a"}))
    (waits / "reused.json").write_text(json.dumps({**rec, "pid": 2, "start": "old"}))
    (waits / "live.json").write_text(json.dumps({**rec, "pid": 3, "start": "b"}))
    (waits / "garbage.json").write_text("{not json")
    (waits / "nopid.json").write_text(json.dumps(rec))
    stats = {2: ("S", "new"), 3: ("S", "b")}
    monkeypatch.setattr(suite_guard, "_proc_stat", lambda pid: stats.get(pid))
    assert [p.name for p, _ in suite_guard._ledgers()] == ["live.json"]
    assert sorted(p.name for p in waits.iterdir()) == ["garbage.json", "live.json",
                                                       "nopid.json"]


def test_proc_stat_reads_state_and_start_and_none_when_gone():
    state, start = suite_guard._proc_stat(os.getpid())
    assert state in "RS" and start.isdigit()
    assert suite_guard._proc_stat(2 ** 22 + 11) is None


def test_waits_in_credits_only_this_worktree(slots_on, monkeypatch, tmp_path):
    wt = tmp_path / "wt"
    (wt / "sub").mkdir(parents=True)
    waits = slots_on / "suite-slots" / "waits"
    waits.mkdir(parents=True)
    monkeypatch.setattr(suite_guard, "_proc_stat", lambda pid: ("S", "s"))
    rows = [(str(wt), 30.0, 5.0), (str(wt / "sub"), None, 7.0), (str(tmp_path), 1.0, 99.0),
            (str(tmp_path / "wt2"), 2.0, 50.0)]
    for i, (cwd, since, waited) in enumerate(rows):
        (waits / f"{i}.json").write_text(json.dumps({"pid": i, "start": "s", "cwd": cwd,
                                                     "waiting_since": since,
                                                     "waited": waited}))
    assert suite_guard.waits_in(wt) == {"waiting_since": 30.0, "waited": 12.0}
    assert suite_guard.waits_in(tmp_path / "elsewhere") is None
    (waits / "bad.json").write_text(json.dumps({"pid": 9, "start": "s", "cwd": str(wt),
                                                "waiting_since": "soon", "waited": 0}))
    assert suite_guard.waits_in(wt) == {"waiting_since": 30.0, "waited": 12.0}


def test_ledger_write_never_raises(slots_on, monkeypatch, caplog):
    import logging
    caplog.set_level(logging.DEBUG, logger="chela.suite_guard")
    (slots_on / "suite-slots").write_text("a file where the dir should be")
    suite_guard._Ledger("/wt").write(1.0)
    assert "could not publish the slot wait" in caplog.text


def test_take_closes_its_fd_on_an_unexpected_error(slots_on, monkeypatch):
    d = slots_on / "s"
    d.mkdir()
    closed = []
    real_close = os.close
    monkeypatch.setattr(suite_guard.os, "close", lambda fd: (closed.append(fd), real_close(fd)))
    monkeypatch.setattr(suite_guard.fcntl, "flock",
                        lambda fd, op: (_ for _ in ()).throw(PermissionError("nope")))
    with pytest.raises(PermissionError):
        suite_guard._take(d, 2)
    assert len(closed) == 1


def test_holders_names_who_holds_and_survives_garbage(tmp_path):
    (tmp_path / "slot-0").write_text(json.dumps({"pid": 7, "cwd": "/w/CMX-12-r1",
                                                 "since": time.time() - 250}))
    (tmp_path / "slot-1").write_text("{garbled")
    assert suite_guard._holders(tmp_path, 3) == "CMX-12-r1 (pid 7, 4m)"
    assert suite_guard._holders(tmp_path / "none", 2) == "holders unknown"


def test_a_slot_wait_is_not_spent_budget(slots_on, monkeypatch):
    from chela import sweep
    monkeypatch.setattr(suite_guard, "_waited", 100.0)
    for clock in (suite_guard.busy_clock, judge._now, sweep._now):
        assert abs(time.monotonic() - 100.0 - clock()) < 1.0


def test_run_suite_takes_a_slot_with_its_cwd_hook_and_bounded_wait(guard_on, monkeypatch):
    import contextlib
    seen = []

    @contextlib.contextmanager
    def fake_slot(cwd, on_wait=None, max_wait=None):
        seen.append((cwd, on_wait, max_wait))
        yield
    hook = object()
    monkeypatch.setattr(judge.suite_guard, "slot", fake_slot)
    monkeypatch.setitem(judge._run_ctx, "on_slot_wait", hook)
    assert judge.run_suite("true", guard_on, timeout=30).green
    assert seen == [(guard_on, hook, 120)]


def test_run_suite_that_never_gets_a_slot_is_cannot_verify(guard_on, monkeypatch):
    import contextlib

    @contextlib.contextmanager
    def no_slot(cwd, on_wait=None, max_wait=None):
        raise suite_guard.SlotTimeout("no suite slot came free in 120s (all 1 busy: x)")
        yield
    monkeypatch.setattr(judge.suite_guard, "slot", no_slot)
    result = judge.run_suite("true", guard_on, timeout=30)
    assert (result.ok, result.green) == (False, False)
    assert result.detail == ("the suite could not be run: no suite slot came free in 120s "
                             "(all 1 busy: x)")


# --- a queued run is waiting, never stuck (CMX-110) --------------------------------------

def _inbox_run():
    return {"task_id": "T9", "title": "t", "status": "running", "branch_name": "cmx-9",
            "pr_url": "", "pr_state": "open"}


def _inbox_tick(store, now, *, selfcheck=None, slot_wait=None, activity=0.0):
    from chela import inbox
    probe = {"T9": {"activity": activity, "markers": {}, "selfcheck": selfcheck,
                    "slot_wait": slot_wait}}
    return [e["kind"] for e in inbox.stuck_events([_inbox_run()], store, probe, now)]


@pytest.fixture
def inbox_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CHELA_INBOX_FILE", str(tmp_path / "inbox.json"))
    monkeypatch.setenv("CHELA_EVENTS_FILE", str(tmp_path / "events.jsonl"))
    monkeypatch.setenv("CHELA_STUCK_AGENT_ALERT_S", "1200")
    monkeypatch.setenv("CHELA_SELFCHECK_ALERT_S", "3600")
    from chela import inbox
    return inbox._empty()


def test_a_queued_self_check_past_the_ceiling_gets_no_notice(inbox_env):
    sc = {"what": "task-finished self-check", "since": 0.0, "pid": 5}
    assert _inbox_tick(inbox_env, 5000.0, selfcheck=sc,
                       slot_wait={"waiting_since": 100.0, "waited": 0.0}) == []
    assert _inbox_tick(inbox_env, 5000.0, selfcheck=sc) == ["run_selfcheck_long"]


def test_time_spent_queued_is_not_running_time(inbox_env):
    """5000s since the self-check started, 1500s of it queued: 3500s run, under the 3600s
    ceiling. 1000s queued: 4000s run, over it."""
    sc = {"what": "task-finished self-check", "since": 0.0, "pid": 5}
    assert _inbox_tick(inbox_env, 5000.0, selfcheck=sc,
                       slot_wait={"waiting_since": None, "waited": 1500.0}) == []
    assert _inbox_tick(inbox_env, 5000.0, selfcheck=sc,
                       slot_wait={"waiting_since": None, "waited": 1000.0}) == [
                           "run_selfcheck_long"]


def test_an_open_wait_counts_as_queued_time(inbox_env):
    sc = {"what": "task-finished self-check", "since": 0.0, "pid": 5}
    _inbox_tick(inbox_env, 5000.0, selfcheck=sc,
                slot_wait={"waiting_since": 2000.0, "waited": 0.0})
    entry = inbox_env["stuck"]["T9"]
    assert "selfcheck" not in entry and entry["active_at"] == 5000.0


def test_a_queued_run_with_no_self_check_seen_is_never_stuck(inbox_env):
    old = {"activity": 1.0}
    assert _inbox_tick(inbox_env, 99_999.0, **old,
                       slot_wait={"waiting_since": 10.0, "waited": 0.0}) == []
    assert _inbox_tick(inbox_env, 99_999.0 + 1000, **old) == []     # its active_at moved
    assert _inbox_tick(inbox_env, 99_999.0 + 1300, **old) == ["run_stuck"]


def test_stuck_probes_carry_the_worktrees_slot_wait(tmp_path, monkeypatch):
    from chela import inbox
    wt = tmp_path / "wt"
    wt.mkdir()
    monkeypatch.setattr(inbox.transcripts, "transcript_for_cwd", lambda cwd: None)
    monkeypatch.setattr(inbox, "_git_last_commit", lambda w: None)
    monkeypatch.setattr(inbox, "_selfcheck_scan", lambda: [])
    monkeypatch.setattr(inbox.suite_guard, "waits_in",
                        lambda w: {"waiting_since": 1.0, "waited": 2.0} if w == str(wt) else 0)
    run = {**_inbox_run(), "worktree_path": str(wt)}
    assert inbox.stuck_probes([run])["T9"]["slot_wait"] == {"waiting_since": 1.0, "waited": 2.0}


def test_vitest_is_capped_at_4_workers():
    """The measured peak (SUITE_PEAK_BYTES) assumes it: Vitest's default on a 20-core box was
    19 jsdom workers and 4.3 GB on its own."""
    config = Path(__file__).resolve().parent.parent / "chela/dashboard/frontend/vite.config.ts"
    assert "maxWorkers: 4 }" in config.read_text()
