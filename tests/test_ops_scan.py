"""CMX-260 — `chela ops scan`: one fixture per rule (a match AND a near-miss), the 10 s budget,
read-only-ness, dedupe, the fleet-pause skip, and the daemon wiring. Fake /proc, fake logs —
never the live fleet."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from chela import main, ops
from tests.test_doctor_notify import _run_daemon_ticks

HZ = os.sysconf("SC_CLK_TCK")
NOW = 1_800_000_000.0
UPTIME = 100_000.0


@pytest.fixture
def env(tmp_path):
    chela_dir, proc = tmp_path / "chela", tmp_path / "proc"
    (chela_dir / "judge-heldout").mkdir(parents=True)
    proc.mkdir()
    (proc / "uptime").write_text(f"{UPTIME} 1.0\n")
    return SimpleNamespace(dir=chela_dir, proc=proc, wt=chela_dir / "worktrees" / "chelamux")


def add_proc(env, pid, argv, cwd, *, ppid=500, age=1800, cpu=0.0, environ=None, cgroup=None):
    d = env.proc / str(pid)
    d.mkdir()
    if cgroup is not None:
        (d / "cgroup").write_text(cgroup)
    if environ is not None:
        (d / "environ").write_bytes(b"\0".join(e.encode() for e in environ) + b"\0")
    start = int((UPTIME - age) * HZ)
    ticks = int(cpu * HZ)                            # split: CPU time is utime + stime
    fields = (["S", str(ppid)] + ["0"] * 9 + [str(ticks // 2), str(ticks - ticks // 2)]
              + ["0"] * 6 + [str(start)])
    (d / "stat").write_text(f"{pid} (x y) " + " ".join(fields) + " 0 0\n")
    (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
    os.symlink(str(cwd), d / "cwd")


def src(env, **kw):
    return ops.Sources(env.dir, env.proc, now=NOW, **kw)


def write_events(env, events):
    (env.dir / "events-core.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))


def leaked(env, state=None):
    """``rule_leaked_proc`` on a scan whose previous scan saw the same processes: an orphan
    is reported only on its 2nd scan in a row (CMX-332)."""
    first = src(env)
    ops.rule_leaked_proc(first, state or {})
    return ops.rule_leaked_proc(src(env), dict(state or {},
                                               orphans=sorted(first.orphan_suspects)))


def set_runs(env, rows):
    import sqlite3
    conn = sqlite3.connect(env.dir / "scheduler.db")
    conn.execute("CREATE TABLE runs (task_id TEXT, status TEXT, started_at TEXT)")
    conn.executemany("INSERT INTO runs VALUES (?, ?, ?)", rows)
    conn.commit()
    conn.close()


GOD = "PM2 v7.0.4: God Daemon (/tmp/chela-tests-pm2-x)"
SCOPE = "chela-suite-1f78a0debe9e"


def cg(unit):
    return f"0::/user.slice/user-1000.slice/user@1000.service/app.slice/{unit}.scope\n"


# --- rule 1: hung test run (CMX-205) -------------------------------------------------------

@pytest.mark.parametrize("argv, where, age, cpu, hit", [
    (["/v/bin/pytest", "-q"], "CMX-1", 1800, 0.0, True),
    (["python3", "-X", "dev", "-m", "pytest"], "judge-CMX-1", 1800, 17.0, True),   # 0.94%
    (["node", "vitest"], "CMX-1", 1800, 19.0, False),                     # 1.06% — busy
    (["node", "--test", "a.test.mjs"], "CMX-1", 1800, 0.0, True),
    (["node", "node_modules/vite/bin/vite.js"], "CMX-1", 1800, 0.0, False),  # a dev server
    (["/v/bin/pytest"], "CMX-1", 1150, 0.0, False),                       # <20 min old
    (["uv", "run", "pytest"], "CMX-1", 1800, 0.0, False),                 # the wrapper, not it
    (["/v/bin/pytest"], None, 1800, 0.0, False),                          # not in a worktree
])
def test_hung_test_rule(env, argv, where, age, cpu, hit):
    add_proc(env, 42, argv, env.wt / where if where else env.dir / "elsewhere", age=age, cpu=cpu)
    got = ops.rule_hung_test(src(env), {})
    assert [f["subject"] for f in got] == (["pid:42"] if hit else [])


def test_hung_test_measures_since_the_previous_sample(env):
    """A suite that burned CPU for 14 min then hung: busy over its life, idle since the last
    scan. The near-miss: the same process still burning since that sample."""
    add_proc(env, 42, ["/v/bin/pytest"], env.wt / "CMX-1", age=5400, cpu=840.0)
    start = int((UPTIME - 5400) * HZ)
    assert ops.rule_hung_test(src(env), {}) == []                 # 15.6% over its life
    idle = {"cpu": {f"42:{start}": [NOW - 1800, 839.0]}}
    busy = {"cpu": {f"42:{start}": [NOW - 1800, 600.0]}}
    assert [f["evidence"]["span_s"] for f in ops.rule_hung_test(src(env), idle)] == [1800]
    assert ops.rule_hung_test(src(env), busy) == []


# --- rule 2: leaked process (CMX-253) ------------------------------------------------------

@pytest.mark.parametrize("where, status, ppid, extra, hit", [
    ("CMX-1", "done", 500, [], True),
    ("judge-CMX-1", "needs_human", 500, [], True),
    ("CMX-1", "closed", 500, [], True),
    ("CMX-1", "running", 1, [], True),                       # orphaned
    ("CMX-1", "running", 500, [], False),
    ("judge-CMX-1", "awaiting_review", 1, ["--detached-child"], False),   # setsid on purpose
    ("judge-CMX-1", "done", 1, ["--detached-child"], True),
])
def test_leaked_proc_rule(env, where, status, ppid, extra, hit):
    set_runs(env, [("CMX-1", "running", "2026-01-01"), ("CMX-1", status, "2026-01-02")])
    add_proc(env, 42, ["python3", "x", *extra], env.wt / where, ppid=ppid)
    got = leaked(env)
    assert [(f["subject"], f["evidence"]["task"]) for f in got] == (
        [("pid:42", "CMX-1")] if hit else [])


WF_2400 = "judge:\n  suite_timeout_seconds: 2400\n"


@pytest.mark.parametrize("where, status, argv, workflow, age, hit", [
    ("CMX-1", "running", ["/v/bin/pytest", "-q"], WF_2400, 2401, True),   # CMX-274: a runaway
    ("CMX-1", "running", ["python", "-m", "pytest"], WF_2400, 5880, True),
    ("CMX-1", "running", ["/v/bin/pytest", "-q"], WF_2400, 2400, False),  # exactly the timeout
    ("CMX-1", "running", ["/v/bin/pytest", "-q"], WF_2400, 901, False),   # the workflow's, not 900
    ("CMX-1", "running", ["/v/bin/pytest", "-q"], None, 901, True),       # the 900 s default
    ("CMX-1", "running", ["/v/bin/pytest", "-q"], None, 900, False),
    ("CMX-1", "running", ["python3", "x"], None, 9000, False),            # not a test run
    ("CMX-1", "awaiting_review", ["/v/bin/pytest"], None, 9000, False),   # not a running agent
    ("judge-CMX-1", "running", ["/v/bin/pytest"], None, 9000, False),     # the judge has its own
])
def test_leaked_proc_flags_a_test_run_under_a_running_agent_past_the_suite_timeout(
        env, where, status, argv, workflow, age, hit):
    set_runs(env, [("CMX-1", status, "2026-01-01")])
    (env.wt / where).mkdir(parents=True, exist_ok=True)
    if workflow:
        (env.wt / where / "WORKFLOW.md").write_text(f"---\nproject_key: CMX\n{workflow}---\n")
    add_proc(env, 42, argv, env.wt / where, age=age)
    got = leaked(env)
    assert [f["summary"] for f in got] == (
        [f"pid 42 under run CMX-1 is alive but a test run {age / 60:.0f}m old, past the "
         "suite timeout"] if hit else [])


def test_leaked_proc_reads_argv_paths_and_skips_unreadable_procs(env):
    set_runs(env, [("CMX-2", "done", "2026-01-01")])
    add_proc(env, 42, ["pytest", str(env.wt / "CMX-2" / "tests")], env.dir)
    (env.proc / "43").mkdir()                                    # no stat: vanished mid-scan
    add_proc(env, 44, ["init"], env.dir, ppid=1)                 # orphan, but not ours
    add_proc(env, os.getpid(), ["pytest"], env.wt / "CMX-2", ppid=1)   # the scan itself
    add_proc(env, 45, [], env.wt / "CMX-2", ppid=1)               # a kernel thread: no argv
    add_proc(env, 99, ["x"], env.wt / "CMX-2")
    os.rename(env.proc / "99", env.proc / "self")                # /proc/self is not a pid
    assert [f["subject"] for f in leaked(env)] == ["pid:42", "pid:45"]
    assert [f["subject"] for f in ops.rule_hung_test(src(env), {})] == ["pid:42"]


def test_procs_come_back_in_pid_order_whatever_the_listing_order(env, monkeypatch):
    """CMX-285: /proc listing order is the filesystem's. Fed in reverse — which is also the
    lexical order of "10" < "9" — the findings still come back by numeric pid."""
    for pid in (9, 10, 11):
        add_proc(env, pid, ["pytest"], env.wt / "CMX-2", ppid=1, age=5400)
    listing = [env.proc / n for n in ("uptime", "11", "10", "9")]
    real = type(env.proc).iterdir
    monkeypatch.setattr(type(env.proc), "iterdir",
                        lambda self: iter(listing) if self == env.proc else real(self))
    want = ["pid:9", "pid:10", "pid:11"]
    assert [f["subject"] for f in leaked(env)] == want
    assert [f["subject"] for f in ops.rule_hung_test(src(env), {})] == want


# CMX-332: an orphan is a finding only on its 2nd scan in a row ----------------------------

def orphan_scans(env, n, between=lambda: None):
    """The leaked subjects of each of ``n`` scans, fed forward as the daemon feeds them."""
    state, out = {}, []
    for i in range(n):
        if i:
            between()
        s = src(env)
        out.append([f["subject"] for f in ops.rule_leaked_proc(s, state)])
        state = {"daemons": sorted(s.daemon_suspects), "orphans": sorted(s.orphan_suspects)}
    return out


def test_an_orphan_gone_by_the_next_scan_is_never_a_finding(env):
    """The 10-24..10-27 shape: a test child re-parented to 1, reaped a minute later."""
    add_proc(env, 42, ["node", "hang.test.mjs"], env.wt / "CMX-1", ppid=1, age=5)
    assert orphan_scans(env, 3, lambda: shutil.rmtree(env.proc / "42", True)) == [[], [], []]


def test_a_persistent_orphan_is_one_finding_from_its_second_scan(env):
    """The 10-22 leak: hang.test.mjs, 2 days old, ppid 1. Negative control: the first
    sighting alone fires nothing."""
    add_proc(env, 42, ["node", "hang.test.mjs"], env.wt / "CMX-1", ppid=1, age=2 * 86400)
    assert orphan_scans(env, 1) == [[]]
    assert orphan_scans(env, 3) == [[], ["pid:42"], ["pid:42"]]


def test_an_orphan_that_is_reparented_back_starts_over(env):
    add_proc(env, 42, ["sleep"], env.wt / "CMX-1", ppid=1)

    def adopt():
        shutil.rmtree(env.proc / "42")
        add_proc(env, 42, ["sleep"], env.wt / "CMX-1", ppid=500)

    assert orphan_scans(env, 2, adopt) == [[], []]


def test_a_reused_pid_is_not_the_previous_scans_orphan(env):
    add_proc(env, 42, ["sleep"], env.wt / "CMX-1", ppid=1, age=120)
    s = src(env)
    ops.rule_leaked_proc(s, {})
    assert s.orphan_suspects == {f"42:{int((UPTIME - 120) * HZ)}"}
    prev = {"orphans": [f"42:{int((UPTIME - 120) * HZ) - 1}"]}
    assert ops.rule_leaked_proc(src(env), prev) == []


@pytest.mark.parametrize("ppid, where, status", [
    (500, "CMX-1", "running"),                                  # not orphaned
    (1, "CMX-1", "awaiting_review"),                            # spared by the live scope
])
def test_a_spared_process_is_no_suspect(env, ppid, where, status):
    set_runs(env, [("CMX-1", status, "2026-01-01")])
    add_proc(env, 42, ["sleep"], env.wt / where, ppid=ppid, cgroup=cg(SCOPE), age=120)
    add_proc(env, 50, ["sh"], env.dir / "elsewhere", cgroup=cg(SCOPE))
    s = src(env)
    assert ops.rule_leaked_proc(s, {}) == [] and s.orphan_suspects == set()


def test_an_over_runs_orphan_is_still_found_on_the_first_scan(env):
    """Only the orphan reason waits a scan; a finished run's process never did."""
    set_runs(env, [("CMX-1", "done", "2026-01-01")])
    add_proc(env, 42, ["sleep"], env.wt / "CMX-1", ppid=1)
    assert [f["summary"] for f in ops.rule_leaked_proc(src(env), {})] == [
        "pid 42 under run CMX-1 is alive but its run is done"]


