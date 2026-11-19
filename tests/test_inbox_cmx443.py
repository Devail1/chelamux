"""CMX-443: the 🧊 STUCK notice names a process blocked reading a stdin nobody writes.

An agent's ``$(cat <<'EOF' …)`` commit message broke on a quote, and ``cat`` sat on the
background shell's never-closing stdin for over an hour; the STUCK notice could not say why.
"""
from __future__ import annotations

import calendar
import subprocess
import sys
import time
from pathlib import Path

import pytest

from chela import config, inbox, stdin_blocked

_HZ = 100
BTIME = 1_000_000


def _run(**over):
    return {"task_id": "T9", "title": "fix the parser", "status": "running",
            "branch_name": "cmx-9", "pr_url": "https://github.com/o/r/pull/9",
            "pr_state": "open", **over}


def _fake_proc(root: Path, procs: list[dict]) -> None:
    """A ``/proc`` holding, per process, the files the scan reads: ``stat`` (comm, state,
    ppid, starttime), ``wchan``, an ``fd/0`` link and a ``cwd`` link."""
    root.mkdir(exist_ok=True)
    (root / "stat").write_text(f"cpu  1 2 3\nbtime {BTIME}\nprocesses 9\n")
    for p in procs:
        d = root / str(p["pid"])
        d.mkdir()
        rest = [p.get("state", "S"), str(p.get("ppid", 1))] + ["0"] * 17 + [
            str(p.get("ticks", 0))] + ["0"] * 5                 # index 19 = starttime
        (d / "stat").write_text(f"{p['pid']} ({p['comm']}) " + " ".join(rest) + "\n")
        if "wchan" in p:
            (d / "wchan").write_text(p["wchan"])
        (d / "fd").mkdir()
        if "fd0" in p:
            (d / "fd" / "0").symlink_to(p["fd0"])
        if "cwd" in p:
            (d / "cwd").symlink_to(p["cwd"])


@pytest.fixture
def fake_proc(tmp_path, monkeypatch):
    monkeypatch.setattr(stdin_blocked, "_PROC", tmp_path / "proc")
    monkeypatch.setattr(stdin_blocked.os, "sysconf", lambda name: _HZ)
    wt = tmp_path / "wt"
    wt.mkdir()
    return tmp_path / "proc", wt.resolve()


def _cat(wt, **over):
    return {"pid": 500, "comm": "cat", "ppid": 400, "wchan": "anon_pipe_read",
            "fd0": "pipe:[724535199]", "cwd": wt, "ticks": 300, **over}


_BASH = {"pid": 400, "comm": "bash", "ppid": 1, "wchan": "do_wait", "fd0": "pipe:[1]"}


# --- the scan --------------------------------------------------------------------------


def test_the_scan_finds_a_leaf_cat_reading_a_pipe(fake_proc):
    proc, wt = fake_proc
    _fake_proc(proc, [_BASH | {"cwd": wt}, _cat(wt)])
    assert stdin_blocked.scan() == [{"pid": 500, "comm": "cat", "ppid": 400, "parent": "bash",
                                     "cwd": wt, "started": BTIME + 3.0}]


@pytest.mark.parametrize("wchan,fd0", [
    ("pipe_read", "pipe:[9]"), ("anon_pipe_read", "pipe:[9]"),
    ("n_tty_read", "/dev/pts/20"), ("wait_woken", "/dev/pts/20"), ("wait_woken", "/dev/tty1"),
])
def test_each_stdin_read_wait_is_found(fake_proc, wchan, fd0):
    proc, wt = fake_proc
    _fake_proc(proc, [_cat(wt, wchan=wchan, fd0=fd0)])
    assert [p["pid"] for p in stdin_blocked.scan()] == [500]


@pytest.mark.parametrize("over", [
    {"wchan": "pipe_write"},                            # a busy writer: blocked on OUTPUT
    {"wchan": "anon_pipe_write"},
    {"wchan": "hrtimer_nanosleep"},                     # `sleep`
    {"wchan": "do_epoll_wait"},                         # an event loop
    {"wchan": "wait_woken", "fd0": "pipe:[9]"},         # a tty wait with a pipe stdin
    {"wchan": "anon_pipe_read", "fd0": "/dev/pts/3"},   # a pipe wait with a tty stdin
    {"fd0": "/dev/null"},
    {"fd0": "socket:[77]"},
    {"fd0": "/home/x/file.txt"},
    {"state": "R"},                                     # running, not sleeping
    {"state": "D"},
], ids=lambda o: "-".join(f"{k}={v}" for k, v in o.items()))
def test_a_process_not_waiting_on_its_stdin_is_not_found(fake_proc, over):
    proc, wt = fake_proc
    _fake_proc(proc, [_cat(wt, **over)])
    assert stdin_blocked.scan() == []


