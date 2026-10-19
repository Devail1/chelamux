"""CMX-260 — `chela ops scan`: one fixture per rule (a match AND a near-miss), the 10 s budget,
read-only-ness, dedupe, the fleet-pause skip, and the daemon wiring. Fake /proc, fake logs —
never the live fleet."""
from __future__ import annotations

import json
import os
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


def add_proc(env, pid, argv, cwd, *, ppid=500, age=1800, cpu=0.0):
    d = env.proc / str(pid)
    d.mkdir()
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
    (env.dir / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))


def set_runs(env, rows):
    import sqlite3
    conn = sqlite3.connect(env.dir / "scheduler.db")
    conn.execute("CREATE TABLE runs (task_id TEXT, status TEXT, started_at TEXT)")
    conn.executemany("INSERT INTO runs VALUES (?, ?, ?)", rows)
    conn.commit()
    conn.close()


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
    got = ops.rule_leaked_proc(src(env), {})
    assert [(f["subject"], f["evidence"]["task"]) for f in got] == (
        [("pid:42", "CMX-1")] if hit else [])


def test_leaked_proc_reads_argv_paths_and_skips_unreadable_procs(env):
    set_runs(env, [("CMX-2", "done", "2026-01-01")])
    add_proc(env, 42, ["pytest", str(env.wt / "CMX-2" / "tests")], env.dir)
    (env.proc / "43").mkdir()                                    # no stat: vanished mid-scan
    add_proc(env, 44, ["init"], env.dir, ppid=1)                 # orphan, but not ours
    add_proc(env, os.getpid(), ["pytest"], env.wt / "CMX-2", ppid=1)   # the scan itself
    add_proc(env, 45, [], env.wt / "CMX-2", ppid=1)               # a kernel thread: no argv
    add_proc(env, 99, ["x"], env.wt / "CMX-2")
    os.rename(env.proc / "99", env.proc / "self")                # /proc/self is not a pid
    assert [f["subject"] for f in ops.rule_leaked_proc(src(env), {})] == ["pid:42", "pid:45"]
    assert [f["subject"] for f in ops.rule_hung_test(src(env), {})] == ["pid:42"]


def test_without_a_runs_db_an_orphan_is_still_found(env):
    add_proc(env, 42, ["sleep"], env.wt / "CMX-1", ppid=1)
    assert [f["evidence"]["run_status"] for f in ops.rule_leaked_proc(src(env), {})] == [None]


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