@pytest.mark.parametrize("sharer, age, hit", [
    (dict(cgroup=cg(SCOPE)), 120, False),                      # 10-27: its suite still runs
    (dict(cgroup=cg(SCOPE)), 900, False),                      # the 900 s default, exactly
    (dict(cgroup=cg(SCOPE)), 901, True),                       # past it: no suite excuses it
    (dict(cgroup=cg(SCOPE), ppid=1), 120, True),               # an orphan shares nothing
    (dict(cgroup=cg(SCOPE), ppid=42), 120, True),              # nor its own child
    (dict(cgroup=cg(SCOPE), argv=[GOD]), 120, True),           # nor a daemon
    (dict(cgroup=cg("chela-suite-abc")), 120, True),           # another suite's scope
    (dict(cgroup=cg("app.slice/other")), 120, True),           # no suite scope at all
    (dict(), 120, True),                                       # the sharer has no scope
])
def test_an_orphan_in_a_live_suites_scope_is_spared(env, sharer, age, hit):
    """CMX-332 (the 10-27 case): ``tmux new-session … sleep 60`` from a test with its own
    TMUX_TMPDIR, in the hand-in suite's scope — no -L socket names its owner test."""
    add_proc(env, 42, ["tmux", "new-session", "-d", "-s", "cmx340"], env.wt / "CMX-1",
             ppid=1, age=age, cgroup=cg(SCOPE))
    add_proc(env, 50, sharer.get("argv", ["sh", "-c", "x"]), env.dir / "elsewhere",
             ppid=sharer.get("ppid", 500), cgroup=sharer.get("cgroup"))
    assert orphan_scans(env, 2)[-1] == (["pid:42"] if hit else [])


@pytest.mark.parametrize("argv", [
    ["/usr/bin/tmux", "-L", "chelatest-77-ab12cd", "new-session", "-d"],   # its test is gone
    ["/usr/bin/tmux", "-Lchelatest-77-ab12cd", "new-session", "-d"],
    ["/v/bin/pytest", "-q"],                                   # an orphaned test run
    ["python3", "-m", "pytest"],
])
def test_a_live_scope_never_spares_an_orphan_with_its_own_verdict(env, argv):
    """CMX-279's definitive leaks stay leaks inside a live suite's scope."""
    add_proc(env, 42, argv, env.wt / "CMX-1", ppid=1, age=120, cgroup=cg(SCOPE))
    add_proc(env, 50, ["sh", "-c", "x"], env.dir / "elsewhere", cgroup=cg(SCOPE))
    assert orphan_scans(env, 2)[-1] == ["pid:42"]


def test_an_orphan_without_a_scope_is_not_spared_by_a_scoped_stranger(env):
    add_proc(env, 42, ["sleep"], env.wt / "CMX-1", ppid=1)
    add_proc(env, 50, ["sh"], env.dir / "elsewhere", cgroup=cg(SCOPE))
    assert orphan_scans(env, 2)[-1] == ["pid:42"]


def test_without_a_runs_db_an_orphan_is_still_found(env):
    add_proc(env, 42, ["sleep"], env.wt / "CMX-1", ppid=1)
    assert [f["evidence"]["run_status"] for f in leaked(env)] == [None]


# --- rule 2b: a self-daemonizing PM2 God Daemon is ppid 1 by design (CMX-273/353) ---------

@pytest.fixture
def pm2(env, tmp_path):
    home = tmp_path / "chela-tests-pm2-x"
    home.mkdir()
    return home


def god(env, home, *, pid=42, argv=None, where="CMX-1", age=120, environ=None, ppid=1,
        cgroup=None):
    add_proc(env, pid, argv or [GOD], env.wt / where, ppid=ppid, age=age, cgroup=cgroup,
             environ=[f"PM2_HOME={home}", "PATH=/bin"] if environ is None else environ)


def leaks(env, state=None):
    return sorted((f["subject"], f["summary"])
                  for f in leaked(env, state))


def scans(env, n=2):
    """``n`` scans in a row, each fed the previous one's suspects; the last's leaks."""
    state, got = {}, []
    for _ in range(n):
        s = src(env)
        got = sorted((f["subject"], f["summary"]) for f in ops.rule_leaked_proc(s, state))
        state = {"daemons": sorted(s.daemon_suspects), "orphans": sorted(s.orphan_suspects)}
    return got


def daemon_leak(pid, run, why, where="run"):
    return (f"pid:{pid}", f"pid {pid} under {where} {run} is alive but {why} on 2 scans in a "
                          "row — do not kill while a suite runs")


def incident(env, pm2):
    """2026-10-26: CMX-290's suite in its systemd scope; xdist workers carry no PM2_HOME in
    their /proc environ (conftest sets it after start-up) and one has chdir'd to a tmp dir."""
    god(env, pm2, cgroup=cg(SCOPE))
    run = env.wt / "CMX-1"
    add_proc(env, 50, [str(run / ".venv/bin/python"), str(run / ".venv/bin/pytest"), "-q"],
             run, environ=["PATH=/bin"], cgroup=cg(SCOPE))
    add_proc(env, 51, ["/usr/bin/python3", "-u", "-c", "import sys;exec(...)"],
             env.dir / "tmp-chdir", ppid=50, environ=["PYTEST_XDIST_WORKER=gw0"],
             cgroup=cg(SCOPE))


def test_live_suite_with_a_daemonized_test_pm2_is_never_a_leak(env, pm2):
    incident(env, pm2)
    assert scans(env, 3) == []


def test_negative_control_the_env_matching_check_alone_flags_the_live_suite(env, pm2):
    """The pre-CMX-353 check: a live process of the run whose environ carries the daemon's
    PM2_HOME. On the incident's own processes it finds none, so it reported a live suite."""
    incident(env, pm2)
    s = src(env)
    sharers = [q["pid"] for q in s.procs if q["pid"] != 42
               and (s.environ(q["pid"]) or {}).get("PM2_HOME") == str(pm2)]
    assert sharers == []                          # ⇒ the env check alone: a false finding
    assert scans(env) == []                       # ⇒ by run: none