def test_a_parent_is_never_the_culprit_only_the_leaf(fake_proc):
    """🔴 GUARD: a shell reading its pipe while it has a child is not reported — the leaf is."""
    proc, wt = fake_proc
    _fake_proc(proc, [_BASH | {"cwd": wt, "wchan": "anon_pipe_read"}, _cat(wt)])
    assert [p["pid"] for p in stdin_blocked.scan()] == [500]


def test_a_zombie_child_does_not_hide_its_blocked_parent(fake_proc):
    proc, wt = fake_proc
    _fake_proc(proc, [_cat(wt), {"pid": 600, "comm": "true", "ppid": 500, "state": "Z"}])
    assert [p["pid"] for p in stdin_blocked.scan()] == [500]


def test_a_live_child_makes_its_parent_not_a_leaf(fake_proc):
    proc, wt = fake_proc
    _fake_proc(proc, [_cat(wt), {"pid": 600, "comm": "true", "ppid": 500, "state": "S"}])
    assert stdin_blocked.scan() == []


@pytest.mark.parametrize("missing", ["wchan", "fd0", "cwd"])
def test_an_unreadable_file_skips_that_process_only(fake_proc, missing):
    proc, wt = fake_proc
    broken = _cat(wt, pid=501)
    del broken[missing]
    _fake_proc(proc, [broken, _cat(wt, ppid=1)])
    assert [p["pid"] for p in stdin_blocked.scan()] == [500]


def test_a_garbled_stat_skips_that_process_only(fake_proc):
    proc, wt = fake_proc
    _fake_proc(proc, [_cat(wt), _cat(wt, pid=501)])
    (proc / "501" / "stat").write_text("501 (cat) S\n")
    assert [p["pid"] for p in stdin_blocked.scan()] == [500]


def test_an_unreadable_proc_finds_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(stdin_blocked, "_PROC", tmp_path / "nope")
    assert stdin_blocked.scan() == []


def test_no_btime_leaves_the_start_unknown(fake_proc):
    proc, wt = fake_proc
    _fake_proc(proc, [_cat(wt)])
    (proc / "stat").write_text("cpu 1 2 3\n")
    assert stdin_blocked.scan()[0]["started"] is None


def test_a_comm_with_spaces_and_parens_is_read_whole(fake_proc):
    proc, wt = fake_proc
    _fake_proc(proc, [_cat(wt, comm="my (odd) cmd")])
    assert stdin_blocked.scan()[0]["comm"] == "my (odd) cmd"


def test_a_parent_not_in_the_table_reads_as_none(fake_proc):
    proc, wt = fake_proc
    _fake_proc(proc, [_cat(wt, ppid=12345)])
    assert stdin_blocked.scan()[0]["parent"] is None


# --- per worktree -----------------------------------------------------------------------


def _p(pid, cwd, started):
    return {"pid": pid, "comm": "cat", "ppid": 1, "parent": "bash", "cwd": cwd,
            "started": started}


def test_for_worktree_keeps_its_own_old_enough_processes_oldest_first(tmp_path):
    wt = (tmp_path / "wt")
    (wt / "sub").mkdir(parents=True)
    wt = wt.resolve()
    other = tmp_path / "wt-other"
    procs = [_p(1, wt, 700.0), _p(2, wt / "sub", 600.0), _p(3, other, 100.0),
             _p(4, tmp_path, 100.0), _p(5, wt, 701.0), _p(6, wt, None)]
    got = stdin_blocked.for_worktree(str(wt), procs, now=1000.0, min_age=300)
    assert [p["pid"] for p in got] == [2, 1]     # 5 is 299s old; 6 has no known start


def test_the_age_floor_is_inclusive(tmp_path):
    procs = [_p(1, tmp_path.resolve(), 700.0)]
    assert stdin_blocked.for_worktree(str(tmp_path), procs, 1000.0, 300) == procs
    assert stdin_blocked.for_worktree(str(tmp_path), procs, 999.0, 300) == []


# --- the notice clause ------------------------------------------------------------------


