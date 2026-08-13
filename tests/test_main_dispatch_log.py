"""CMX-265 round 9 finding 2: operator visibility for a closed-only reconcile tick.

Both daemon loops that call `dispatcher.tick()` — `chela run` (`cmd_run`) and
`chela dispatch` (`cmd_dispatch`) — only log a tick's summary when something
happened: dispatched, reconciled_done, reconciled_closed, or reconciled_failed.
A tick whose ONLY work was reconciling a closed-not-merged PR is still real
state change; drop it from that condition and the tick becomes completely
silent in the daemon log, which is exactly the failure mode CMX-265 exists to
end — a ghost row nobody could see move. Nothing else in tests/ reaches either
call site: the only `reconciled_closed` assertions elsewhere are on `tick()`'s
own summary dict, never on whether the daemon actually logged it.

Negative control: drop `or summary["reconciled_closed"]` from either logging
condition in chela/main.py and the matching test below goes RED.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from chela import main

CLOSED_ONLY_SUMMARY = {
    "dispatched": 0,
    "reconciled_done": 0,
    "reconciled_closed": 1,
    "reconciled_failed": 0,
    "blocked": False,
    "held": False,
}


def _stub_daemon_loop_inert(monkeypatch) -> None:
    """One real pass of `cmd_run`'s `while not stop.stopping` loop, everything besides
    the dispatch tick neutered — same one-tick harness shape as
    `test_orchestrator_autolaunch.test_the_daemon_loop_calls_maybe_wake_on_the_inbox_tick`."""
    monkeypatch.setattr(main.GracefulShutdown, "install", lambda self: self)

    def stop_after_one(self, _seconds):
        self._event.set()
        return True

    monkeypatch.setattr(main.GracefulShutdown, "wait", stop_after_one)

    monkeypatch.setattr(main.scheduler, "init", lambda: None)
    monkeypatch.setattr(main.scheduler, "tick", lambda: 0)
    monkeypatch.setattr(main.agent_manager, "reconcile_window_names", lambda: [])
    monkeypatch.setattr(main.agent_manager, "start_background_refresh", lambda *a, **kw: None)
    monkeypatch.setattr(main.capabilities, "effective", lambda: [])
    monkeypatch.setattr(main.capabilities, "announce", lambda caps, log: None)
    monkeypatch.setattr(main.capabilities, "publish", lambda caps, boot_id: None)
    monkeypatch.setattr(main.capabilities, "clear", lambda: None)
    monkeypatch.setattr(main, "maintenance_tick", lambda last_capture, now: last_capture)
    monkeypatch.setattr(main.context, "prune_snapshots", lambda retention_days: 0)
    monkeypatch.setattr(main.automerge, "enabled", lambda: False)
    monkeypatch.setattr(main.notify, "enabled", lambda: False)
    monkeypatch.setattr(main.doctor, "check_and_notify", lambda seen: seen)
    monkeypatch.setattr(main.update, "auto_apply_enabled", lambda: False)
    monkeypatch.setattr(main.update, "check_and_notify", lambda seen: seen)
    monkeypatch.setattr(main.inbox, "enabled", lambda: False)
    monkeypatch.setattr(main.rooms, "has_pending", lambda: False)


def _capture_log_info(monkeypatch) -> list[tuple]:
    calls: list[tuple] = []
    monkeypatch.setattr(main.log, "info", lambda msg, *args: calls.append((msg, args)))
    return calls


def test_cmd_run_logs_a_tick_whose_only_work_was_reconciling_a_closed_PR(monkeypatch):
    """🔴 WIRING (production call-site, `chela run`) — a closed-not-merged reconcile is
    real state change; the daemon log must show the tick that did it."""
    _stub_daemon_loop_inert(monkeypatch)
    wf_path = Path("wf.md")
    monkeypatch.setattr(main, "DISPATCH_WORKFLOWS", [wf_path])
    monkeypatch.setattr(main.dispatcher, "poll_interval", lambda *a, **k: 30.0)
    monkeypatch.setattr(main.dispatcher, "tick", lambda *a, **k: dict(CLOSED_ONLY_SUMMARY))
    calls = _capture_log_info(monkeypatch)

    main.cmd_run(SimpleNamespace())

    assert ("Dispatch %s: %s", (wf_path.name, CLOSED_ONLY_SUMMARY)) in calls, (
        "cmd_run stayed silent on a tick whose only work was reconciling a closed PR — "
        "that reconcile is unobservable in the daemon log"
    )


def test_cmd_dispatch_logs_a_tick_whose_only_work_was_reconciling_a_closed_PR(monkeypatch):
    """🔴 WIRING (production call-site, `chela dispatch`) — same guard, the standalone
    `chela dispatch --once`-less foreground loop."""
    monkeypatch.setattr(main.GracefulShutdown, "install", lambda self: self)

    def stop_after_one(self, _seconds):
        self._event.set()
        return True

    monkeypatch.setattr(main.GracefulShutdown, "wait", stop_after_one)

    wf_path = Path("wf.md")
    monkeypatch.setattr(main.dispatcher, "tick", lambda *a, **k: dict(CLOSED_ONLY_SUMMARY))
    monkeypatch.setattr(main.dispatcher, "poll_interval", lambda *a, **k: 30.0)
    calls = _capture_log_info(monkeypatch)

    args = SimpleNamespace(
        resume=False, pause=False, hold_status=False,
        workflow=wf_path, dry_run=False, once=False, interval=5,
    )
    main.cmd_dispatch(args)

    assert ("Dispatch tick: %s", (CLOSED_ONLY_SUMMARY,)) in calls, (
        "cmd_dispatch stayed silent on a tick whose only work was reconciling a closed PR — "
        "that reconcile is unobservable in the daemon log"
    )