def test_a_suite_that_ended_with_its_daemon_alive_is_one_finding_on_the_second_scan(env, pm2):
    god(env, pm2, cgroup=cg(SCOPE))
    assert scans(env, 1) == []
    want = [daemon_leak(42, "CMX-1", f"no suite of its run is live (PM2_HOME {pm2})")]
    assert scans(env, 2) == want
    assert scans(env, 3) == want


def test_a_suspect_that_is_live_on_the_next_scan_starts_over(env, pm2):
    god(env, pm2)
    s1 = src(env)
    ops.rule_leaked_proc(s1, {})
    assert s1.daemon_suspects == {f"42:{int((UPTIME - 120) * HZ)}"}
    add_proc(env, 50, ["/v/bin/pytest"], env.wt / "CMX-1")
    s2 = src(env)
    assert ops.rule_leaked_proc(s2, {"daemons": sorted(s1.daemon_suspects)}) == []
    assert s2.daemon_suspects == set()


def test_a_reused_pid_is_not_the_previous_scans_suspect(env, pm2):
    god(env, pm2)
    prev = {"daemons": [f"42:{int((UPTIME - 120) * HZ) - 1}"]}
    assert leaks(env, prev) == []


@pytest.mark.parametrize("witness, live", [
    (dict(cgroup=cg(SCOPE), cwd="elsewhere", argv=["python", "-c", "w"]), True),  # its scope
    (dict(cgroup=cg("chela-suite-abc"), argv=["sh"]), True),   # another suite of its run
    (dict(cgroup=cg("chela-suite-abc"), cwd="CMX-2", argv=["sh"]), False),  # another run's
    (dict(argv=["/v/bin/python", "/v/bin/pytest", "-q"]), True),  # no scope: its test run
    (dict(argv=["/v/bin/pytest"]), True),
    (dict(argv=["python", "-m", "pytest"]), True),
    (dict(argv=["python", "x"]), False),                       # no scope, not a test run
    (dict(argv=["python", "/v/pytest.py"]), False),            # pytest.py is not pytest
    (dict(argv=["python3.13", "/v/bin/vitest"]), True),
    (dict(argv=["vim", "pytest"]), False),                     # not a python running it
    (dict(argv=["/v/bin/pytest"], cwd="CMX-2"), False),        # another run's test
    (dict(argv=["/v/bin/pytest"], cwd="judge-CMX-1"), False),  # its judge's, not the run's
    (dict(cgroup=cg(SCOPE), ppid=1), False),                   # an orphan shares nothing
    (dict(cgroup=cg(SCOPE), argv=[GOD.replace("-x)", "-y)")]), False),  # another daemon
    (dict(cgroup=cg(SCOPE), ppid=42), False),                  # the daemon's own child
    (dict(cgroup=cg(SCOPE), ppid=52), False),                  # ...and grandchild
    (dict(cgroup=cg("app.slice/other"), argv=["sh"]), False),  # not a suite scope
])
def test_what_makes_a_suite_of_the_run_live(env, pm2, witness, live):
    god(env, pm2, cgroup=cg(SCOPE))
    add_proc(env, 52, ["bash"], env.dir / "elsewhere", ppid=42)
    cwd = witness.get("cwd", "CMX-1")
    add_proc(env, 50, witness.get("argv", ["python", "-c", "w"]),
             env.dir / cwd if cwd == "elsewhere" else env.wt / cwd,
             ppid=witness.get("ppid", 500), cgroup=witness.get("cgroup"))
    assert [s for s, _ in scans(env) if s == "pid:42"] == ([] if live else ["pid:42"])


def test_a_daemon_outside_any_scope_is_not_live_by_a_scoped_stranger(env, pm2):
    """Two processes with NO scope do not share one."""
    god(env, pm2)
    add_proc(env, 50, ["python", "-c", "w"], env.dir / "elsewhere")
    add_proc(env, 51, ["python", "-c", "w"], env.dir / "elsewhere", cgroup=cg("chela-suite-ab"))
    assert [s for s, _ in scans(env)] == ["pid:42"]


@pytest.mark.parametrize("text, scope", [
    (cg(SCOPE), SCOPE),
    (f"12:memory:/app.slice/{SCOPE}.scope\n0::/x.scope\n", SCOPE),   # cgroup v1 lines too
    ("0::/app.slice/chela-suite-1f78.scope", "chela-suite-1f78"),    # no trailing newline
    ("0::/app.slice/chela-suite-1f78.scope (deleted)\n", "chela-suite-1f78"),  # removed cgroup
    ("0::/app.slice/chela-suite-1f78.scope (gone)\n", None),
    ("0::/app.slice/xchela-suite-1f78.scope\n", None),               # anchored at a /
    ("0::/app.slice/chela-suite-1f78.scope/sub\n", None),            # the scope itself
    ("0::/app.slice/chela-suite-1F78.scope\n", None),                # hex is lowercase
    ("0::/app.slice/chela-suite-guard-x.scope\n", None),
    ("0::/app.slice/chela-suite-.scope\n", None),
    ("0::/app.slice/chela-suite-1f78.service\n", None),
    ("", None),
])
def test_suite_scope_reads_the_cgroup_path(env, text, scope):
    add_proc(env, 50, ["x"], env.dir, cgroup=text)
    assert src(env).suite_scope(50) == scope


def test_suite_scope_of_an_unreadable_cgroup_is_none(env):
    add_proc(env, 50, ["x"], env.dir)
    assert src(env).suite_scope(50) is None


def test_suite_scope_is_the_unit_suite_guard_names(monkeypatch):
    from chela import suite_guard
    monkeypatch.setattr(suite_guard.uuid, "uuid4", lambda: SimpleNamespace(hex="1f78a0debe9e00"))
    monkeypatch.setattr(suite_guard, "ceiling_bytes", lambda: 1 << 30)
    monkeypatch.setattr(suite_guard, "_scope_works", lambda env: True)
    monkeypatch.setattr(suite_guard, "_reap", lambda *a, **k: None)
    with suite_guard.bounded("true", {}) as (cmd, _check):
        unit = cmd.split("--unit=", 1)[1].split()[0]
    assert ops.SUITE_SCOPE_RE.search(f"/app.slice/{unit}.scope").group(1) == unit == SCOPE


def test_god_daemon_whose_home_is_gone_or_unreadable_is_a_leak(env, pm2):
    god(env, pm2.parent / "chela-tests-pm2-gone", pid=42)
    god(env, pm2, pid=43, environ=["PATH=/bin"])
    add_proc(env, 44, [GOD], env.wt / "CMX-1", ppid=1, age=120)        # no environ file
    assert scans(env) == [
        daemon_leak(42, "CMX-1", f"its PM2_HOME {pm2.parent / 'chela-tests-pm2-gone'} is gone"),
        daemon_leak(43, "CMX-1", "it is orphaned (ppid 1) and its environ has no PM2_HOME"),
        daemon_leak(44, "CMX-1", "it is orphaned (ppid 1) and its environ is unreadable")]


def test_a_live_suite_spares_even_a_daemon_older_than_the_suite_timeout(env, pm2):
    god(env, pm2, age=9000)
    add_proc(env, 50, ["/v/bin/pytest"], env.wt / "CMX-1")
    assert scans(env) == []


@pytest.mark.parametrize("argv, hit", [
    ([GOD], False),                                            # the PM2 rule: its suite is live
    (["node", GOD], True),                                     # the marker must be argv[0]
    (["PM2 v7.0.4: God Daemon"], True),                        # no " (" — not the title
])
def test_only_a_god_daemon_title_takes_the_pm2_rule(env, pm2, argv, hit):
    """A live suite process in its scope that is no test run: it spares a God Daemon at any
    age, while any other orphan is a helper the scope spares only within the suite timeout
    (CMX-279/332)."""
    god(env, pm2, argv=argv, cgroup=cg(SCOPE), age=9000)
    add_proc(env, 50, ["sh", "-c", "x"], env.dir / "elsewhere", cgroup=cg(SCOPE))
    assert [s for s, _ in scans(env)] == (["pid:42"] if hit else [])


@pytest.mark.parametrize("ppid, suite, want", [
    (1, False, "its run is done"),
    (500, False, "its run is done"),                           # not orphaned: still over
    (1, True, None),                                           # a live suite: never
])
def test_god_daemon_of_a_finished_run(env, pm2, ppid, suite, want):
    set_runs(env, [("CMX-1", "done", "2026-01-01")])
    god(env, pm2, ppid=ppid)
    if suite:
        add_proc(env, 50, ["/v/bin/pytest"], env.wt / "CMX-1")
    got = [x for x in scans(env) if x[0] == "pid:42"]
    assert got == ([daemon_leak(42, "CMX-1", want)] if want else [])


def test_a_non_orphaned_god_daemon_of_a_running_run_is_not_a_leak(env, pm2):
    set_runs(env, [("CMX-1", "running", "2026-01-01")])
    god(env, pm2, ppid=500)
    assert scans(env) == []


def test_a_judge_worktrees_daemon_is_named_under_judge(env, pm2):
    god(env, pm2, where="judge-CMX-1")
    assert scans(env) == [daemon_leak(42, "CMX-1", f"no suite of its run is live (PM2_HOME "
                                                   f"{pm2})", where="judge")]


# --- rule 2c: a live test's own detached helper is ppid 1 by design (CMX-279) --------------

TMUX = ["/usr/bin/tmux", "-L", "chelatest-50-ab12cd", "new-session", "-d", "-s", "t"]
SCRIPT = ["bash", "scripts/agent-terminals.sh"]


def helper(env, argv, *, pid=42, where="CMX-1", age=120, cwd=None):
    add_proc(env, pid, argv, cwd or env.wt / where, ppid=1, age=age)


def test_tmux_server_of_a_live_test_is_not_a_leak(env):
    helper(env, TMUX)
    add_proc(env, 50, ["python", "-c", "worker"], env.dir / "elsewhere")   # any live pid 50
    assert leaks(env) == []


