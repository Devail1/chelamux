"""🧠 CMX-393: a battery (sweep, self-check, judge battery, ``task-finished``) holds ONE suite
slot from start to finish; a battery killed mid-run is named in ONE aggregated notice."""
from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from chela import dispatcher, judge, procguard, suite_guard, sweep


@pytest.fixture
def one_slot(monkeypatch, tmp_path):
    monkeypatch.setattr(suite_guard.config, "CHELA_DIR", tmp_path)
    monkeypatch.setenv("CHELA_SUITE_SLOTS", "1")
    monkeypatch.setenv("CHELA_SUITE_MEMORY_MAX", "0")       # no systemd scope in a unit test
    monkeypatch.delenv("CHELA_SUITE_SLOT_MAX_WAIT", raising=False)
    monkeypatch.setattr(suite_guard, "_waited", 0.0)
    monkeypatch.setattr(suite_guard, "_thread_waited", {})
    return tmp_path


# --- two fake agents, both starting a sweep ----------------------------------------------

SUITES_PER_BATTERY = 3


def _two_agents(tmp_path: Path, monkeypatch) -> dict:
    """Agent A starts a sweep; once A's first suite is running, agent B starts one. Each
    sweep's battery runs ``SUITES_PER_BATTERY`` suites through the real ``judge.run_suite``
    (the shell itself is faked). Returns the suite runs in the order they ran, what
    ``suite_guard.holders()`` said while B was queued, and each agent's sweep report."""
    runs: list[tuple[str, float, float]] = []
    a_started, b_queued = threading.Event(), threading.Event()
    seen: dict = {"holders_while_b_waits": None}

    def fake_shell(cmd, cwd, env, timeout, frozen=None):
        name = Path(cwd).name
        t0 = time.monotonic()
        if name == "CMX-A" and not a_started.is_set():
            a_started.set()
            # Hold A's first suite until B is visibly queued (bounded: never hang the test).
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if any(rec.get("waiting_since") is not None and Path(rec["cwd"]).name == "CMX-B"
                       for _p, rec in suite_guard._ledgers()):
                    b_queued.set()
                    seen["holders_while_b_waits"] = suite_guard.holders()
                    break
                time.sleep(0.02)
        time.sleep(0.05)
        runs.append((name, t0, time.monotonic()))
        return SimpleNamespace(returncode=0, stdout="1 passed", stderr="")

    def fake_battery(worktree, base, test_cmd, **_kw):
        for _ in range(SUITES_PER_BATTERY):
            assert judge.run_suite(test_cmd, worktree, timeout=30).green
        return sweep.SweepReport()

    monkeypatch.setattr(procguard, "run_shell_group", fake_shell)
    monkeypatch.setattr(sweep, "_sweep", fake_battery)
    monkeypatch.setattr(suite_guard, "SLOT_POLL_SECONDS", 0.02)     # keeps the test quick
    reports: dict = {}

    def agent(name):
        wt = tmp_path / name
        wt.mkdir(exist_ok=True)
        reports[name] = sweep.sweep(wt, "origin/dev", "true", timeout=30)

    a = threading.Thread(target=agent, args=("CMX-A",), daemon=True)
    b = threading.Thread(target=agent, args=("CMX-B",), daemon=True)
    a.start()
    assert a_started.wait(10)
    b.start()
    a.join(30)
    b.join(30)
    assert not a.is_alive() and not b.is_alive()
    return {"runs": runs, "order": [n for n, _s, _e in runs], "b_queued": b_queued.is_set(),
            "holders": seen["holders_while_b_waits"], "reports": reports}


def _battery_isolated(runs: list[tuple[str, float, float]]) -> bool:
    """True when each agent's suites ran as ONE contiguous block that overlapped no other
    agent's suite — the whole battery inside one slot."""
    order = [n for n, _s, _e in runs]
    blocks = [n for i, n in enumerate(order) if i == 0 or order[i - 1] != n]
    overlap = any(s2 < e1 for (_n1, _s1, e1), (_n2, s2, _e2) in zip(runs, runs[1:]))
    return len(blocks) == len(set(order)) and not overlap


