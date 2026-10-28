"""CMX-348: keeper-only workflows (`dispatch: false`), the daemon's keeper schedule, and
`chela keeper stats` — the trial's kill metric."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from chela import (automerge, dispatcher, hold, keeper, keeper_run, keeper_schedule, main,
                   mergegate, sweep_nightly)
from chela.workflow import (dispatch_enabled, keeper_every_seconds, load_workflow,
                            parse_workflow, reset_cache)
from tests.test_merge_outside_gate import ticking  # noqa: F401 — the dispatcher fixture

HEAD = "---\nproject_key: CMX\ntracker:\n  kind: markdown\n  path: TODO.md\n"


def wf_of(tmp_path: Path, extra: str = ""):
    return parse_workflow(tmp_path / "WORKFLOW.md", f"{HEAD}{extra}---\nbody\n")


# --- the two WORKFLOW.md keys ----------------------------------------------------------

@pytest.mark.parametrize("extra, want", [
    ("", True),
    ("dispatch: false\n", False),
    ("dispatch: true\n", True),
    ("dispatch:\n  enabled: false\n", False),
    ("dispatch:\n  enabled: true\n", True),
    ("dispatch: {}\n", True),
])
def test_dispatch_enabled(tmp_path, extra, want):
    assert dispatch_enabled(wf_of(tmp_path, extra)) is want


@pytest.mark.parametrize("extra, want", [
    ("", None),
    ("keeper:\n  enabled: false\n  every: 1h\n", None),
    ("keeper:\n  enabled: true\n", 2700),            # the documented default, 45m
    ("keeper:\n  enabled: true\n  every: 2h\n", 7200),
    ("keeper:\n  enabled: true\n  every: 600\n", 600),
    ("keeper: {}\n", None),
])
def test_keeper_every(tmp_path, extra, want):
    assert keeper_every_seconds(wf_of(tmp_path, extra)) == want


@pytest.mark.parametrize("extra", [
    "dispatch: 'no'\n",
    "dispatch: [false]\n",
    "dispatch:\n  enabled: 'false'\n",
    "keeper: true\n",
    "keeper:\n  enabled: 'yes'\n",
    "keeper:\n  enabled: true\n  every: soon\n",
    "keeper:\n  enabled: true\n  every: 0\n",
])
def test_a_bad_mode_value_fails_the_parse(tmp_path, extra):
    """A typo must never silently turn dispatch on or off: the parse fails, so the last
    good config stays in force."""
    with pytest.raises(ValueError):
        wf_of(tmp_path, extra)


# --- a keeper-only workflow: no claim, no judge, no merge-gate entry -----------------------

def _keeper_only(work: Path) -> Path:
    path = work / "WORKFLOW.md"
    path.write_text(path.read_text().replace("project_key: CMX\n",
                                             "project_key: CMX\ndispatch:\n  enabled: false\n"))
    reset_cache()
    return path


def _registry() -> list[dict]:
    return mergegate.load_registry({"CHELA_DIR": str(dispatcher.CHELA_DIR)}) or []


def test_a_keeper_only_tick_claims_judges_and_registers_nothing(ticking, monkeypatch):  # noqa: F811
    """🔴 GUARD: the tick never opens the runs DB (no claim, no reconcile, no judge spawn),
    and the workflow's existing merge-gate entry is REMOVED."""
    path = ticking / "WORKFLOW.md"
    dispatcher.tick(path)                                   # registered while dispatching
    assert any(e["workflow"] == str(path) for e in _registry())
    _keeper_only(ticking)
    spawned, judged = [], []
    monkeypatch.setattr(dispatcher, "_spawn", lambda *a, **kw: spawned.append(a) or False)
    monkeypatch.setattr(dispatcher, "_spawn_judge", lambda *a, **kw: judged.append(a) or False)

    def no_db():
        raise AssertionError("a keeper-only tick opened the runs DB")
    monkeypatch.setattr(dispatcher, "_db", no_db)
    summary = dispatcher.tick(path)
    assert summary["dispatch_off"] is True and summary["blocked"] is False
    assert summary["dispatched"] == 0 and spawned == [] and judged == []
    assert [e for e in _registry() if e["workflow"] == str(path)] == []
    assert dispatcher.dry_run(path) == []