def test_tmux_server_whose_test_is_gone_is_a_leak(env):
    helper(env, TMUX)
    add_proc(env, 51, ["/v/bin/pytest"], env.wt / "CMX-1")    # a live test, but not ITS test
    assert leaks(env) == [("pid:42", "pid 42 under run CMX-1 is alive but it is orphaned "
                                     "(ppid 1) and the test that owns tmux socket "
                                     "chelatest-50-ab12cd is gone")]


@pytest.mark.parametrize("argv, owner, hit", [
    (["tmux", "-Lchelatest-50-x", "new-session", "-d"], dict(), False),   # -L<name>
    (["tmux", "-u", "-L", "chelatest-50-x"], dict(), False),
    (TMUX, dict(age=120), False),                              # started together: still its own
    (TMUX, dict(age=119), True),                               # pid 50 reused AFTER the server
    (TMUX, dict(ppid=42), True),                               # the server's own child
    (["tmux", "-L", "chelatest-500-x"], dict(), True),         # names another pid
    (["tmux", "-L", "xchelatest-50-x"], dict(), True),         # anchored at the start
    (["tmux", "-L", "chelatest-50"], dict(), True),            # <pid>- needs its dash
    (["tmux", "-L"], dict(), True),                            # no socket name
    (["tmuxx", "-L", "chelatest-50-x"], dict(), True),         # not tmux
    (["tmux", "-S", "-L", "chelatest-50-x"], dict(), True),   # -S takes "-L" as its value
    (["tmux", "-f", "x.conf", "-L", "chelatest-50-x"], dict(), False),
    (["/v/bin/tmux", "new", "-L", "chelatest-50-x"], dict(), True),  # the subcommand's, not tmux's
    (["tmux", "new", "chelatest-50-x"], dict(), True),         # no -L at all
])
def test_tmux_socket_names_its_owner_test(env, argv, owner, hit):
    helper(env, argv)
    add_proc(env, 50, ["python", "-c", "worker"], env.dir / "elsewhere",
             age=owner.get("age", 1800), ppid=owner.get("ppid", 500))
    assert [s for s, _ in leaks(env)] == (["pid:42"] if hit else [])


def test_tmux_server_with_a_dead_owner_is_a_leak_even_beside_a_live_pytest(env):
    """The socket names its owner: a different live test in its worktree does not adopt it."""
    helper(env, TMUX)
    add_proc(env, 51, ["/v/bin/pytest"], env.wt / "CMX-1")
    assert [s for s, _ in leaks(env)] == ["pid:42"]


def test_unnamed_tmux_socket_falls_back_to_the_worktree_rule(env):
    helper(env, ["tmux", "-L", "chelatest-fence-proof", "new-session", "-d"])
    assert [s for s, _ in leaks(env)] == ["pid:42"]
    add_proc(env, 51, ["/v/bin/pytest"], env.wt / "CMX-1")
    assert leaks(env) == []


def test_script_in_a_worktree_with_a_live_pytest_is_not_a_leak(env):
    helper(env, SCRIPT, where="judge-CMX-1")
    add_proc(env, 51, ["python3", "-m", "pytest", "-q"], env.wt / "judge-CMX-1")
    assert leaks(env) == []


def test_script_in_a_worktree_without_a_live_pytest_is_a_leak(env):
    helper(env, SCRIPT, where="judge-CMX-1")
    add_proc(env, 51, ["python3", "x"], env.wt / "judge-CMX-1")       # not a test run
    assert leaks(env) == [("pid:42", "pid 42 under judge CMX-1 is alive but it is orphaned "
                                     "(ppid 1) and no live test run shares its worktree")]


@pytest.mark.parametrize("pytest_, hit", [
    (dict(where="CMX-2"), True),                               # another run's pytest
    (dict(where="CMX-1"), True),                               # the run's, not its judge's
    (dict(age=119), True),                                     # started after the helper
    (dict(age=120), False),                                    # started together
    (dict(ppid=42), True),                                     # the helper's own child
    (dict(ppid=52), True),                                     # ...and grandchild
    (dict(ppid=1), True),                                      # an orphaned pytest owns nothing
])
def test_script_owner_must_be_a_live_test_of_its_worktree(env, pytest_, hit):
    helper(env, SCRIPT, where="judge-CMX-1")
    add_proc(env, 52, ["bash"], env.dir / "elsewhere", ppid=42)
    add_proc(env, 51, ["/v/bin/pytest"], env.wt / pytest_.get("where", "judge-CMX-1"),
             age=pytest_.get("age", 1800), ppid=pytest_.get("ppid", 500))
    assert [s for s, _ in leaks(env) if s != "pid:51"] == (["pid:42"] if hit else [])


def test_script_whose_cwd_is_not_the_worktree_is_a_leak(env):
    """Only the CWD places a helper in a worktree — an argv path alone does not."""
    helper(env, ["bash", str(env.wt / "judge-CMX-1" / "scripts" / "agent-terminals.sh")],
           cwd=env.dir / "elsewhere")
    add_proc(env, 51, ["/v/bin/pytest"], env.wt / "judge-CMX-1")
    assert leaks(env) == [("pid:42", "pid 42 under judge CMX-1 is alive but it is orphaned "
                                     "(ppid 1)")]


def test_an_orphaned_test_run_is_never_a_helper(env):
    """CMX-274's runaway stays a leak even beside a live sibling pytest of its run."""
    helper(env, ["/v/bin/pytest", "-q"])
    add_proc(env, 51, ["/v/bin/pytest"], env.wt / "CMX-1")
    assert leaks(env) == [("pid:42", "pid 42 under run CMX-1 is alive but it is orphaned "
                                     "(ppid 1)")]


@pytest.mark.parametrize("argv", [TMUX, SCRIPT])
@pytest.mark.parametrize("workflow, age, hit", [
    (None, 900, False), (None, 901, True),                               # the 900 s default
    ("judge:\n  suite_timeout_seconds: 2400\n", 2400, False),
    ("judge:\n  suite_timeout_seconds: 2400\n", 2401, True),
])
def test_helper_older_than_its_runs_suite_timeout_is_a_leak(env, argv, workflow, age, hit):
    (env.wt / "CMX-1").mkdir(parents=True)
    if workflow:
        (env.wt / "CMX-1" / "WORKFLOW.md").write_text(f"---\nproject_key: CMX\n{workflow}---\n")
    helper(env, argv, age=age)
    add_proc(env, 50, ["/v/bin/pytest"], env.wt / "CMX-1", age=9000)
    assert leaks(env) == ([("pid:42", "pid 42 under run CMX-1 is alive but it is orphaned "
                            "(ppid 1) and older than the suite timeout "
                            f"({2400 if workflow else 900}s)")] if hit else [])


def test_helper_of_a_finished_run_is_a_leak_even_while_owned(env):
    set_runs(env, [("CMX-1", "done", "2026-01-01")])
    helper(env, TMUX)
    add_proc(env, 50, ["/v/bin/pytest"], env.dir / "elsewhere")
    assert leaks(env) == [("pid:42", "pid 42 under run CMX-1 is alive but its run is done")]


# --- rule 3: unsettled CI red (CMX-256) ----------------------------------------------------

def test_ci_red_rule_falls_back_to_the_pr_head_sha(env):
    write_events(env, [{"seq": 7, "type": "run_ci_red",
                        "payload": {"task_id": "CMX-1", "failing": [], "pr_head_sha": "9876543210"}}])
    assert [f["subject"] for f in ops.rule_ci_red_unsettled(src(env), {})] == ["CMX-1@9876543"]


@pytest.mark.parametrize("payload, hit", [
    ({"failing": ["", " "]}, True),
    ({"failing": []}, True),
    ({"failing": ["?"]}, True),
    ({"failing": ["?", "CI / lint"]}, False),
    ({"failing": ["CI / test"]}, False),
])
def test_ci_red_rule(env, payload, hit):
    write_events(env, [{"seq": 7, "ts": NOW, "type": "run_ci_red",
                        "payload": dict(payload, task_id="CMX-1", ci_sha="abcdef123")},
                       {"seq": 8, "type": "run_stuck", "payload": {"failing": []}}])
    got = ops.rule_ci_red_unsettled(src(env), {})
    assert [(f["subject"], f["evidence"]["seq"]) for f in got] == (
        [("CMX-1@abcdef1", 7)] if hit else [])


# --- rule 4: the same flake across judge baselines (CMX-195/257) ----------------------------

def heldout(env, task, at, flakes=(), cannot="", mtime=None):
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(at)), "task_id": task,
           "baseline_flakes": list(flakes), "cannot_verify": cannot}
    path = env.dir / "judge-heldout" / f"{task}.jsonl"
    with open(path, "a") as fh:
        fh.write(json.dumps(rec) + "\n")
    os.utime(path, (mtime or at,) * 2)


def test_repeat_flake_rule_matches_across_tasks_via_flakes_and_red_baselines(env):
    heldout(env, "CMX-1", NOW - 3600, flakes=["tests/a.py::t"])
    heldout(env, "CMX-2", NOW - 60, cannot="(exited 1 — failing: tests/a.py::t, tests/b.py::u[x]).")
    heldout(env, "CMX-3", NOW - 60, cannot="failing: tests/b.py::u[x]). RED")
    got = {f["subject"]: sorted(f["evidence"]["tasks"]) for f in ops.rule_repeat_flake(src(env), {})}
    assert got == {"tests/a.py::t": ["CMX-1", "CMX-2"], "tests/b.py::u[x]": ["CMX-2", "CMX-3"]}