def test_a_second_agents_sweep_waits_for_the_whole_first_battery(one_slot, monkeypatch, capsys):
    out = _two_agents(one_slot, monkeypatch)
    # The second agent queued, and the holder it queued behind is the first agent.
    assert out["b_queued"]
    assert out["holders"] == ["CMX-A"]
    assert "waiting for a suite slot — all 1 busy: CMX-A (pid" in capsys.readouterr().err
    # The battery held the slot from its first suite to its last: A's three, then B's three.
    assert out["order"] == ["CMX-A"] * SUITES_PER_BATTERY + ["CMX-B"] * SUITES_PER_BATTERY
    assert _battery_isolated(out["runs"])
    assert all(not r.cannot_verify for r in out["reports"].values())


def test_negative_control_a_sweep_that_skips_the_battery_acquire_interleaves(one_slot,
                                                                              monkeypatch):
    """The bypass: no battery acquire, only the per-suite slot — the 11-14/11-17 shape. Both
    batteries run at once, mutation by mutation, and the isolation check goes RED."""
    @contextlib.contextmanager
    def bypass(*_a, **_kw):
        yield
    monkeypatch.setattr(suite_guard, "battery", bypass)
    out = _two_agents(one_slot, monkeypatch)
    assert out["b_queued"]
    assert out["order"][:2] == ["CMX-A", "CMX-B"]       # B ran before A's battery ended
    assert not _battery_isolated(out["runs"])


@pytest.mark.parametrize("runs,want", [
    ([("A", 0, 1), ("A", 1, 2), ("B", 2, 3)], True),
    ([("A", 0, 1), ("B", 1, 2), ("A", 2, 3)], False),       # interleaved
    ([("A", 0, 2), ("B", 1, 3)], False),                    # overlapping
])
def test_the_isolation_check_itself(runs, want):
    assert _battery_isolated(runs) is want


# --- the slot is re-entered inside a battery, never taken twice ---------------------------

def test_a_suite_inside_a_battery_reenters_its_slot(one_slot, monkeypatch):
    took = []
    real_take = suite_guard._take
    monkeypatch.setattr(suite_guard, "_take", lambda d, k: took.append(1) or real_take(d, k))
    with suite_guard.battery(one_slot / "CMX-1", "sweep", max_wait=1, poll=0.01):
        for _ in range(3):
            with suite_guard.slot(one_slot / "CMX-1", max_wait=1, poll=0.01):
                assert suite_guard.holders() == ["CMX-1"]
    assert took == [1]                       # ONE acquire for the battery and all 3 suites
    assert suite_guard._held.depth == 0
    with suite_guard.slot("/wt", max_wait=1, poll=0.01):    # and it was freed at the end
        pass


def test_another_thread_still_queues_behind_a_battery(one_slot):
    """Re-entry is per THREAD: a second judge thread in the same process is another agent."""
    out = []
    with suite_guard.battery(one_slot / "CMX-1", "judge", max_wait=1, poll=0.01):
        def other():
            try:
                with suite_guard.slot("/other", max_wait=0.2, poll=0.01):
                    out.append("admitted")
            except suite_guard.SlotTimeout as e:
                out.append(e)
        t = threading.Thread(target=other, daemon=True)
        t.start()
        t.join(5)
    assert len(out) == 1 and isinstance(out[0], suite_guard.SlotTimeout)


def test_slots_off_still_reenters_and_records(one_slot, monkeypatch):
    monkeypatch.setenv("CHELA_SUITE_SLOTS", "0")
    with suite_guard.battery(one_slot / "CMX-1", "task-finished"):
        assert suite_guard._held.depth == 1
        with suite_guard.battery(one_slot / "CMX-1", "sweep"):     # nested: no 2nd record
            assert suite_guard._held.depth == 2
        assert len(list((one_slot / "suite-slots" / "batteries").glob("*.json"))) == 1
    assert suite_guard._held.depth == 0


# --- the battery record: kept when a signal stops it, gone otherwise ----------------------

def _records(root: Path) -> list[dict]:
    return [json.loads(p.read_text())
            for p in (root / "suite-slots" / "batteries").glob("*.json")]


class _Signal(BaseException):
    """Stands in for the sweep's ``_Interrupted`` / a KeyboardInterrupt."""


@pytest.mark.parametrize("exc,kept", [
    (None, False), (RuntimeError("boom"), False), (SystemExit(1), False),
    (_Signal(), True), (KeyboardInterrupt(), True)])
def test_the_battery_record_is_kept_only_when_a_signal_stops_it(one_slot, exc, kept):
    with contextlib.suppress(BaseException):
        with suite_guard.battery(one_slot / "CMX-7", "self-check", max_wait=1, poll=0.01):
            [rec] = _records(one_slot)
            assert (rec["pid"], rec["cwd"], rec["kind"]) == (
                os.getpid(), str(one_slot / "CMX-7"), "self-check")
            if exc is not None:
                raise exc
    assert bool(_records(one_slot)) is kept