@pytest.fixture
def utc(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


START = float(calendar.timegm((2025, 11, 19, 14, 7, 0)))      # 2025-11-19 14:07 UTC


@pytest.mark.parametrize("parent,ppid,under", [
    ("bash", 400, "under background shell bash PID 400"),
    ("zsh", 401, "under background shell zsh PID 401"),
    ("sh", 402, "under background shell sh PID 402"),
    ("python3", 403, "under python3 PID 403"),
    (None, 404, "under PID 404"),
])
def test_describe_names_the_pid_its_parent_and_the_start(utc, parent, ppid, under):
    proc = {"pid": 1717706, "comm": "cat", "ppid": ppid, "parent": parent, "started": START}
    assert stdin_blocked.describe(proc) == (
        f"blocked on stdin: cat PID 1717706, {under}, started 14:07. Killing it frees "
        "the agent without losing work.")


# --- the STUCK notice -------------------------------------------------------------------


def test_the_stuck_notice_names_the_oldest_blocked_process(utc, monkeypatch):
    monkeypatch.setenv("CHELA_STUCK_AGENT_ALERT_S", "1200")
    now = START + 3600
    blocked = [{"pid": 1717706, "comm": "cat", "ppid": 400, "parent": "bash", "started": START},
               {"pid": 1717999, "comm": "read", "ppid": 400, "parent": "bash",
                "started": START + 60}]
    probe = {"T9": {"activity": START + 30, "markers": {}, "stdin_blocked": blocked}}
    [ev] = inbox.stuck_events([_run()], inbox._empty(), probe, now)
    assert ev["kind"] == "run_stuck"
    assert ("while running — blocked on stdin: cat PID 1717706, under background shell "
            "bash PID 400, started 14:07. Killing it frees the agent without losing work. — "
            "chela peek T9") in ev["summary"]
    assert "1717999" not in ev["summary"]
    assert ev["payload"]["stdin_blocked_pid"] == 1717706
    assert ev["payload"]["stdin_blocked_comm"] == "cat"


@pytest.mark.parametrize("started,named", [
    (START, True),                  # began after the last activity
    (START - 60, True),             # began in the slack before it (the shell forked it)
    (START - 61, False),            # predates activity: an MCP server, a `tail -f`
    (START - 6 * 3600, False),
])
def test_only_a_process_nothing_has_happened_since_is_named(utc, monkeypatch, started, named):
    """🔴 GUARD (review): a long-lived stdin reader (a stdio MCP server) that predates the
    agent's last activity was not stopping it then — it is not the stall now."""
    monkeypatch.setenv("CHELA_STUCK_AGENT_ALERT_S", "1200")
    blocked = [{"pid": 77, "comm": "python", "ppid": 6, "parent": "node", "started": started}]
    probe = {"T9": {"activity": START, "markers": {}, "stdin_blocked": blocked}}
    [ev] = inbox.stuck_events([_run()], inbox._empty(), probe, START + 3600)
    assert ("blocked on stdin: python PID 77" in ev["summary"]) is named
    assert ev["payload"]["stdin_blocked_pid"] == (77 if named else None)


def test_an_older_innocent_reader_does_not_hide_the_real_culprit(utc, monkeypatch):
    monkeypatch.setenv("CHELA_STUCK_AGENT_ALERT_S", "1200")
    blocked = [{"pid": 77, "comm": "python", "ppid": 6, "parent": "node", "started": START - 9000},
               {"pid": 88, "comm": "cat", "ppid": 6, "parent": "bash", "started": START + 5}]
    probe = {"T9": {"activity": START, "markers": {}, "stdin_blocked": blocked}}
    [ev] = inbox.stuck_events([_run()], inbox._empty(), probe, START + 3600)
    assert ev["payload"]["stdin_blocked_pid"] == 88


@pytest.mark.parametrize("blocked", [None, []])
def test_a_stuck_notice_with_nothing_blocked_reads_as_before(monkeypatch, blocked):
    monkeypatch.setenv("CHELA_STUCK_AGENT_ALERT_S", "1200")
    now = 2_000_000.0
    probe = {"T9": {"activity": now - 2000, "markers": {}}}
    if blocked is not None:
        probe["T9"]["stdin_blocked"] = blocked
    [ev] = inbox.stuck_events([_run()], inbox._empty(), probe, now)
    assert "blocked on stdin" not in ev["summary"]
    assert "while running — chela peek T9" in ev["summary"]
    assert ev["payload"]["stdin_blocked_pid"] is None
    assert ev["payload"]["stdin_blocked_comm"] is None


def test_a_blocked_process_alone_does_not_make_a_run_stuck(monkeypatch):
    """The process only explains a STUCK notice; it never raises one early."""
    monkeypatch.setenv("CHELA_STUCK_AGENT_ALERT_S", "1200")
    now = 2_000_000.0
    probe = {"T9": {"activity": now - 60, "markers": {}, "stdin_blocked": [
        {"pid": 7, "comm": "cat", "ppid": 6, "parent": "bash", "started": now - 5000}]}}
    assert inbox.stuck_events([_run()], inbox._empty(), probe, now) == []


# --- the probe wiring + the threshold ---------------------------------------------------


@pytest.mark.parametrize("env,expected", [(None, 300), ("0", 0), ("900", 900)])
def test_the_age_floor_reads_the_env_over_its_default(monkeypatch, env, expected):
    if env is None:
        monkeypatch.delenv("CHELA_STDIN_BLOCKED_ALERT_S", raising=False)
    else:
        monkeypatch.setenv("CHELA_STDIN_BLOCKED_ALERT_S", env)
    assert config.stdin_blocked_alert_s() == expected


def _probe_world(monkeypatch, tmp_path, procs):
    monkeypatch.setattr(inbox.transcripts, "transcript_for_cwd", lambda cwd: None)
    monkeypatch.setattr(inbox, "_git_last_commit", lambda w: None)
    monkeypatch.setattr(inbox, "_selfcheck_scan", lambda: [])
    calls = []
    monkeypatch.setattr(inbox.stdin_blocked, "scan", lambda: calls.append(1) or procs)
    return calls


def test_stuck_probes_carry_the_worktrees_blocked_processes(tmp_path, monkeypatch):
    wt, other = tmp_path / "wt", tmp_path / "other"
    wt.mkdir()
    other.mkdir()
    now = time.time()
    procs = [_p(11, wt.resolve(), now - 400), _p(12, wt.resolve(), now - 100),
             _p(13, other.resolve(), now - 400)]
    calls = _probe_world(monkeypatch, tmp_path, procs)
    monkeypatch.setenv("CHELA_STDIN_BLOCKED_ALERT_S", "300")
    probes = inbox.stuck_probes([_run(worktree_path=str(wt)),
                                 _run(task_id="T8", worktree_path=str(other))])
    assert [p["pid"] for p in probes["T9"]["stdin_blocked"]] == [11]
    assert [p["pid"] for p in probes["T8"]["stdin_blocked"]] == [13]
    assert calls == [1]                                 # ONE /proc pass per tick
    monkeypatch.setenv("CHELA_STDIN_BLOCKED_ALERT_S", "50")
    probes = inbox.stuck_probes([_run(worktree_path=str(wt))])
    assert [p["pid"] for p in probes["T9"]["stdin_blocked"]] == [11, 12]


# --- acceptance: a REAL process tree ----------------------------------------------------


def _wchan(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/wchan").read_text().strip()
    except OSError:
        return ""


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc")
def test_a_real_cat_on_stdin_is_named_and_a_busy_writer_is_not(tmp_path, monkeypatch):
    """Acceptance: a ``cat`` blocked on a pipe stdin gets a STUCK notice naming its PID; a
    busy leaf (writing output, like pytest) and a CPU-bound leaf, both with a pipe stdin in
    the same worktree, do not."""
    wt = tmp_path / "wt"
    wt.mkdir()
    cat = subprocess.Popen(["cat"], cwd=wt, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
    writer = subprocess.Popen(                     # blocks writing: nobody drains stdout
        [sys.executable, "-c", "import sys\nwhile True: sys.stdout.write('x' * 4096)"],
        cwd=wt, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    spinner = subprocess.Popen([sys.executable, "-c", "while True: pass"],
                               cwd=wt, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
    kids = (cat, writer, spinner)
    try:
        deadline = time.time() + 10
        while time.time() < deadline and _wchan(cat.pid) not in stdin_blocked.PIPE_READ_WCHANS:
            time.sleep(0.05)
        if _wchan(cat.pid) not in stdin_blocked.PIPE_READ_WCHANS:
            pytest.skip(f"this kernel hides wchan ({_wchan(cat.pid)!r})")
        monkeypatch.setenv("CHELA_STDIN_BLOCKED_ALERT_S", "0")
        monkeypatch.setenv("CHELA_STUCK_AGENT_ALERT_S", "1200")
        monkeypatch.setattr(inbox.transcripts, "transcript_for_cwd", lambda cwd: None)
        monkeypatch.setattr(inbox, "_git_last_commit", lambda w: time.time() - 1300)
        monkeypatch.setattr(inbox, "_selfcheck_scan", lambda: [])
        run = _run(worktree_path=str(wt))
        probes = inbox.stuck_probes([run])
        assert [p["pid"] for p in probes["T9"]["stdin_blocked"]] == [cat.pid]
        [ev] = inbox.stuck_events([run], inbox._empty(), probes, time.time())
        assert f"blocked on stdin: cat PID {cat.pid}," in ev["summary"]
        assert ev["payload"]["stdin_blocked_pid"] == cat.pid
        for busy in (writer, spinner):
            assert f"PID {busy.pid}" not in ev["summary"]
    finally:
        for k in kids:
            k.kill()
            k.wait()
        for k in kids:
            for f in (k.stdin, k.stdout):
                if f:
                    f.close()