def test_repeat_flake_near_misses(env):
    heldout(env, "CMX-1", NOW - 60, flakes=["tests/a.py::t"])
    heldout(env, "CMX-1", NOW - 30, flakes=["tests/a.py::t"])     # same task again
    heldout(env, "CMX-2", NOW - 25 * 3600, flakes=["tests/a.py::t"])  # older than 24 h
    heldout(env, "CMX-3", NOW - 60, flakes=["tests/z.py::other"])
    heldout(env, "CMX-4", NOW - 25 * 3600, flakes=["tests/a.py::t"], mtime=NOW)  # old record
    heldout(env, "CMX-5", NOW - 60, flakes=["tests/a.py::t"], mtime=NOW - 25 * 3600)  # old file
    assert ops.rule_repeat_flake(src(env), {}) == []


def test_repeat_flake_names_a_quarantine_candidate_after_two_prs_ci_flakes(env):
    import sqlite3
    from datetime import datetime, timezone

    def at(ago):
        return datetime.fromtimestamp(NOW - ago, timezone.utc).isoformat()
    t, u = "tests/a.py::t", "tests/b.py::u"
    flakes = {"CMX-1": [{"test": t, "at": at(3600)}, {"test": u, "at": at(60)},
                        {"test": "?", "at": at(60)}],
              "CMX-2": [{"test": t, "at": at(48 * 3600)}, {"test": "?", "at": at(60)}],
              "CMX-3": [{"test": u, "at": at(49 * 3600)}, {"test": u, "at": "garbage"}],
              "CMX-4": "not json"}
    conn = sqlite3.connect(env.dir / "scheduler.db")
    conn.execute("CREATE TABLE runs (task_id TEXT, status TEXT, started_at TEXT, ci_flakes TEXT)")
    conn.executemany("INSERT INTO runs VALUES (?, 'done', '', ?)",
                     [(k, v if isinstance(v, str) else json.dumps(v)) for k, v in flakes.items()])
    conn.commit()
    conn.close()
    got = [(f["subject"], f["summary"], sorted(f["evidence"]["tasks"]))
           for f in ops.rule_repeat_flake(src(env), {})]
    assert got == [(f"ci:{t}", f"quarantine candidate: {t} — CI flake on 2 PRs in 48h: "
                    "CMX-1, CMX-2", ["CMX-1", "CMX-2"])]


def test_repeat_flake_survives_a_runs_table_without_ci_flakes(env):
    set_runs(env, [("CMX-1", "done", "")])
    assert ops.rule_repeat_flake(src(env), {}) == []


# --- CMX-298: a flake from before the test's latest fix does not count ----------------------

def _g(repo, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@t", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_CONFIG_NOSYSTEM="1")
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True, env=env).stdout.strip()


TEST_SRC = ("import os\nfrom pkg import mod\nimport pkg.deep.leaf\nfrom .helper import h\n"
            "from . import sib\ndef test_t():\n    pass\n")
FILES = {"WORKFLOW.md": "---\nproject_key: CMX\nworkspace:\n  base_branch: dev\n---\n",
         "tests/test_a.py": TEST_SRC, "tests/helper.py": "h = 1\n", "tests/sib.py": "",
         "tests/unrelated.py": "", "pkg/__init__.py": "", "pkg/mod.py": "", "pkg/other.py": "",
         "pkg/deep/__init__.py": "", "pkg/deep/leaf.py": ""}
T = "tests/test_a.py::test_t"


@pytest.fixture
def repo(tmp_path):
    """c0 → (fix) on origin/dev; ``head(base)`` makes a PR head forked from ``base``."""
    r = tmp_path / "repo"
    r.mkdir()
    _g(r, "init", "-q", "-b", "dev")
    for rel, text in FILES.items():
        (r / rel).parent.mkdir(parents=True, exist_ok=True)
        (r / rel).write_text(text)
    _g(r, "add", "-A")
    _g(r, "commit", "-qm", "c0")
    n = [0]

    def commit(touch=None, base=None):
        if base:
            _g(r, "checkout", "-q", "--detach", base)
        n[0] += 1
        rel = touch or f"pr{n[0]}.txt"
        with open(r / rel, "a") as fh:
            fh.write(f"# {n[0]}\n")
        _g(r, "add", rel)
        _g(r, "commit", "-qm", f"c{n[0]}")
        return _g(r, "rev-parse", "HEAD")

    def publish(sha):
        _g(r, "update-ref", "refs/remotes/origin/dev", sha)

    c0 = _g(r, "rev-parse", "HEAD")
    publish(c0)
    return SimpleNamespace(path=r, c0=c0, commit=commit, publish=publish,
                           workflow=str(r / "WORKFLOW.md"))


def ci_flakes(env, rows):
    """rows: (task, sha, workflow_path)."""
    import sqlite3
    from datetime import datetime, timezone

    at = datetime.fromtimestamp(NOW - 3600, timezone.utc).isoformat()
    conn = sqlite3.connect(env.dir / "scheduler.db")
    conn.execute("CREATE TABLE runs (task_id TEXT, status TEXT, started_at TEXT, "
                 "ci_flakes TEXT, workflow_path TEXT)")
    conn.executemany("INSERT INTO runs VALUES (?, 'done', '', ?, ?)",
                     [(t, json.dumps([{"test": T, "sha": sha, "at": at}]), wf)
                      for t, sha, wf in rows])
    conn.commit()
    conn.close()


def quarantine(env):
    return [f["evidence"]["tasks"] for f in ops.rule_repeat_flake(src(env), {})
            if f["subject"] == f"ci:{T}"]


@pytest.mark.parametrize("touch, counted", [
    ("tests/test_a.py", False),          # the test itself
    ("pkg/mod.py", False),               # `from pkg import mod`
    ("pkg/__init__.py", False),          # `from pkg import …`'s package
    ("pkg/deep/leaf.py", False),         # `import pkg.deep.leaf`
    ("pkg/deep/__init__.py", False),     # …which runs pkg/deep/__init__.py first
    ("tests/helper.py", False),          # `from .helper import h`
    ("tests/sib.py", False),             # `from . import sib`
    ("pkg/other.py", True),              # NEGATIVE: not imported — the old flakes stand
    ("tests/unrelated.py", True),        # NEGATIVE: a neighbour of the test, not imported
])
def test_flakes_from_before_a_fix_of_the_test_or_its_imports_do_not_count(
        env, repo, touch, counted):
    heads = [repo.commit(base=repo.c0) for _ in range(3)]
    repo.publish(repo.commit(touch=touch, base=repo.c0))
    ci_flakes(env, [(f"CMX-{i}", h, repo.workflow) for i, h in enumerate(heads)])
    assert quarantine(env) == ([{"CMX-0": NOW - 3600, "CMX-1": NOW - 3600,
                                 "CMX-2": NOW - 3600}] if counted else [])


def test_flakes_on_heads_that_contain_the_fix_count(env, repo):
    fix = repo.commit(touch="tests/test_a.py", base=repo.c0)
    repo.publish(fix)
    old = repo.commit(base=repo.c0)
    heads = [repo.commit(base=fix) for _ in range(2)]
    ci_flakes(env, [("CMX-1", heads[0], repo.workflow), ("CMX-2", heads[1], repo.workflow),
                    ("CMX-3", old, repo.workflow)])
    assert [sorted(t) for t in quarantine(env)] == [["CMX-1", "CMX-2"]]


def test_the_fix_is_read_from_origin_base_not_the_checkout(env, repo):
    heads = [repo.commit(base=repo.c0) for _ in range(2)]
    repo.commit(touch="tests/test_a.py", base=repo.c0)   # a local fix, never on origin/dev
    ci_flakes(env, [("CMX-1", heads[0], repo.workflow), ("CMX-2", heads[1], repo.workflow)])
    assert len(quarantine(env)) == 1


@pytest.mark.parametrize("break_it", ["unknown_sha", "no_sha", "no_workflow", "bad_workflow",
                                      "no_origin_ref", "test_not_on_base", "unparseable"])
def test_a_fix_git_cannot_prove_leaves_the_flakes_counted(env, repo, break_it):
    heads = [repo.commit(base=repo.c0) for _ in range(2)]
    repo.publish(repo.commit(touch="tests/test_a.py", base=repo.c0))
    wf = repo.workflow
    if break_it == "unknown_sha":
        heads[0] = "0" * 40
    elif break_it == "no_sha":
        heads[0] = ""
    elif break_it == "no_workflow":
        wf = None
    elif break_it == "bad_workflow":
        (repo.path / "WORKFLOW.md").write_text("no front matter\n")
    elif break_it == "no_origin_ref":
        _g(repo.path, "update-ref", "-d", "refs/remotes/origin/dev")
    elif break_it == "test_not_on_base":
        _g(repo.path, "rm", "-q", "tests/test_a.py")
        _g(repo.path, "commit", "-qm", "rm")
        repo.publish(_g(repo.path, "rev-parse", "HEAD"))
    ci_flakes(env, [("CMX-1", heads[0], wf), ("CMX-2", heads[1], wf)])
    if break_it == "unparseable":                # imports unreadable: the test file alone
        (repo.path / "tests/test_a.py").write_text("def (:\n")
        _g(repo.path, "commit", "-qam", "broken")
        repo.publish(_g(repo.path, "rev-parse", "HEAD"))
        assert quarantine(env) == []             # that commit touched the test itself
        return
    want = {"CMX-1", "CMX-2"} - ({"CMX-2"} if break_it in ("unknown_sha", "no_sha") else set())
    got = quarantine(env)
    assert [set(t) for t in got] == ([want] if len(want) >= 2 else [])
    if len(want) < 2:                            # only CMX-1 proves nothing on its own: pin
        assert src(env).predates_fix({"test": T, "sha": heads[0], "workflow": wf}) is False


V = "tests/x.test.mjs > a click opens the modal"