def test_a_nested_battery_keeps_the_outer_record(one_slot):
    with suite_guard.battery(one_slot / "CMX-7", "task-finished", max_wait=1, poll=0.01):
        with suite_guard.battery(one_slot / "CMX-7", "sweep", max_wait=1, poll=0.01):
            pass
        assert [r["kind"] for r in _records(one_slot)] == ["task-finished"]


def test_a_battery_record_that_cannot_be_written_never_fails_the_battery(one_slot, monkeypatch,
                                                                          caplog):
    monkeypatch.setattr(suite_guard, "_batteries_dir",
                        lambda: one_slot / "missing" / "x")
    (one_slot / "missing").write_text("a file, not a dir")      # mkdir raises OSError
    ran = []
    with caplog.at_level("DEBUG", logger="chela.suite_guard"):
        with suite_guard.battery(one_slot / "CMX-7", "sweep", max_wait=1, poll=0.01):
            ran.append(1)
    assert ran == [1] and "could not record the battery" in caplog.text


def _dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def _write_record(root: Path, name: str, **rec) -> Path:
    d = root / "suite-slots" / "batteries"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{name}.json"
    path.write_text(rec.pop("_raw", None) or json.dumps(rec))
    return path


def test_stopped_batteries_are_the_dead_and_the_reused_never_the_live(one_slot):
    me = suite_guard._proc_stat(os.getpid())[1]
    _write_record(one_slot, "live", pid=os.getpid(), start=me, cwd="/w/CMX-1", kind="sweep",
                  since=1.0)
    dead = _write_record(one_slot, "dead", pid=_dead_pid(), start="1", cwd="/w/CMX-2",
                         kind="self-check", since=3.0)
    reused = _write_record(one_slot, "reused", pid=os.getpid(), start="not-me",
                           cwd="/w/CMX-3", kind="sweep", since=2.0)
    _write_record(one_slot, "garbage", _raw="{not json")
    _write_record(one_slot, "nopid", cwd="/w/CMX-4")
    got = suite_guard.stopped_batteries()
    assert [p for p, _r in got] == [reused, dead]                # oldest first
    assert [r["cwd"] for _p, r in got] == ["/w/CMX-3", "/w/CMX-2"]


def test_stopped_batteries_with_no_dir_is_empty(one_slot):
    assert suite_guard.stopped_batteries() == []


# --- resumable: the slot free AND one suite's peak of memory available --------------------

def _meminfo(tmp_path: Path, kb: int | None) -> Path:
    f = tmp_path / "meminfo"
    f.write_text("MemTotal: 16000000 kB\n" + (f"MemAvailable: {kb} kB\n" if kb is not None
                                                 else ""))
    return f


def test_mem_available_reads_meminfo_and_fails_closed(tmp_path):
    assert suite_guard.mem_available_bytes(_meminfo(tmp_path, 1234)) == 1234 * 1024
    assert suite_guard.mem_available_bytes(_meminfo(tmp_path, None)) is None
    (tmp_path / "meminfo").write_text("MemAvailable: lots kB\n")
    assert suite_guard.mem_available_bytes(tmp_path / "meminfo") is None
    assert suite_guard.mem_available_bytes(tmp_path / "nope") is None


# 2,954,940,416 bytes = 2,885,684 kB: the measured suite peak, written out literally.
@pytest.mark.parametrize("kb,held,want", [
    (2_885_684, False, True), (2_885_683, False, False), (None, False, False),
    (10_000_000, True, False)])
def test_resumable_needs_a_free_slot_and_a_suites_peak_of_memory(one_slot, monkeypatch,
                                                                 kb, held, want):
    monkeypatch.setattr(suite_guard, "holders", lambda *_a: ["CMX-1"] if held else [])
    assert suite_guard.resumable(meminfo=_meminfo(one_slot, kb)) is want


def test_resumable_reads_the_real_holders(one_slot):
    meminfo = _meminfo(one_slot, 10_000_000)
    with suite_guard.slot(one_slot / "CMX-1", max_wait=1, poll=0.01):
        assert suite_guard.resumable(meminfo=meminfo) is False
    assert suite_guard.resumable(meminfo=meminfo) is True


# --- ONE aggregated notice for every stopped run ------------------------------------------

