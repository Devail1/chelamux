"""CMX-187: a red `chela doctor` finding used to reach only whoever ran `chela doctor` by
hand. `relay.transcripts` diagnosed a dead outbound relay perfectly and it sat unseen for
hours because nothing ever pushed it anywhere. `doctor.check_and_notify` closes that the
same way `notify.check_waiting` / `update.check_and_notify` already close it for their own
facts: edge-triggered on the transition into ERROR, logged always, pushed through notify
when configured.
"""
from __future__ import annotations

from types import SimpleNamespace

from chela import doctor, main


class _StubNotify:
    def __init__(self, enabled: bool):
        self._enabled = enabled
        self.sent: list[tuple] = []

    def enabled(self):
        return self._enabled

    def send(self, message, title=None):
        self.sent.append((message, title))
        return True


def _finding(level, title, fact="some.fact"):
    return doctor.Finding(level, title, fact=fact)


def test_notifies_once_on_the_transition_into_red(monkeypatch):
    stub = _StubNotify(enabled=True)
    monkeypatch.setattr(doctor, "notify", stub)
    monkeypatch.setattr(doctor, "check", lambda: [_finding(doctor.ERROR, "it broke")])

    red = doctor.check_and_notify(set())
    red_again = doctor.check_and_notify(red)   # a second tick, same finding still red

    assert red == {("some.fact", "it broke")}
    assert red_again == red
    assert len(stub.sent) == 1                 # not one per tick


def test_stays_quiet_when_nothing_is_red(monkeypatch):
    stub = _StubNotify(enabled=True)
    monkeypatch.setattr(doctor, "notify", stub)
    monkeypatch.setattr(doctor, "check", lambda: [_finding(doctor.OK, "all good")])

    red = doctor.check_and_notify(set())

    assert red == set()
    assert stub.sent == []


def test_ignores_warn_level_findings(monkeypatch):
    """Only ERROR is escalated — WARN findings are not the "something is broken now"
    signal doctor.ERROR is (see doctor.py's module docstring)."""
    stub = _StubNotify(enabled=True)
    monkeypatch.setattr(doctor, "notify", stub)
    monkeypatch.setattr(doctor, "check", lambda: [_finding(doctor.WARN, "heads up")])

    red = doctor.check_and_notify(set())

    assert red == set()
    assert stub.sent == []


def test_logs_even_when_notify_is_not_configured(monkeypatch, caplog):
    """The daemon log is a surface of its own — a red finding must not depend entirely on
    CHELA_NOTIFY_URL being set to be visible anywhere."""
    import logging

    stub = _StubNotify(enabled=False)
    monkeypatch.setattr(doctor, "notify", stub)
    monkeypatch.setattr(doctor, "check", lambda: [_finding(doctor.ERROR, "it broke")])

    with caplog.at_level(logging.ERROR, logger=doctor.log.name):
        red = doctor.check_and_notify(set())

    assert red == {("some.fact", "it broke")}
    assert stub.sent == []
    assert any("it broke" in r.getMessage() for r in caplog.records)


def test_a_second_distinct_error_under_an_already_red_fact_still_notifies(monkeypatch):
    """Findings are keyed by (fact, title), not fact alone — a different stuck PR or dead
    window under the same fact must not hide behind the first one that went red."""
    stub = _StubNotify(enabled=True)
    monkeypatch.setattr(doctor, "notify", stub)
    monkeypatch.setattr(
        doctor, "check", lambda: [_finding(doctor.ERROR, "window @1 is dead", fact="tmux.windows")])

    red = doctor.check_and_notify(set())
    assert len(stub.sent) == 1

    monkeypatch.setattr(
        doctor, "check",
        lambda: [
            _finding(doctor.ERROR, "window @1 is dead", fact="tmux.windows"),
            _finding(doctor.ERROR, "window @2 is dead", fact="tmux.windows"),
        ],
    )
    red = doctor.check_and_notify(red)

    assert len(stub.sent) == 2
    assert "window @2 is dead" in stub.sent[1][0]


def test_a_cleared_finding_does_not_come_back_as_new(monkeypatch):
    stub = _StubNotify(enabled=True)
    monkeypatch.setattr(doctor, "notify", stub)
    monkeypatch.setattr(doctor, "check", lambda: [_finding(doctor.ERROR, "it broke")])
    red = doctor.check_and_notify(set())

    monkeypatch.setattr(doctor, "check", lambda: [_finding(doctor.OK, "fixed now")])
    red = doctor.check_and_notify(red)

    assert red == set()
    assert len(stub.sent) == 1   # the recovery itself is not a new "red" notification