def _vitest_flakes(env, rows):
    """rows: (task, sha, workflow_path, test) — CMX-351's ids, not only pytest's."""
    import sqlite3
    from datetime import datetime, timezone

    at = datetime.fromtimestamp(NOW - 3600, timezone.utc).isoformat()
    conn = sqlite3.connect(env.dir / "scheduler.db")
    conn.execute("CREATE TABLE runs (task_id TEXT, status TEXT, started_at TEXT, "
                 "ci_flakes TEXT, workflow_path TEXT)")
    conn.executemany("INSERT INTO runs VALUES (?, 'done', '', ?, ?)",
                     [(t, json.dumps([{"test": tid, "sha": sha, "at": at}]), wf)
                      for t, sha, wf, tid in rows])
    conn.commit()
    conn.close()


def test_a_repeat_vitest_flaker_is_a_quarantine_candidate_and_unnamed_ones_never_are(env):
    """🔎 CMX-351: a Vitest id counts like a pytest node id; a flake whose test was not
    identified (None, the old "?", or a whole log line) is counted under no name at all."""
    _vitest_flakes(env, [("CMX-1", "", None, V), ("CMX-2", "", None, V),
                         ("CMX-3", "", None, None), ("CMX-4", "", None, None),
                         ("CMX-5", "", None, "re-register failed nope"),
                         ("CMX-6", "", None, "re-register failed nope")])
    assert [(f["subject"], sorted(f["evidence"]["tasks"]))
            for f in ops.rule_repeat_flake(src(env), {})] == [(f"ci:{V}", ["CMX-1", "CMX-2"])]


@pytest.mark.parametrize("touch, counted", [
    ("tests/x.test.mjs", False),          # the spec itself was fixed after these heads
    ("tests/unrelated.py", True),         # NEGATIVE: not the spec — the flakes stand
])
def test_a_vitest_flake_from_before_its_spec_was_fixed_does_not_count(env, repo, touch,
                                                                     counted):
    heads = [repo.commit(base=repo.c0) for _ in range(2)]
    repo.publish(repo.commit(touch=touch, base=repo.c0))
    _vitest_flakes(env, [(f"CMX-{i}", h, repo.workflow, V) for i, h in enumerate(heads)])
    got = [f["subject"] for f in ops.rule_repeat_flake(src(env), {})]
    assert got == ([f"ci:{V}"] if counted else [])


def test_runs_without_a_workflow_path_column_still_count(env):
    import sqlite3
    from datetime import datetime, timezone

    at = datetime.fromtimestamp(NOW - 60, timezone.utc).isoformat()
    conn = sqlite3.connect(env.dir / "scheduler.db")
    conn.execute("CREATE TABLE runs (task_id TEXT, status TEXT, started_at TEXT, ci_flakes TEXT)")
    conn.executemany("INSERT INTO runs VALUES (?, 'done', '', ?)",
                     [(t, json.dumps([{"test": T, "sha": "a" * 40, "at": at}]))
                      for t in ("CMX-1", "CMX-2")])
    conn.commit()
    conn.close()
    assert [sorted(t) for t in quarantine(env)] == [["CMX-1", "CMX-2"]]


def test_git_is_called_with_its_repo_and_a_timeout(env, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: calls.append((argv, kw)) or "ok")
    assert src(env)._git(tmp_path, "log", "-1") == "ok"
    assert calls == [(["git", "-C", str(tmp_path), "log", "-1"],
                      {"capture_output": True, "text": True, "errors": "replace",
                       "timeout": 5})]


@pytest.mark.parametrize("exc", [FileNotFoundError("git"),
                                 subprocess.TimeoutExpired(["git"], 5)])
def test_git_that_cannot_run_is_none_and_the_flake_counts(env, monkeypatch, tmp_path, exc):
    def boom(*a, **k):
        raise exc
    monkeypatch.setattr(subprocess, "run", boom)
    assert src(env)._git(tmp_path, "log") is None
    s = src(env)
    monkeypatch.setattr(s, "last_fix", lambda wf, f: (tmp_path, "f" * 40))
    assert s.predates_fix({"test": T, "sha": "a" * 40, "workflow": "w"}) is False


def test_imported_paths_are_the_direct_imports_as_files():
    assert ops._imported_paths(TEST_SRC, "tests/test_a.py") == {
        "os.py", "os/__init__.py", "pkg.py", "pkg/__init__.py", "pkg/mod.py",
        "pkg/mod/__init__.py", "pkg/deep/__init__.py", "pkg/deep/leaf.py",
        "pkg/deep/leaf/__init__.py", "tests/__init__.py", "tests/helper.py",
        "tests/helper/__init__.py", "tests/helper/h.py", "tests/helper/h/__init__.py",
        "tests.py", "tests/sib.py", "tests/sib/__init__.py"}
    assert ops._imported_paths("from .. import up\nfrom ... import gone\n", "tests/t.py") == {
        "up.py", "up/__init__.py"}


def test_the_tree_is_listed_once_per_repo_and_ref(env, repo):
    s, calls = src(env), []
    real = s._git
    s._git = lambda r, *a: calls.append(a[0]) or real(r, *a)
    assert s.last_fix(repo.workflow, "tests/test_a.py") is not None
    assert s.last_fix(repo.workflow, "tests/sib.py") is not None
    assert calls.count("ls-tree") == 1


def test_a_known_quarantine_finding_is_not_renotified_across_restarts(env, monkeypatch):
    import sqlite3
    from datetime import datetime, timezone

    sent = []
    monkeypatch.setattr("chela.inbox.enqueue", lambda *a: sent.append(a) or True)
    monkeypatch.setattr("chela.event_log.log_path", lambda: env.dir / "events.jsonl")
    clock = [NOW]
    monkeypatch.setattr(ops.time, "time", lambda: clock[0])

    def flaked(*tasks):
        at = datetime.fromtimestamp(clock[0] - 60, timezone.utc).isoformat()
        conn = sqlite3.connect(env.dir / "scheduler.db")
        conn.execute("CREATE TABLE IF NOT EXISTS runs (task_id TEXT, status TEXT, "
                     "started_at TEXT, ci_flakes TEXT)")
        conn.executemany("INSERT INTO runs VALUES (?, 'done', '', ?)",
                         [(t, json.dumps([{"test": T, "at": at}])) for t in tasks])
        conn.commit()
        conn.close()

    flaked("CMX-1", "CMX-2")
    ops.scheduled(env.dir, env.proc)
    for _ in range(3):                           # each a fresh process over the saved state
        clock[0] += ops.RENOTIFY_S + 1
        ops.scheduled(env.dir, env.proc)
    assert len(sent) == 1
    flaked("CMX-3")                              # a NEW PR flaked: that is news
    clock[0] += 60
    ops.scheduled(env.dir, env.proc)
    assert len(sent) == 2 and "CMX-1, CMX-2, CMX-3" in sent[1][1]
    clock[0] += 60
    ops.scheduled(env.dir, env.proc)
    assert len(sent) == 2
    state = json.loads((env.dir / "ops" / "state.json").read_text())
    assert state["sigs"] == {f"repeat_flake:ci:{T}": "CMX-1,CMX-2,CMX-3"}


def test_a_finding_without_a_sig_still_renotifies_and_leaves_no_sig(env):
    f = ops._finding("leaked_proc", "pid:1", "x")
    assert "sig" not in f
    state = {"notified": {"leaked_proc:pid:1": NOW - ops.RENOTIFY_S}, "sigs": {
        "leaked_proc:pid:1": "stale"}}
    result = {"ts": NOW, "findings": [f], "rules_done": ["leaked_proc"], "errors": {}}
    assert ops.record(env.dir, result, state, src(env)) == [f]
    assert json.loads((env.dir / "ops" / "state.json").read_text())["sigs"] == {}


def test_a_sigd_finding_that_resolves_forgets_its_sig(env):
    key = f"repeat_flake:ci:{T}"
    state = {"notified": {key: NOW - 60}, "sigs": {key: "CMX-1,CMX-2"}}
    result = {"ts": NOW, "findings": [], "rules_done": ["repeat_flake"], "errors": {}}
    ops.record(env.dir, result, state, src(env))
    saved = json.loads((env.dir / "ops" / "state.json").read_text())
    assert saved["notified"] == {} and saved["sigs"] == {}


# --- rule 5: judge whack-a-mole (CMX-176/259) ----------------------------------------------

def judge_body(*locs):
    return "## ⚖️ THE JUDGE\n" + "".join(
        f"### n\n\n**File:** `{f}`\n\n```diff\n- {line}\n+ x\n```\n" for f, line in locs)


@pytest.mark.parametrize("rounds, hit", [
    ([[("a.py", "x")], [("b.py", "x")], [("c.py", "x")]], True),
    ([[("a.py", "x")], [("a.py", "y")], [("a.py", "z")]], True),        # moved line, same file
    ([[("a.py", "x")], [("b.py", "x")], [("a.py", "x"), ("c.py", "q")]], True),
    ([[("a.py", "x")], [("a.py", "x")], [("b.py", "x")]], False),       # one stubborn guard
    ([[("a.py", "x")], [("b.py", "x")]], False),                        # only 2 rounds
])
def test_whack_a_mole_rule(env, rounds, hit):
    reviews = [{"round": i, "body": judge_body(*locs)} for i, locs in enumerate(rounds)]
    reviews.insert(1, {"round": 9, "body": "human review, no survivors"})
    write_events(env, [{"seq": 3, "type": "run_changes_requested",
                        "payload": {"task_id": "CMX-1", "reviews": reviews[:1]}},
                       {"seq": 4, "type": "run_changes_requested",
                        "payload": {"task_id": "CMX-1", "reviews": reviews}}])
    got = ops.rule_judge_whack_a_mole(src(env), {})
    assert [(f["subject"], f["evidence"]["seq"]) for f in got] == ([("CMX-1", 4)] if hit else [])