def _stopped_three(root: Path) -> list[Path]:
    return [_write_record(root, n, pid=_dead_pid(), start="1", cwd=f"/w/{n}", kind=k,
                          since=s)
            for n, k, s in (("CMX-385", "sweep", 1.0), ("CMX-389", "sweep", 2.0),
                            ("CMX-388", "self-check", 3.0))]


def test_three_stopped_runs_get_one_notice_naming_all_three(one_slot, monkeypatch):
    paths = _stopped_three(one_slot)
    monkeypatch.setattr(suite_guard, "resumable", lambda: True)
    queued = []
    from chela import inbox
    monkeypatch.setattr(inbox, "enqueue",
                        lambda kind, summary, payload: queued.append((kind, summary, payload))
                        or True)
    out = dispatcher.notice_stopped_batteries()
    [(kind, summary, payload)] = queued
    assert kind == "suite_runs_stopped"
    assert summary.startswith("🧠 3 suite run(s) were stopped mid-battery: CMX-385 (sweep), "
                              "CMX-389 (sweep), CMX-388 (self-check).")
    assert [r["worktree"] for r in payload["runs"]] == ["CMX-385", "CMX-389", "CMX-388"]
    assert out["notified"] is True and len(out["runs"]) == 3
    assert not any(p.exists() for p in paths)              # consumed: announced once
    assert dispatcher.notice_stopped_batteries() is None
    assert len(queued) == 1


def test_no_notice_until_the_slot_frees_and_memory_recovers(one_slot, monkeypatch):
    paths = _stopped_three(one_slot)
    monkeypatch.setattr(suite_guard, "resumable", lambda: False)
    from chela import inbox
    monkeypatch.setattr(inbox, "enqueue", lambda *a: pytest.fail("posted too early"))
    assert dispatcher.notice_stopped_batteries() is None
    assert all(p.exists() for p in paths)                  # kept for the next tick


def test_no_stopped_runs_no_notice(one_slot, monkeypatch):
    from chela import inbox
    monkeypatch.setattr(suite_guard, "resumable", lambda: True)
    monkeypatch.setattr(inbox, "enqueue", lambda *a: pytest.fail("nothing to say"))
    assert dispatcher.notice_stopped_batteries() is None


def test_the_notice_never_raises(one_slot, monkeypatch, caplog):
    def boom():
        raise RuntimeError("disk")
    monkeypatch.setattr(suite_guard, "stopped_batteries", boom)
    assert dispatcher.notice_stopped_batteries() is None
    assert "could not report the stopped suite runs" in caplog.text


def test_the_inbox_off_still_consumes_and_logs(one_slot, monkeypatch, caplog):
    from chela import inbox
    paths = _stopped_three(one_slot)
    monkeypatch.setattr(suite_guard, "resumable", lambda: True)
    monkeypatch.setattr(inbox, "enqueue", lambda *a: False)
    out = dispatcher.notice_stopped_batteries()
    assert out["notified"] is False and not any(p.exists() for p in paths)
    assert "NOT queued" in caplog.text