# --- production call-site: the daemon loop actually reaches check_and_notify ----------

def _run_daemon_ticks(monkeypatch, n: int) -> None:
    """Drive exactly `n` iterations of `cmd_run`'s `while not stop.stopping` loop with
    every other subsystem kept inert, so each tick reaches the doctor-check call-site.

    Mirrors `tests/test_context.py::_run_daemon_ticks` / `tests/test_update.py`'s
    `_run_one_daemon_tick` — the same shape of test that catches a call-site being unwired
    even though every unit test of the extracted seam (`check_and_notify`) stays green,
    because none of them exercise `cmd_run` itself. `last_doctor_check` starts at 0.0 in
    `cmd_run`, so the real epoch makes the doctor-check branch due on the very first pass —
    no need to fake time. Deliberately does NOT mock `time.time` (see test_context.py's
    `_run_daemon_ticks` docstring for why: `main.time` is the real stdlib module, shared
    process-wide with logging's own `time.time()` calls).
    """
    monkeypatch.setattr(main.GracefulShutdown, "install", lambda self: self)

    remaining = n

    def stop_after_n(self, _seconds):
        nonlocal remaining
        remaining -= 1
        if remaining <= 0:
            self._event.set()
            return True
        return False

    monkeypatch.setattr(main.GracefulShutdown, "wait", stop_after_n)
    monkeypatch.setattr(main.scheduler, "init", lambda: None)
    monkeypatch.setattr(main.scheduler, "tick", lambda: 0)
    monkeypatch.setattr(main.agent_manager, "reconcile_window_names", lambda: [])
    monkeypatch.setattr(main, "DISPATCH_WORKFLOWS", [])
    monkeypatch.setattr(main.notify, "enabled", lambda: False)
    monkeypatch.setattr(main.rooms, "has_pending", lambda: False)
    monkeypatch.setattr(main.inbox, "enabled", lambda: False)
    monkeypatch.setattr(main.capabilities, "effective", lambda: [])
    monkeypatch.setattr(main.capabilities, "announce", lambda caps, log: None)
    monkeypatch.setattr(main.capabilities, "publish", lambda caps, boot_id: None)
    # update's own hourly check must not shell out to real git and mask the assertions below.
    monkeypatch.setattr(main.update, "auto_apply_enabled", lambda: False)
    monkeypatch.setattr(main.update, "check_and_notify", lambda behind: behind)


def test_the_daemon_loop_calls_doctor_check_and_notify_on_the_first_tick(monkeypatch):
    """🔴 WIRING (production call-site) — every test above exercises `check_and_notify` in
    isolation, so they ALL stay green even if `cmd_run` never reaches it (e.g. the
    `if now - last_doctor_check >= DOCTOR_CHECK_INTERVAL:` guard reverted to `if False`).
    Drives one real tick of the daemon loop and proves the wire is connected."""
    _run_daemon_ticks(monkeypatch, n=1)
    calls = []
    monkeypatch.setattr(main.doctor, "check_and_notify", lambda red: calls.append(red) or set())

    main.cmd_run(SimpleNamespace())

    assert calls == [set()], (
        "cmd_run did NOT call doctor.check_and_notify on the loop pass — the red-finding "
        "escalation is unwired and can be reverted with the suite green"
    )


def test_the_daemon_loop_does_not_re_check_before_the_interval_elapses(monkeypatch):
    """🔴 WIRING (cadence persistence) — the interval guard is inline
    (`last_doctor_check = now` right before calling `check_and_notify`), so reverting it to
    `last_doctor_check = last_doctor_check` still lets the single-tick test above pass
    (`last_doctor_check` starts at 0.0, so tick 1 is due either way). Drive two REAL ticks,
    microseconds apart — correct code calls `check_and_notify` once (tick 1 advances
    `last_doctor_check` to ~now, so tick 2's microsecond gap is nowhere near
    DOCTOR_CHECK_INTERVAL); a frozen `last_doctor_check` would call it on BOTH ticks (it
    never advances off 0.0, and the real epoch is always >> DOCTOR_CHECK_INTERVAL)."""
    _run_daemon_ticks(monkeypatch, n=2)
    calls = []
    monkeypatch.setattr(main.doctor, "check_and_notify", lambda red: calls.append(red) or set())

    main.cmd_run(SimpleNamespace())

    assert len(calls) == 1, (
        "doctor.check_and_notify was called on both ticks — last_doctor_check is not "
        "advancing after a check, so the audit would run on EVERY daemon pass instead of "
        f"on DOCTOR_CHECK_INTERVAL (called {len(calls)} times)"
    )