# --- rule 6: notice storm (CMX-250) --------------------------------------------------------

@pytest.mark.parametrize("spec, hit", [
    ([("run_stuck", "CMX-1", 0), ("run_stuck", "CMX-1", 1700), ("run_stuck", "CMX-1", 3600)], True),
    ([("run_stuck", "CMX-1", 0), ("run_stuck", "CMX-1", 1700), ("run_stuck", "CMX-1", 3601)], False),
    ([("run_stuck", "CMX-1", 0), ("run_stuck", "CMX-2", 10), ("run_stuck", "CMX-1", 20)], False),
    ([("run_stuck", "CMX-1", 0), ("run_ci_red", "CMX-1", 10), ("run_stuck", "CMX-1", 20)], False),
    ([("daemon_start", "CMX-1", 0), ("daemon_start", "CMX-1", 1), ("daemon_start", "CMX-1", 2)], False),
    ([("run_stuck", "CMX-1", t) for t in (0, 10, 20, 30, 40)], True),   # one finding per burst
])
def test_notice_storm_rule(env, spec, hit):
    write_events(env, [{"seq": i, "type": k, "ts": NOW + t, "payload": {"task_id": task}}
                       for i, (k, task, t) in enumerate(spec)])
    got = ops.rule_notice_storm(src(env), {})
    assert [(f["subject"], f["evidence"]["seqs"]) for f in got] == (
        [("run_stuck:CMX-1", [0, 1, 2])] if hit else [])


def test_events_are_read_from_the_tail_only(env, monkeypatch):
    monkeypatch.setattr(ops, "TAIL_BYTES", 300)
    old = {"seq": 1, "type": "run_ci_red", "payload": {"task_id": "OLD", "failing": []}}
    write_events(env, [old] + [{"seq": i, "type": "x", "pad": "p" * 40} for i in range(2, 9)])
    assert [e["seq"] for e in src(env).events] == [6, 7, 8]       # ~75-byte lines; 1st is cut


def test_events_default_to_the_core_log(env):
    """CMX-328: every rule reads chela's own events, which the hook traffic in
    ``events.jsonl`` pushed out of a 2 MB tail within the hour."""
    assert ops.Sources(env.dir, env.proc).events_path == env.dir / "events-core.jsonl"


def test_events_are_topped_up_from_the_rolled_core_log(env, monkeypatch):
    monkeypatch.setattr(ops, "TAIL_BYTES", 300)
    (env.dir / "events-core.jsonl.1").write_text(
        "".join(json.dumps({"seq": i, "type": "x", "pad": "p" * 40}) + "\n" for i in range(1, 6)))
    write_events(env, [{"seq": 6, "type": "x", "pad": "p" * 40}])
    # ~75-byte lines: 225 bytes of budget left from .1 holds its last 2 whole lines
    assert [e["seq"] for e in src(env).events] == [4, 5, 6]


def test_a_full_current_tail_reads_no_rolled_lines(env, monkeypatch):
    monkeypatch.setattr(ops, "TAIL_BYTES", 300)
    (env.dir / "events-core.jsonl.1").write_text(json.dumps({"seq": 1, "type": "x"}) + "\n")
    write_events(env, [{"seq": i, "type": "x", "pad": "p" * 40} for i in range(2, 9)])
    assert [e["seq"] for e in src(env).events] == [6, 7, 8]


def _hook_flooded(env):
    """A ``run_ci_red`` the core log holds, and a main log of nothing but hooks."""
    write_events(env, [{"seq": 7, "ts": NOW, "type": "run_ci_red",
                        "payload": {"task_id": "CMX-1", "failing": [], "ci_sha": "abcdef123"}}])
    (env.dir / "events.jsonl").write_text(
        "".join(json.dumps({"seq": i, "type": "hook.stop"}) + "\n" for i in range(8, 20)))


def test_scheduled_reads_the_core_log(env, monkeypatch):
    _hook_flooded(env)
    monkeypatch.setattr("chela.event_log.log_path", lambda: env.dir / "events.jsonl")
    monkeypatch.setattr("chela.inbox.enqueue", lambda *a: True)
    monkeypatch.setattr(ops.time, "time", lambda: NOW)
    got = ops.scheduled(env.dir, env.proc)
    assert [f["subject"] for f in got["findings"] if f["rule"] == "ci_red_unsettled"] == [
        "CMX-1@abcdef1"]


def test_the_cli_scan_reads_the_core_log(env, monkeypatch, capsys):
    _hook_flooded(env)
    monkeypatch.setattr("chela.event_log.log_path", lambda: env.dir / "events.jsonl")
    monkeypatch.setattr(main.config, "CHELA_DIR", env.dir)
    seen = []
    monkeypatch.setattr(ops, "scan", lambda src, state: seen.append(src.events_path) or {
        "findings": []})
    main.cmd_ops_scan(SimpleNamespace(notify=False, json=True))
    assert seen == [env.dir / "events-core.jsonl"]


# --- budget, crash isolation ---------------------------------------------------------------

def test_budget_constants_are_the_documented_ones():
    assert (ops.BUDGET_S, ops.INTERVAL_S, ops.RENOTIFY_S) == (10.0, 1800, 21600)


def test_a_slow_source_yields_a_partial_result_within_the_budget(env):
    release = threading.Event()

    def fast(s, st):
        return [ops._finding("fast", "s", "found")]

    def slow(s, st):
        release.wait(30)
        return [ops._finding("slow", "s", "late")]

    t0 = time.monotonic()
    res = ops.scan(src(env), budget=0.5, rules=(fast, slow, fast))
    release.set()
    assert time.monotonic() - t0 < 2
    assert (res["partial"], res["rules_done"], [f["rule"] for f in res["findings"]]) == (
        True, ["fast"], ["fast"])


def test_a_rule_that_raises_is_reported_and_the_rest_still_run(env):
    def boom(s, st):
        raise RuntimeError("bad source")

    res = ops.scan(src(env), rules=(boom, ops.rule_notice_storm))
    assert res["partial"] is False and res["errors"] == {"boom": "RuntimeError: bad source"}
    assert res["rules_done"] == ["boom", "notice_storm"]


# --- scheduled: record, dedupe, pause, read-only --------------------------------------------

@pytest.fixture
def leaky(env, monkeypatch):
    set_runs(env, [("CMX-1", "done", "2026-01-01")])
    add_proc(env, 42, ["sleep"], env.wt / "CMX-1")
    sent = []
    monkeypatch.setattr("chela.inbox.enqueue", lambda *a: sent.append(a) or True)
    monkeypatch.setattr("chela.event_log.log_path", lambda: env.dir / "events.jsonl")
    clock = [NOW]
    monkeypatch.setattr(ops.time, "time", lambda: clock[0])
    return SimpleNamespace(sent=sent, clock=clock,
                           run=lambda: ops.scheduled(env.dir, env.proc))


def test_scheduled_notifies_once_then_renotifies_after_6h(env, leaky):
    leaky.run()
    leaky.clock[0] += 6 * 3600 - 1
    leaky.run()
    assert [(k, len(p["findings"])) for k, _, p in leaky.sent] == [("ops_findings", 1)]
    assert "pid 42 under run CMX-1" in leaky.sent[0][1]
    leaky.clock[0] += 1
    leaky.run()
    assert len(leaky.sent) == 2
    lines = [json.loads(x) for x in (env.dir / "ops" / "findings.jsonl").read_text().splitlines()]
    assert [(f["subject"], f["ts"]) for f in lines] == [("pid:42", NOW), ("pid:42", NOW + 6 * 3600)]


def test_a_rule_that_raises_does_not_forget_what_it_found(env, leaky, monkeypatch):
    leaky.run()
    with monkeypatch.context() as m:
        m.setattr(ops.Sources, "runs", property(lambda s: 1 / 0))
        leaky.clock[0] += 60
        assert leaky.run()["errors"]["leaked_proc"].startswith("ZeroDivisionError")
    leaky.clock[0] += 60
    leaky.run()
    assert len(leaky.sent) == 1


def test_cpu_samples_are_kept_for_test_runs_under_worktrees_only(env, leaky):
    add_proc(env, 50, ["/v/bin/pytest"], env.wt / "CMX-1", cpu=900.0)
    add_proc(env, 51, ["/v/bin/pytest"], env.dir, cpu=900.0)
    leaky.run()
    cpu = json.loads((env.dir / "ops" / "state.json").read_text())["cpu"]
    assert cpu == {f"50:{int((UPTIME - 1800) * HZ)}": [NOW, 900.0]}


@pytest.mark.parametrize("done, errors, kept", [
    (["hung_test"], {"hung_test": "OSError: x"}, True),    # it raised: proves nothing
    ([], {}, True),                                         # the budget never reached it
    (["hung_test"], {}, False),                             # it ran clean: resampled
])
def test_cpu_samples_are_resampled_only_by_a_clean_hung_test(env, done, errors, kept):
    add_proc(env, 50, ["/v/bin/pytest"], env.wt / "CMX-1", cpu=900.0)
    old = {"cpu": {"50:1": [NOW - 60, 1.0]}}
    result = {"ts": NOW, "findings": [], "rules_done": done, "errors": errors}
    ops.record(env.dir, result, old, src(env))
    cpu = json.loads((env.dir / "ops" / "state.json").read_text())["cpu"]
    assert cpu == (old["cpu"] if kept else {f"50:{int((UPTIME - 1800) * HZ)}": [NOW, 900.0]})