def test_negative_control_a_dispatching_tick_claims_and_registers(ticking, monkeypatch):  # noqa: F811
    spawned = []
    monkeypatch.setattr(dispatcher, "_spawn", lambda *a, **kw: spawned.append(a) or False)
    path = ticking / "WORKFLOW.md"
    summary = dispatcher.tick(path)
    assert "dispatch_off" not in summary and len(spawned) == 1
    assert [e["workflow"] for e in _registry() if e["workflow"] == str(path)] == [str(path)]
    assert len(dispatcher.dry_run(path)) == 1


def test_unregister_keeps_the_other_workflows(tmp_path):
    env = {"CHELA_DIR": str(tmp_path / "c")}
    a, b = tmp_path / "a" / "WORKFLOW.md", tmp_path / "b" / "WORKFLOW.md"
    assert mergegate.register(a, ["dev"], env) and mergegate.register(b, ["main"], env)
    assert dispatcher.unregister_merge_gate(a, env) is True
    assert [e["workflow"] for e in mergegate.load_registry(env)] == [str(b)]
    assert dispatcher.unregister_merge_gate(a, env) is False    # nothing left to drop
    assert dispatcher.unregister_merge_gate(a, {"CHELA_DIR": str(tmp_path / "none")}) is False


def test_an_unwritable_registry_is_left_in_force(tmp_path, monkeypatch):
    env = {"CHELA_DIR": str(tmp_path / "c")}
    a = tmp_path / "a" / "WORKFLOW.md"
    mergegate.register(a, ["dev"], env)

    def deny(*args, **kw):
        raise PermissionError("read-only")
    monkeypatch.setattr(dispatcher.os, "replace", deny)
    assert dispatcher.unregister_merge_gate(a, env) is False
    assert [e["workflow"] for e in mergegate.load_registry(env)] == [str(a)]


def _seed_clean_run(task_id: str, workflow_path: Path) -> None:
    with dispatcher._db() as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, window_name, started_at, "
            "attempt, pr_url, pr_state, judge_state, branch_name) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (task_id, str(workflow_path), "t", "awaiting_review", "@9", dispatcher._now(), 1,
             "https://github.com/o/r/pull/1", "open", "clean", "cmx-1"))
        conn.commit()


