"""CMX-120 — the "self-check running past the ceiling" notice fires ONCE, not every tick.

10-13 ~07:20Z: after CMX-110 the orchestrator got "⏳ … judge self-check running for 1h00m
(ceiling 1h00m) — not stuck, still working" at 1h00m, again at 1h00m, then at 1h01m — once
per daemon tick. The latch was keyed on the self-check's float start (``btime +
starttime/HZ``, re-read every tick), and that float is not stable (WSL2 re-derives ``btime``
from a resynced clock), so every tick looked like a NEW self-check. Locked in here:

  * one self-check (one process) past the ceiling over N ticks is exactly ONE notice, even
    when its measured start jitters between ticks;
  * still running an hour later it may say so once more — never per tick;
  * a NEW self-check process (a re-run) is news again;
  * the latch clears when the self-check ends, and when the run leaves ``running``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from chela import inbox


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CHELA_INBOX_FILE", str(tmp_path / "inbox.json"))
    monkeypatch.setenv("CHELA_EVENTS_FILE", str(tmp_path / "events.jsonl"))
    monkeypatch.setenv("CHELA_STUCK_AGENT_ALERT_S", "1200")
    monkeypatch.setenv("CHELA_SELFCHECK_ALERT_S", "3600")


START = 1_000_000.0
CEILING = 3600


def _run(status="running"):
    return {"task_id": "T9", "title": "fix the parser", "status": status,
            "branch_name": "cmx-9", "pr_url": "https://github.com/o/r/pull/9",
            "pr_state": "open"}


def _probe(pid=4242, since=START, what="judge self-check"):
    sc = {"what": what, "since": since, "pid": pid}
    return {"T9": {"activity": START, "markers": {}, "selfcheck": sc}}


def _tick(store, now, probe, status="running"):
    return [e["kind"] for e in inbox.stuck_events([_run(status)], store, probe, now)]


def test_one_self_check_past_the_ceiling_over_many_ticks_is_one_notice():
    """🔴 GUARD: the 10-13 flood — 30 ticks a minute apart past the ceiling, the measured
    start jittering by a fraction of a second each tick (same pid), is exactly ONE notice."""
    store, fired = inbox._empty(), []
    for i in range(30):
        jitter = (-1) ** i * 0.37 * (i % 3)
        fired += _tick(store, START + CEILING + 60 * i, _probe(since=START + jitter))
    assert fired == ["run_selfcheck_long"]


def test_without_a_pid_a_jittering_start_is_still_one_self_check():
    """🔴 GUARD: a probe that could not name the pid falls back to the start, with slack —
    a reading a second off is the same self-check, not a new one each tick."""
    store, fired = inbox._empty(), []
    for i in range(10):
        fired += _tick(store, START + CEILING + 60 * i,
                       _probe(pid=None, since=START + (i % 2) * 1.5))
    assert fired == ["run_selfcheck_long"]


def test_still_running_an_hour_past_the_ceiling_says_so_at_most_once_more():
    """🔴 GUARD: the coarse re-notify — +1h past the ceiling is ONE more notice, and the
    ticks around it stay silent."""
    store, fired = inbox._empty(), []
    for minute in range(0, 125):
        fired += _tick(store, START + CEILING + 60 * minute, _probe())
    # ceiling (minute 0), +1h (minute 60), +2h (minute 120) — never per tick.
    assert fired == ["run_selfcheck_long"] * 3


def test_a_new_self_check_process_is_news_again():
    """🔴 GUARD: a re-run is a different process — past the ceiling it gets its own notice."""
    store = inbox._empty()
    assert _tick(store, START + CEILING, _probe(pid=1)) == ["run_selfcheck_long"]
    assert _tick(store, START + CEILING + 60, _probe(pid=1)) == []
    rerun = START + 600
    assert _tick(store, rerun + CEILING, _probe(pid=2, since=rerun)) == ["run_selfcheck_long"]
    assert _tick(store, rerun + CEILING + 60, _probe(pid=2, since=rerun)) == []


def test_the_latch_clears_when_the_self_check_ends():
    """🔴 GUARD: a tick with no self-check drops the latch from the store."""
    store = inbox._empty()
    assert _tick(store, START + CEILING, _probe()) == ["run_selfcheck_long"]
    assert store["stuck"]["T9"].get("selfcheck")
    idle = {"T9": {"activity": START + CEILING, "markers": {}, "selfcheck": None}}
    _tick(store, START + CEILING + 60, idle)
    assert "selfcheck" not in store["stuck"]["T9"]


def test_the_latch_resets_when_the_run_leaves_running():
    """🔴 GUARD: a run that leaves ``running`` loses its latch; back in ``running`` with the
    same self-check still past the ceiling, it is announced again."""
    store = inbox._empty()
    assert _tick(store, START + CEILING, _probe()) == ["run_selfcheck_long"]
    assert _tick(store, START + CEILING + 60, _probe(), status="awaiting_review") == []
    assert "T9" not in store["stuck"]
    assert _tick(store, START + CEILING + 120, _probe()) == ["run_selfcheck_long"]


def test_the_probe_names_the_earliest_instance_pid():
    """🔴 GUARD: the self-check's identity is the pid of its earliest instance — the one the
    start is read from — so the latch keys on a stable process, not a float."""
    root = Path("/tmp").resolve()
    procs = [("judge self-check", root, 200.0, 77), ("judge self-check", root, 100.0, 55),
             ("pytest", root, 50.0, 11)]
    assert inbox._selfcheck_for(str(root), procs) == {
        "what": "judge self-check", "since": 100.0, "pid": 55}


def test_a_quick_re_run_is_a_new_process_even_with_a_start_inside_the_slack():
    """🔴 GUARD: with both pids known the pid decides — a re-run that started seconds after
    the first (inside the pid-less slack) is still a new self-check and is news."""
    store = inbox._empty()
    assert _tick(store, START + CEILING, _probe(pid=1)) == ["run_selfcheck_long"]
    assert _tick(store, START + CEILING + 60, _probe(pid=2, since=START + 30)) == [
        "run_selfcheck_long"]


def test_a_probe_with_no_readable_start_names_the_command_without_a_pid():
    """🔴 GUARD: a process whose start could not be read still names the self-check (it is
    activity), with no start and no pid to latch on — never None, never a crash."""
    root = Path("/tmp").resolve()
    procs = [("task-finished self-check", root, None, 9)]
    assert inbox._selfcheck_for(str(root), procs) == {
        "what": "task-finished self-check", "since": None, "pid": None}


# --- the PRODUCER of the identity: ``_selfcheck_scan`` reads the pid off ``/proc`` ---------
# Every test above hands ``stuck_events`` / ``_selfcheck_for`` a probe that already carries a
# pid. The pid has to come from somewhere real — the ``/proc/<pid>`` directory name — and a
# scan that stopped reporting it would fall back to the start-with-slack match and look fine
# on every jittering-start fixture. These run the real scan over a fake ``/proc``.

_HZ = 100


def _fake_proc(root: Path, btime: float, procs: list[tuple[int, list[str], Path, int]]):
    """A ``/proc`` with ``stat``'s ``btime`` and, per ``(pid, argv, cwd, start_ticks)``, the
    three files the scan reads: ``cmdline``, a ``cwd`` link and ``stat`` (field 22)."""
    if root.exists():
        for child in sorted(root.rglob("*"), reverse=True):
            child.unlink() if child.is_file() or child.is_symlink() else child.rmdir()
    root.mkdir(exist_ok=True)
    (root / "stat").write_text(f"cpu  1 2 3\nbtime {int(btime)}\nprocesses 9\n")
    for pid, argv, cwd, ticks in procs:
        d = root / str(pid)
        d.mkdir()
        (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
        (d / "cwd").symlink_to(cwd)
        rest = ["S"] + ["0"] * 18 + [str(ticks)] + ["0"] * 5   # index 19 = starttime
        (d / "stat").write_text(f"{pid} (python) " + " ".join(rest) + "\n")


_JUDGE = ["python", "-m", "chela.main", "judge", "self-check"]


@pytest.fixture
def fake_proc(tmp_path, monkeypatch):
    monkeypatch.setattr(inbox, "_PROC", tmp_path / "proc")
    monkeypatch.setattr(inbox.os, "sysconf", lambda name: _HZ)
    monkeypatch.setattr(inbox.transcripts, "transcript_for_cwd", lambda cwd: None)
    monkeypatch.setattr(inbox, "_git_last_commit", lambda w: None)
    wt = (tmp_path / "wt")
    wt.mkdir()
    return tmp_path / "proc", wt.resolve()


def test_the_scan_reports_each_process_by_its_proc_pid(fake_proc):
    """🔴 GUARD: the pid the latch keys on is the ``/proc/<pid>`` the process was read from —
    distinct per process, never None for a readable one."""
    proc, wt = fake_proc
    _fake_proc(proc, 1000, [(4242, _JUDGE, wt, 500), (17, ["pytest", "-q"], wt, 900),
                            (5, ["bash"], wt, 100)])
    assert sorted(inbox._selfcheck_scan()) == sorted([
        ("judge self-check", wt, 1005.0, 4242), ("pytest", wt, 1009.0, 17)])


def test_a_quick_re_run_through_the_real_scan_is_news_again(fake_proc):
    """🔴 GUARD (end-to-end): scan → probe → latch. A self-check re-run 30s after the first
    one (inside the pid-less slack) is a NEW process — read off ``/proc`` — so it is news;
    the same process on the next tick, its start jittering with ``btime``, is not."""
    proc, wt = fake_proc
    run = dict(_run(), worktree_path=str(wt))
    store, fired = inbox._empty(), []

    def tick(now, btime, pid, ticks):
        _fake_proc(proc, btime, [(pid, _JUDGE, wt, ticks)])
        probes = inbox.stuck_probes([run])
        fired.extend(e["kind"] for e in inbox.stuck_events([run], store, probes, now))
        return probes["T9"]["selfcheck"]

    sc = tick(START + CEILING, START, 1001, 0)
    assert sc == {"what": "judge self-check", "since": START, "pid": 1001}
    tick(START + CEILING + 60, START + 1, 1001, 0)           # btime resynced: same process
    assert fired == ["run_selfcheck_long"]
    tick(START + CEILING + 120, START, 1002, 30 * _HZ)       # the re-run (+30s): new pid
    assert fired == ["run_selfcheck_long"] * 2