def test_a_resolved_finding_that_recurs_is_new_again(env, leaky):
    leaky.run()
    os.unlink(env.proc / "42" / "stat")                          # the process is gone
    leaky.clock[0] += 60
    leaky.run()
    add_proc(env, 43, ["sleep"], env.wt / "CMX-1")
    os.rename(env.proc / "43" / "stat", env.proc / "42" / "stat")
    leaky.clock[0] += 60
    leaky.run()
    assert len(leaky.sent) == 2


@pytest.mark.parametrize("pause", ["{}", "not json", None])
def test_scheduled_skips_while_a_fleet_pause_is_active(env, leaky, pause):
    if pause is None:                                   # a dangling symlink still means paused
        os.symlink(env.dir / "nowhere", env.dir / "fleet-pause.json")
    else:
        (env.dir / "fleet-pause.json").write_text(pause)
    assert leaky.run()["skipped"].startswith("a fleet pause")
    assert leaky.sent == [] and not (env.dir / "ops").exists()


def test_scheduled_notifies_a_leaked_daemon_once_on_its_second_scan(env, leaky, tmp_path):
    os.unlink(env.proc / "42" / "stat")                          # only the daemon this time
    home = tmp_path / "chela-tests-pm2-x"
    home.mkdir()
    add_proc(env, 43, [GOD], env.wt / "CMX-2", ppid=1, age=120, environ=[f"PM2_HOME={home}"])
    leaky.run()
    assert leaky.sent == []
    state = json.loads((env.dir / "ops" / "state.json").read_text())
    assert state["daemons"] == [f"43:{int((UPTIME - 120) * HZ)}"]
    for _ in range(2):
        leaky.clock[0] += 60
        leaky.run()
    assert len(leaky.sent) == 1
    assert "do not kill while a suite runs" in leaky.sent[0][1]


@pytest.mark.parametrize("kind", ["daemons", "orphans"])
@pytest.mark.parametrize("done, errors, kept", [
    (["leaked_proc"], {"leaked_proc": "OSError: x"}, True),    # it raised: proves nothing
    ([], {}, True),                                             # the budget never reached it
    (["leaked_proc"], {}, False),                               # it ran clean: replaced
])
def test_suspects_are_replaced_only_by_a_clean_leaked_proc(env, done, errors, kept, kind):
    s = src(env)
    getattr(s, f"{kind[:-1]}_suspects").add("7:7")
    old = {kind: ["42:1"]}
    result = {"ts": NOW, "findings": [], "rules_done": done, "errors": errors}
    ops.record(env.dir, result, old, s)
    got = json.loads((env.dir / "ops" / "state.json").read_text())
    assert got[kind] == (["42:1"] if kept else ["7:7"])
    assert got["daemons" if kind == "orphans" else "orphans"] == []


def test_scheduled_notifies_a_persistent_orphan_once_and_a_transient_one_never(env, leaky):
    """CMX-332 end to end: the 10-22 leak (2 days old) is one notice on the 2nd scan; a test
    child re-parented to 1 for a moment, gone by the next scan, is never one."""
    os.unlink(env.proc / "42" / "stat")
    add_proc(env, 43, ["node", "hang.test.mjs"], env.wt / "CMX-2", ppid=1,
             age=2 * 86400)
    add_proc(env, 44, ["tmux", "new-session", "-d", "-s", "cmx340", "sleep", "60"],
             env.wt / "CMX-3", ppid=1, age=5)
    leaky.run()
    assert leaky.sent == []
    state = json.loads((env.dir / "ops" / "state.json").read_text())
    assert state["orphans"] == [f"43:{int((UPTIME - 2 * 86400) * HZ)}",
                                f"44:{int((UPTIME - 5) * HZ)}"]
    shutil.rmtree(env.proc / "44")                              # reaped by its test
    for _ in range(2):
        leaky.clock[0] += 60
        leaky.run()
    assert [[f["subject"] for f in p["findings"]] for _, _, p in leaky.sent] == [["pid:43"]]


def test_scheduled_is_read_only_outside_the_ops_dir(env, leaky, monkeypatch):
    def snapshot():
        return {p: p.stat().st_mtime_ns for p in env.dir.rglob("*") if p.is_file()}

    before = snapshot()
    kills = []
    monkeypatch.setattr(os, "kill", lambda *a: kills.append(a))
    monkeypatch.setattr(os, "killpg", lambda *a: kills.append(a))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: kills.append(a))
    leaky.run()
    changed = {p for p, m in snapshot().items() if before.get(p) != m}
    assert {p.relative_to(env.dir).as_posix() for p in changed} == {
        "ops/findings.jsonl", "ops/state.json"}
    assert kills == [] and len(leaky.sent) == 1


@pytest.mark.parametrize("n, tail", [(3, " — chela ops scan"), (4, " (+1 more) — chela ops scan")])
def test_the_notice_lists_three_and_counts_the_rest(env, leaky, n, tail):
    for pid in range(43, 42 + n):
        add_proc(env, pid, ["sleep"], env.wt / "CMX-1")
    leaky.run()
    (kind, summary, payload), = leaky.sent
    assert summary.endswith(tail) and summary.startswith(f"🔎 ops scan: {n} new finding(s) — ")
    assert len(payload["findings"]) == n


def test_render_says_skipped_and_partial():
    assert ops.render({"skipped": "paused"}) == "ops scan: skipped — paused"
    out = ops.render({"findings": [], "partial": True, "rules_done": ["a"], "errors": {"b": "E"}})
    assert out == "ops scan: 0 finding(s) — PARTIAL (finished: a)\n  rule b raised E"


@pytest.mark.parametrize("notify", [True, False])
def test_the_cli_routes_ops_scan(monkeypatch, capsys, notify):
    monkeypatch.setattr(sys, "argv", ["chela", "ops", "scan", *(["--notify"] if notify else [])])
    monkeypatch.setattr(main.ops, "scheduled", lambda: {"skipped": "notify-path"})
    monkeypatch.setattr(main.ops, "scan", lambda *a: {"skipped": "plain-path"})
    main.main()
    assert capsys.readouterr().out == f"ops scan: skipped — {'notify' if notify else 'plain'}-path\n"


def test_a_command_after_ops_is_not_routed_to_it(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["chela", "adopt", "346", "--workflow", "W.md"])
    monkeypatch.setattr(main, "cmd_ops_scan", lambda a: pytest.fail("routed to ops"))
    seen = []
    monkeypatch.setattr(main, "cmd_adopt", lambda a: seen.append(a.pr))
    main.main()
    assert seen == ["346"]


def test_the_cli_without_notify_writes_nothing(env, monkeypatch, capsys):
    monkeypatch.setattr(main.config, "CHELA_DIR", env.dir)
    monkeypatch.setattr("chela.event_log.log_path", lambda: env.dir / "events.jsonl")
    write_events(env, [{"seq": 5, "type": "run_ci_red", "payload": {"task_id": "T", "failing": []}}])
    main.cmd_ops_scan(SimpleNamespace(notify=False, json=True))
    assert [f["subject"] for f in json.loads(capsys.readouterr().out)["findings"]] == ["T@"]
    assert not (env.dir / "ops").exists()


# --- spawning: detached, niced, never doubled; the daemon loop wiring -----------------------

def test_spawn_is_detached_niced_and_not_doubled(monkeypatch, tmp_path):
    monkeypatch.setattr(ops.config, "CHELA_DIR", tmp_path)
    monkeypatch.setattr(ops.shutil, "which", lambda name: f"/usr/bin/{name}")
    calls = []
    child = SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(ops.subprocess, "Popen", lambda argv, **kw: calls.append((argv, kw)) or child)
    assert ops.spawn() is child and ops.spawn(child) is child
    (argv, kw), = calls
    done, fresh = SimpleNamespace(poll=lambda: 0), SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(ops.subprocess, "Popen", lambda argv, **kw: calls.append(argv) or fresh)
    assert ops.spawn(done) is fresh and len(calls) == 2      # a FINISHED scan is replaced
    assert argv == ["nice", "-n", "19", "ionice", "-c", "3", sys.executable, "-m",
                    "chela.main", "ops", "scan", "--notify"]
    assert (kw["start_new_session"], kw["stdin"], kw["stdout"].name) == (
        True, subprocess.DEVNULL, str(tmp_path / "ops" / "scan.log"))
    monkeypatch.setattr(ops.shutil, "which", lambda name: None)
    assert ops.spawn_argv()[:1] == [sys.executable]


def test_the_daemon_spawns_the_scan_once_per_interval_and_survives_its_crash(monkeypatch):
    _run_daemon_ticks(monkeypatch, n=2)
    monkeypatch.setattr(main.doctor, "check_and_notify", lambda red: red)
    spawned, after = [], []
    monkeypatch.setattr(main.ops, "spawn", lambda prev: spawned.append(prev) or "child")
    monkeypatch.setattr(main.update, "check_and_notify", lambda b: after.append(b) or b)
    main.cmd_run(SimpleNamespace())
    assert spawned == [None]

    # Due every tick: the second spawn must be handed the FIRST tick's child, or a scan
    # still running is doubled (spawn() only refuses to double the child it is given).
    _run_daemon_ticks(monkeypatch, n=3)
    monkeypatch.setattr(main.ops, "INTERVAL_S", 0)
    spawned.clear()
    children = iter(["child-1", "child-2", "child-3"])
    monkeypatch.setattr(main.ops, "spawn", lambda prev: spawned.append(prev) or next(children))
    main.cmd_run(SimpleNamespace())
    assert spawned == [None, "child-1", "child-2"]

    def boom(prev):
        raise RuntimeError("no fork")

    _run_daemon_ticks(monkeypatch, n=1)
    monkeypatch.setattr(main.ops, "spawn", boom)
    after.clear()
    monkeypatch.setattr(main.update, "check_and_notify", lambda b: after.append(b) or b)
    main.cmd_run(SimpleNamespace())
    assert after == [0]