def test_auto_merge_skips_a_keeper_only_workflows_run(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    on, off, never = tmp_path / "on", tmp_path / "off", tmp_path / "never"
    for d, extra in ((on, ""), (off, "dispatch: false\n")):
        d.mkdir()
        (d / "WORKFLOW.md").write_text(f"{HEAD}{extra}---\nbody\n")
    never.mkdir()
    (never / "WORKFLOW.md").write_text("# never parsed\n")
    _seed_clean_run("t-on", on / "WORKFLOW.md")
    _seed_clean_run("t-off", off / "WORKFLOW.md")
    _seed_clean_run("t-never", never / "WORKFLOW.md")
    got = sorted(r["task_id"] for r in automerge.candidates())
    assert got == ["t-never", "t-on"]      # never-parsed: contract.merge's own gate decides


def test_the_nightly_sweep_is_never_due_for_a_keeper_only_workflow(tmp_path):
    at, chela = datetime(2026, 10, 21, 3, 0), tmp_path / "chela"
    sweep = "sweep:\n  nightly_at: '02:30'\n"
    assert sweep_nightly.due(wf_of(tmp_path, sweep), at, chela)
    assert not sweep_nightly.due(wf_of(tmp_path, sweep + "dispatch: false\n"), at, chela)


# --- the schedule ------------------------------------------------------------------------

class Child:
    def __init__(self, done=False):
        self.done = done

    def poll(self):
        return 0 if self.done else None


@pytest.fixture
def sched(tmp_path):
    path = tmp_path / "WORKFLOW.md"
    path.write_text(f"{HEAD}keeper:\n  enabled: true\n  every: 45m\n---\nbody\n")
    reset_cache()
    calls = {"spawned": [], "asked": []}

    def spawner(p):
        calls["spawned"].append(p)
        return Child()

    def active(wf, since):
        calls["asked"].append(since)
        return calls.get("active", True)
    return keeper_schedule.Scheduler(spawner=spawner, active=active), path, calls


def test_a_due_workflow_runs_once_and_waits_for_every(sched):
    s, path, calls = sched
    assert s.tick([path], now=10_000.0) == [str(path)]
    assert calls["asked"] == [10_000.0 - 2700]              # first run: one `every` back
    s.children[str(path)].done = True
    assert s.tick([path], now=10_000.0 + 2699) == []        # not yet due
    assert s.tick([path], now=10_000.0 + 2700) == [str(path)]
    assert calls["asked"][-1] == 10_000.0                   # since the last run
    assert json.loads(keeper_schedule.state_path().read_text()) == {str(path): 12_700.0}


def test_at_most_one_run_in_flight(sched):
    s, path, calls = sched
    s.tick([path], now=10_000.0)
    assert s.tick([path], now=20_000.0) == []               # the first is still going
    s.children[str(path)].done = True
    assert s.tick([path], now=20_000.0) == [str(path)]


def test_a_held_queue_runs_nothing(sched):
    s, path, calls = sched
    hold.take("rewriting the queue", ttl_seconds=3600)
    assert s.tick([path]) == [] and calls["spawned"] == []
    hold.release()
    assert s.tick([path]) == [str(path)]


def test_no_activity_since_the_last_run_skips_and_rechecks_later(sched):
    s, path, calls = sched
    calls["active"] = False
    assert s.tick([path], now=10_000.0) == []
    assert not keeper_schedule.state_path().exists()        # the run stays owed
    calls["active"] = True
    assert s.tick([path], now=10_000.0 + 299) == []         # not re-asked yet
    assert len(calls["asked"]) == 1
    assert s.tick([path], now=10_000.0 + 300) == [str(path)]
    assert calls["asked"] == [10_000.0 - 2700, 10_300.0 - 2700]   # never run: the last `every`


def test_after_a_run_activity_is_asked_from_that_run(sched):
    s, path, calls = sched
    s.tick([path], now=10_000.0)
    s.children[str(path)].done = True
    calls["active"] = False
    assert s.tick([path], now=13_000.0) == []
    calls["active"] = True
    assert s.tick([path], now=13_300.0) == [str(path)]
    assert calls["asked"][1:] == [10_000.0, 10_000.0]       # the window does not slide


def test_a_workflow_without_keeper_is_never_run(sched, tmp_path):
    s, _, calls = sched
    (tmp_path / "plain").mkdir()
    plain = tmp_path / "plain" / "WORKFLOW.md"
    plain.write_text(f"{HEAD}---\nbody\n")
    assert s.tick([plain]) == [] and calls["asked"] == []


def test_a_workflow_without_keeper_is_skipped_quietly(sched, tmp_path, caplog):
    """Not an error path: nothing is logged for a workflow that never opted in."""
    s, _, calls = sched
    (tmp_path / "plain").mkdir()
    plain = tmp_path / "plain" / "WORKFLOW.md"
    plain.write_text(f"{HEAD}---\nbody\n")
    with caplog.at_level("INFO", logger="chela.keeper_schedule"):
        s.tick([plain], now=10_000.0)
    assert caplog.records == []


def test_one_workflow_raising_does_not_stop_the_next(sched, tmp_path):
    s, path, calls = sched
    (tmp_path / "b").mkdir()
    other = tmp_path / "b" / "WORKFLOW.md"
    other.write_text(path.read_text())

    def active(wf, since):
        if wf.path == path.resolve():
            raise RuntimeError("tmux went away")
        return True
    s._active = active
    assert s.tick([path, other], now=10_000.0) == [str(other)]


def test_a_spawn_that_fails_is_not_recorded_as_run(sched):
    s, path, calls = sched

    def boom(p):
        raise OSError("no fork")
    s._spawn = boom
    assert s.tick([path], now=10_000.0) == []
    assert not keeper_schedule.state_path().exists()      # still owed on the next pass


@pytest.mark.parametrize("state, why", [("{not json", "is unreadable"),
                                        ("[1, 2]", "is not a JSON object")])
def test_an_unreadable_schedule_runs_nothing(sched, state, why, caplog):
    s, path, calls = sched
    keeper_schedule.state_path().parent.mkdir(parents=True, exist_ok=True)
    keeper_schedule.state_path().write_text(state)
    with caplog.at_level("WARNING", logger="chela.keeper_schedule"):
        assert s.tick([path]) == [] and calls["spawned"] == []
    assert [r.levelname for r in caplog.records] == ["WARNING"]
    assert why in caplog.records[0].getMessage()


def test_a_garbled_last_run_runs_nothing(sched, caplog):
    s, path, calls = sched
    keeper_schedule.state_path().parent.mkdir(parents=True, exist_ok=True)
    keeper_schedule.state_path().write_text(json.dumps({str(path): "yesterday"}))
    with caplog.at_level("WARNING", logger="chela.keeper_schedule"):
        assert s.tick([path]) == [] and calls["spawned"] == []
    assert [r.levelname for r in caplog.records] == ["WARNING"]
    assert "holds 'yesterday'" in caplog.records[0].getMessage()


def test_spawn_runs_keeper_run_detached(tmp_path, monkeypatch):
    seen = {}

    def popen(argv, **kw):
        seen.update(argv=argv, **kw)
        return Child()
    monkeypatch.setattr(keeper_schedule.subprocess, "Popen", popen)
    keeper_schedule.spawn(tmp_path / "WORKFLOW.md")
    assert seen["argv"] == [sys.executable, "-m", "chela.main", "keeper", "run",
                            str(tmp_path / "WORKFLOW.md")]
    assert seen["stdin"] is subprocess.DEVNULL and seen["stderr"] is subprocess.STDOUT
    assert seen["start_new_session"] is True and seen["close_fds"] is True
    assert Path(seen["stdout"].name) == keeper_run.keeper_dir() / "run.log"


def test_activity_is_a_prompt_or_an_edit_from_a_live_window_in_the_repo(tmp_path, monkeypatch):
    wf = wf_of(tmp_path)
    window = {"wid": "@4", "session_id": "s", "started": None}
    monkeypatch.setattr(keeper, "repo_roots", lambda w: (str(tmp_path), [str(tmp_path)]))
    monkeypatch.setattr(keeper, "live_windows", lambda roots: [window])
    asked = []

    def events(cursor, since):
        asked.append((cursor, since))
        return {"events": [{"type": keeper.PROMPT_EVENT, "wid": "@4", "ts": 50,
                            "payload": {"prompt": "go"}}]}
    monkeypatch.setattr(keeper, "read_events", events)
    assert keeper_schedule.active_since(wf, 40.0) is True
    assert asked == [(None, 40.0)]
    window["wid"] = "@5"
    window["session_id"] = None
    assert keeper_schedule.active_since(wf, 40.0) is False   # someone else's window


# --- stats --------------------------------------------------------------------------------

def _log(rows):
    keeper_run.log_path().parent.mkdir(parents=True, exist_ok=True)
    keeper_run.log_path().write_text("".join(
        r if isinstance(r, str) else json.dumps(r) + "\n" for r in rows))


def row(outcome, action, ts=1000.0):
    return {"ts": ts, "batch": "b", "outcome": outcome, "proposal": {"action": action}}


def test_stats_math():
    _log([row("proposed", "done"), row("proposed", "done"), row("proposed", "comment"),
          row("dropped", "in_progress"), row("accepted", "done"), row("failed", "done"),
          row("rejected", "comment"), row("accepted", "done", ts=10.0)])
    s = keeper_run.stats(since=500, now=1200.0)
    assert (s["proposed"], s["dropped"], s["accepted"], s["rejected"], s["failed"]) == \
        (3, 1, 1, 1, 1)
    assert s["made"] == 4 and s["approved"] == 2 and s["decided"] == 3
    assert s["rate"] == pytest.approx(2 / 3)
    assert s["by_action"]["done"] == {"proposed": 2, "dropped": 0, "accepted": 1,
                                      "rejected": 0, "failed": 1}
    assert list(s["by_action"]) == ["comment", "done", "in_progress"]
    assert keeper_run.stats(now=1200.0)["accepted"] == 2       # all time keeps the old row


def test_stats_flags_a_rate_under_the_70_percent_kill_line():
    _log([row("accepted", "done")] * 7 + [row("rejected", "done")] * 3)
    assert "kill line" not in keeper_run.render_stats(keeper_run.stats())
    _log([row("accepted", "done")] * 69 + [row("rejected", "done")] * 31)
    text = keeper_run.render_stats(keeper_run.stats())
    assert "acceptance rate: 69% (69 approved of 100 decided)" in text
    assert "below the 70% kill line" in text
    assert "  by action:\n    done: accepted 69, rejected 31\n" in text


def test_stats_with_nothing_decided_has_no_rate():
    assert keeper_run.stats()["rate"] is None                # no log yet: an empty trial
    _log([row("proposed", "done")])
    s = keeper_run.stats()
    assert s["rate"] is None and s["made"] == 1
    assert "n/a (nothing decided)" in keeper_run.render_stats(s)


def test_stats_counts_unreadable_lines_instead_of_skipping_them():
    _log(["{oops\n", json.dumps({"ts": 1, "outcome": "liked"}) + "\n",
          json.dumps({"outcome": "accepted"}) + "\n", "\n", row("accepted", "done")])
    s = keeper_run.stats()
    assert s["bad"] == 3 and s["accepted"] == 1
    assert "3 log line(s) could not be read" in keeper_run.render_stats(s)


def test_an_unreadable_log_is_an_error(tmp_path):
    keeper_run.log_path().mkdir(parents=True)                # a directory: IsADirectoryError
    assert "could not be read" in keeper_run.stats()["error"]


def cli(monkeypatch, *argv):
    monkeypatch.setattr("sys.argv", ["chela", *argv])
    main.main()


def test_stats_cli(capsys, monkeypatch):
    _log([row("accepted", "done", ts=9e9), row("rejected", "done", ts=9e9)])
    cli(monkeypatch, "keeper", "stats", "--since", "7d")
    out = capsys.readouterr().out
    assert out.startswith("keeper stats — the last 7d\n") and "acceptance rate: 50%" in out
    cli(monkeypatch, "keeper", "stats", "--json")
    assert json.loads(capsys.readouterr().out)["decided"] == 2
    with pytest.raises(SystemExit) as e:
        cli(monkeypatch, "keeper", "stats", "--since", "soon")
    assert e.value.code == 2


def test_stats_cli_unreadable_log_exits_1(capsys, monkeypatch):
    keeper_run.log_path().mkdir(parents=True)
    with pytest.raises(SystemExit) as e:
        cli(monkeypatch, "keeper", "stats")
    assert e.value.code == 1 and "could not be read" in capsys.readouterr().err


def test_load_workflow_reads_both_keys(tmp_path):
    path = tmp_path / "WORKFLOW.md"
    path.write_text(f"{HEAD}dispatch:\n  enabled: false\nkeeper:\n  enabled: true\n"
                    "  every: 45m\n---\nbody\n")
    wf = load_workflow(path)
    assert dispatch_enabled(wf) is False and keeper_every_seconds(wf) == 2700


# --- the daemon wiring -----------------------------------------------------------------

def test_the_daemon_loop_ticks_the_keeper_schedule_with_the_dispatch_workflows(monkeypatch):
    """🔴 WIRING — drives ONE real pass of ``cmd_run``'s loop. The shared harness parks
    ``DISPATCH_WORKFLOWS`` at ``[]``, which is exactly what ``keeper_sched.tick([])`` passes,
    so this re-points it at a NON-empty list and checks both identity and contents
    (defeat shape 348b)."""
    from tests.test_context import _run_one_daemon_tick

    _run_one_daemon_tick(monkeypatch)
    monkeypatch.setattr(main.ops, "spawn", lambda child: child)
    monkeypatch.setattr(main.sweep_nightly, "daemon_tick", lambda workflows, prev: prev)
    monkeypatch.setattr(main.dispatcher, "poll_interval", lambda path: float("inf"))
    monkeypatch.setattr(main.worktree_gc, "spawn", lambda prev: prev)
    workflows = [Path("/nowhere/a/WORKFLOW.md"), Path("/nowhere/b/WORKFLOW.md")]
    monkeypatch.setattr(main, "DISPATCH_WORKFLOWS", workflows)
    ticks = []

    class FakeScheduler:
        def tick(self, paths):
            ticks.append(paths)

    monkeypatch.setattr(main.keeper_schedule, "Scheduler", FakeScheduler)
    main.cmd_run(argparse.Namespace())
    assert len(ticks) == 1, "the daemon loop never ticked the keeper schedule"
    assert ticks[0] is workflows and list(ticks[0]) == workflows