def test_the_daemon_tick_posts_it(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(dispatcher, "notice_stopped_batteries", lambda: called.append(1))
    monkeypatch.setattr(dispatcher, "get_source",
                        lambda wf: (_ for _ in ()).throw(RuntimeError("stop here")))
    wf = tmp_path / "WORKFLOW.md"
    wf.write_text(f"---\nproject_key: TEST\nworkspace:\n  root: {tmp_path / '.chela' / 'wts'}\n"
                  "---\nbody\n")
    with contextlib.suppress(Exception):
        dispatcher.tick(wf)
    assert called == [1]


# --- every battery entry point holds one slot for its whole run ---------------------------

@pytest.fixture
def battery_spy(monkeypatch):
    calls = []

    @contextlib.contextmanager
    def spy(cwd, kind, on_wait=None, max_wait=None, poll=None, rank=None):
        calls.append({"cwd": Path(cwd), "kind": kind, "on_wait": on_wait,
                      "max_wait": max_wait, "rank": rank, "inside": True})
        yield
        calls[-1]["inside"] = False
    monkeypatch.setattr(suite_guard, "battery", spy)
    monkeypatch.delenv("CHELA_SUITE_SLOT_MAX_WAIT", raising=False)
    return calls


def test_sweep_runs_its_battery_inside_one_slot(battery_spy, monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(sweep, "_sweep",
                        lambda *a, **k: seen.append(dict(battery_spy[-1])) or sweep.SweepReport())
    sweep.sweep(tmp_path, "origin/dev", "true", timeout=30)
    assert seen == [{"cwd": tmp_path.resolve(), "kind": "sweep", "on_wait": None,
                     "max_wait": 360.0, "rank": None, "inside": True}]


def test_self_check_runs_its_battery_inside_one_slot(battery_spy, monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(judge, "_self_check",
                        lambda *a, **k: seen.append(dict(battery_spy[-1])) or judge.Report())
    judge.self_check(tmp_path, "true", {"experiments": []}, timeout=30)
    assert seen == [{"cwd": tmp_path, "kind": "self-check", "on_wait": None,
                     "max_wait": 360.0, "rank": None, "inside": True}]


def test_the_judge_battery_runs_inside_one_slot_with_its_wait_hook(battery_spy, monkeypatch,
                                                                     tmp_path):
    seen = []
    hook = object()
    monkeypatch.setitem(judge._run_ctx, "on_slot_wait", hook)
    monkeypatch.setattr(judge, "_run_experiments",
                        lambda *a, **k: seen.append(dict(battery_spy[-1])) or judge.Report())
    judge.run_experiments(tmp_path, "true", {"experiments": []}, timeout=30)
    assert seen == [{"cwd": tmp_path, "kind": "judge", "on_wait": hook,
                     "max_wait": 360.0, "rank": None, "inside": True}]


@pytest.fixture
def no_slot(monkeypatch):
    @contextlib.contextmanager
    def never(*_a, **_kw):
        raise suite_guard.SlotTimeout("no suite slot came free in 9s (all 1 busy: CMX-X)")
        yield
    monkeypatch.setattr(suite_guard, "battery", never)


def test_a_sweep_that_never_gets_a_slot_is_cannot_verify(no_slot, monkeypatch, tmp_path):
    monkeypatch.setattr(sweep, "_sweep", lambda *a, **k: pytest.fail("ran without a slot"))
    rep = sweep.sweep(tmp_path, "origin/dev", "true", timeout=30)
    assert rep.cannot_verify == ("the sweep could not be run: no suite slot came free in 9s "
                                 "(all 1 busy: CMX-X)")


def test_a_self_check_that_never_gets_a_slot_is_cannot_verify(no_slot, monkeypatch, tmp_path):
    monkeypatch.setattr(judge, "_self_check", lambda *a, **k: pytest.fail("ran without a slot"))
    rep = judge.self_check(tmp_path, "true", {"experiments": []}, timeout=30)
    assert rep.cannot_verify == ("the self-check could not be run: no suite slot came free "
                                 "in 9s (all 1 busy: CMX-X)")


def test_a_judge_battery_that_never_gets_a_slot_is_cannot_verify(no_slot, monkeypatch,
                                                                   tmp_path):
    monkeypatch.setattr(judge, "_run_experiments",
                        lambda *a, **k: pytest.fail("ran without a slot"))
    rep = judge.run_experiments(tmp_path, "true", {"experiments": []}, timeout=30)
    assert rep.cannot_verify == ("the battery could not be run: no suite slot came free in "
                                 "9s (all 1 busy: CMX-X)")


# --- task-finished: ONE slot across the self-check, the sweep and the base-move re-run -----

@pytest.fixture
def tf_run(one_slot, monkeypatch, tmp_path):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    wt = tmp_path / "CMX-9"
    wt.mkdir()
    wf = tmp_path / "WORKFLOW.md"
    wf.write_text("---\nproject_key: TEST\njudge:\n  test_cmd: \"true\"\n  suite_timeout_seconds: 45\n---\nbody\n")
    with dispatcher._db() as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, window_name, "
            "worktree_path, branch_name, started_at, attempt, task_number) "
            "VALUES ('t9', ?, 'x', 'running', 'cmx-9', ?, 'b', '2026-11-17T00:00:00+00:00', "
            "1, 9)", (str(wf), str(wt)))
        conn.commit()
    for name, value in (("check_base_sync", {"ok": True, "since": "abc"}),
                        ("check_changelog_fragments", {"ok": True}),
                        ("check_hand_in_size", {"ok": True}),
                        ("check_review_section", {"ok": True}),
                        ("hand_in_tier", "core"),
                        ("request_task_finished", {"ok": True, "task_id": "t9"})):
        monkeypatch.setattr(dispatcher, name, lambda *a, _v=value, **k: _v)
    return wt


def test_task_finished_holds_one_slot_across_every_check(tf_run, monkeypatch, tmp_path):
    from chela import main
    seen = []

    def inside(step, value):
        def f(*_a, **_k):
            seen.append((step, suite_guard._held.depth, suite_guard.holders(),
                         [r["kind"] for r in _records(tmp_path)]))
            return value
        return f
    monkeypatch.setattr(dispatcher, "verify_self_check",
                        inside("self_check", {"ok": True, "blocking": 0, "cannot_verify": "",
                                              "outcomes": []}))
    monkeypatch.setattr(dispatcher, "verify_sweep",
                        inside("sweep", {"ok": True, "survivors": [], "mutations": 0,
                                         "killed": 0, "cannot_verify": "", "recovered": ""}))
    monkeypatch.setattr(dispatcher, "reconcile_base_move", inside("base_move", {"ok": True}))
    with patch.object(sys, "argv", ["chela", "task-finished", "t9",
                                     "--self-check-experiments", str(tmp_path / "e.json")]):
        main.main()
    want = (1, ["CMX-9"], ["task-finished"])
    assert seen == [("self_check", *want), ("sweep", *want), ("base_move", *want)]
    assert suite_guard._held.depth == 0 and not _records(tmp_path)


def test_task_finished_that_never_gets_a_slot_refuses(tf_run, monkeypatch, capsys):
    from chela import main
    monkeypatch.setattr(dispatcher, "verify_sweep", lambda *a: pytest.fail("ran without a slot"))
    monkeypatch.setattr(dispatcher, "hand_in_battery",
                        lambda task_id: (_ for _ in ()).throw(
                            suite_guard.SlotTimeout("no suite slot came free in 9s")))
    with patch.object(sys, "argv", ["chela", "task-finished", "t9", "--no-new-guards"]):
        with pytest.raises(SystemExit) as e:
            main.main()
    assert e.value.code == 1
    assert ("task-finished: 🧠 the hand-in checks could not start: no suite slot came free "
            "in 9s") in capsys.readouterr().out


def test_hand_in_battery_reads_the_runs_worktree_and_suite_timeout(tf_run, monkeypatch):
    got = []
    monkeypatch.setattr(suite_guard, "battery",
                        lambda cwd, kind, max_wait=None, rank=None:
                        got.append((cwd, kind, max_wait, rank)))
    dispatcher.hand_in_battery("t9")
    # 12 x the workflow's 45s; no priority on the row ranks as an unknown hand-in (5)
    assert got == [(str(tf_run), "task-finished", 540.0, 5)]


def test_hand_in_battery_without_a_run_or_workflow_falls_back(tf_run, monkeypatch, tmp_path):
    got = []
    monkeypatch.setattr(suite_guard, "battery",
                        lambda cwd, kind, max_wait=None, rank=None:
                        got.append((cwd, kind, max_wait)))
    monkeypatch.chdir(tmp_path)
    dispatcher.hand_in_battery("nope")
    with dispatcher._db() as conn:
        conn.execute("UPDATE runs SET workflow_path=? WHERE task_id='t9'",
                     (str(tmp_path / "missing.md"),))
        conn.commit()
    dispatcher.hand_in_battery("t9")
    default = 12 * 900.0
    assert got == [(str(tmp_path), "task-finished", default),
                   (str(tf_run), "task-finished", default)]


@pytest.mark.parametrize("env,stored,want", [
    (None, None, 360.0), ("7", 9, 7.0), (None, 9, 9.0), ("0", None, 360.0),
    ("-3", None, 360.0), ("soon", 9, 9.0)])
def test_battery_max_wait_env_then_config_then_twelve_timeouts(one_slot, monkeypatch, env,
                                                               stored, want):
    from chela import userconfig
    if env is not None:
        monkeypatch.setenv("CHELA_SUITE_SLOT_MAX_WAIT", env)
    if stored is not None:
        userconfig.set_("suite_slot_max_wait", stored)
    assert suite_guard.battery_max_wait(30) == want


def test_a_hand_in_with_an_unreadable_workflow_says_so(tf_run, monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(suite_guard, "battery",
                        lambda cwd, kind, max_wait=None, rank=None: max_wait)
    with dispatcher._db() as conn:
        conn.execute("UPDATE runs SET workflow_path=? WHERE task_id='t9'",
                     (str(tmp_path / "missing.md"),))
        conn.commit()
    assert dispatcher.hand_in_battery("t9") == 12 * 900.0
    assert "could not be read" in caplog.text and "waiting at most 12" in caplog.text
