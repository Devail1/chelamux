"""CMX-25: an agent BLOCKED by its auto-mode permission classifier never reaches the native
``waiting`` state (there is no prompt — the session goes busy → idle), so
``notify.check_waiting`` never pushed for it. ``notify.DeniedWatch`` tails the event log
for ``hook.permission_denied`` instead: one push per window per cooldown, sent only by the
holder of the announcer lease."""
from __future__ import annotations

import pytest

from chela import event_log, hooks, notify


class _AlwaysOwner:
    def acquire(self):
        return True


@pytest.fixture
def world(monkeypatch, tmp_path):
    state = {"sent": []}
    monkeypatch.setattr(notify, "NOTIFY_URL", "https://ntfy.sh/test-topic")
    monkeypatch.setattr(notify.discovery, "get_all_windows",
                        lambda: {"tradeplan-2": "@33", "builder": "@7"})
    monkeypatch.setattr(notify, "send",
                        lambda message, title=None: state["sent"].append(message) or True)
    daemon = notify.OwnerLock(tmp_path / notify.LOCK_NAME)
    dashboard = notify.OwnerLock(tmp_path / notify.LOCK_NAME)
    yield state, daemon, dashboard
    daemon.release()
    dashboard.release()


def _deny(wid, command="kill 4242; rm -f /tmp/x.pid"):
    body = {"session_id": "s", "tool_name": "Bash", "tool_input": {"command": command}}
    return event_log.append(notify.DENIED_TYPE, hooks.summarize("PermissionDenied", body),
                            hooks.clip_payload(body), wid=wid)


def _armed(owner):
    watch = notify.DeniedWatch()
    assert watch.check(owner=owner, now=0) == []   # anchors at the tip
    return watch


def test_type_is_the_one_hooks_ingest_writes():
    assert notify.DENIED_TYPE == "hook.permission_denied"


def test_constants_are_pinned():
    assert notify.DENIED_COOLDOWN_S == 600          # one push per window per 10 min
    assert notify.DENIED_DETAIL_CHARS == 80


def test_a_denial_pushes_once_with_window_and_command(world):
    state, daemon, _ = world
    watch = _armed(daemon)
    _deny("@33")
    watch.check(owner=daemon, now=1000)
    assert state["sent"] == [
        "@33 tradeplan-2: blocked by its permission classifier: kill 4242; rm -f /tmp/x.pid"]
    watch.check(owner=daemon, now=1001)            # nothing new in the log
    assert len(state["sent"]) == 1


def test_long_command_is_clipped(world):
    state, daemon, _ = world
    watch = _armed(daemon)
    _deny("@33", command="x" * 300)
    watch.check(owner=daemon, now=1000)
    detail = state["sent"][0].split("classifier: ", 1)[1]
    assert len(detail) == notify.DENIED_DETAIL_CHARS and detail.endswith("…")


def test_repeat_on_the_same_window_within_the_cooldown_is_suppressed(world):
    state, daemon, _ = world
    watch = _armed(daemon)
    _deny("@33")
    _deny("@33")                                   # a burst in one tick
    watch.check(owner=daemon, now=1000)
    _deny("@33")
    watch.check(owner=daemon, now=1000 + notify.DENIED_COOLDOWN_S - 1)
    assert len(state["sent"]) == 1

    _deny("@33")                                   # cooldown over: push again
    watch.check(owner=daemon, now=1000 + notify.DENIED_COOLDOWN_S)
    assert len(state["sent"]) == 2


def test_a_second_window_still_pushes(world):
    state, daemon, _ = world
    watch = _armed(daemon)
    _deny("@33")
    watch.check(owner=daemon, now=1000)
    _deny("@7", command="git push --force")
    watch.check(owner=daemon, now=1001)
    assert state["sent"][1] == "@7 builder: blocked by its permission classifier: git push --force"


def test_a_non_holder_of_the_lease_never_sends(world):
    state, daemon, dashboard = world
    holder, standby = _armed(daemon), _armed(dashboard)
    _deny("@33")
    standby.check(owner=dashboard, now=999)        # dashboard ticks first, but is not owner
    holder.check(owner=daemon, now=1000)
    assert len(state["sent"]) == 1

    # The standby advanced its cursor: taking over later does not replay that denial.
    daemon.release()
    standby.check(owner=dashboard, now=2000)
    assert len(state["sent"]) == 1


def test_negative_control_without_the_lock_both_send(world):
    state, _, _ = world
    a, b = _armed(_AlwaysOwner()), _armed(_AlwaysOwner())
    _deny("@33")
    a.check(owner=_AlwaysOwner(), now=1000)
    b.check(owner=_AlwaysOwner(), now=1000)
    assert len(state["sent"]) == 2                 # so the test above is the lock


def test_first_check_does_not_replay_history(world):
    state, daemon, _ = world
    _deny("@33")                                   # before the process started
    watch = notify.DeniedWatch()
    watch.check(owner=daemon, now=1000)
    watch.check(owner=daemon, now=1001)
    assert state["sent"] == []


def test_a_denial_with_no_window_is_not_announced(world):
    state, daemon, _ = world
    watch = _armed(daemon)
    _deny(None)
    watch.check(owner=daemon, now=1000)
    assert state["sent"] == []


def test_notifications_off_is_a_noop(world, monkeypatch):
    state, daemon, _ = world
    watch = _armed(daemon)
    monkeypatch.setattr(notify, "NOTIFY_URL", "")
    _deny("@33")
    assert watch.check(owner=daemon, now=1000) == []
    assert state["sent"] == []


class _StopLoop(BaseException):
    """Escapes a `while True` loop whose body catches every ``Exception``."""


class _RecordingWatch:
    """Stands in for ``notify.DeniedWatch`` at a production call site: counts checks."""
    instances: list["_RecordingWatch"] = []

    def __init__(self):
        self.calls = 0
        _RecordingWatch.instances.append(self)

    def check(self, *a, **kw):
        self.calls += 1
        return []


def _record_watches(monkeypatch) -> list[_RecordingWatch]:
    _RecordingWatch.instances = []
    monkeypatch.setattr(notify, "DeniedWatch", _RecordingWatch)
    return _RecordingWatch.instances


def test_the_daemon_loop_runs_the_watch(monkeypatch):
    """🔴 WIRING (``chela run``) — drives ONE real pass of ``cmd_run``'s loop with notify on
    and asserts the watch was actually CHECKED, not merely mentioned in the source. A
    dead-coded call (``False and denied_watch.check()``) leaves the text intact and must
    still go RED here."""
    from types import SimpleNamespace

    from chela import main
    from tests.test_main_dispatch_log import _stub_daemon_loop_inert
    _stub_daemon_loop_inert(monkeypatch)
    monkeypatch.setattr(main, "DISPATCH_WORKFLOWS", [])
    monkeypatch.setattr(main.notify, "enabled", lambda: True)
    monkeypatch.setattr(main.notify, "check_waiting", lambda seen: seen)
    watches = _record_watches(monkeypatch)

    main.cmd_run(SimpleNamespace())

    assert [w.calls for w in watches] == [1], (
        "cmd_run's notify tick never called DeniedWatch.check — classifier denials are unwired")


def test_the_dashboard_notifier_thread_runs_the_watch(monkeypatch):
    """🔴 WIRING (dashboard) — runs ``_start_notifier``'s thread body synchronously for one
    iteration and asserts the watch was CHECKED."""
    import threading
    from types import SimpleNamespace

    from chela.dashboard import app

    class _InlineThread:
        def __init__(self, target, **_kw):
            self._target = target

        def start(self):
            self._target()

    def _stop(_s):
        raise _StopLoop

    monkeypatch.setattr(app.notify, "enabled", lambda: True)
    monkeypatch.setattr(app.notify, "check_waiting", lambda seen: seen)
    monkeypatch.setattr(threading, "Thread", _InlineThread)
    monkeypatch.setattr(app, "time", SimpleNamespace(sleep=_stop))
    watches = _record_watches(monkeypatch)

    with pytest.raises(_StopLoop):
        app._start_notifier()

    assert [w.calls for w in watches] == [1], (
        "the dashboard notifier thread never called DeniedWatch.check — denials are unwired")


def test_a_daemon_restart_does_not_replay_old_denials(world):
    """A new boot id makes the cursor untrustworthy; the log then resumes from what it
    serves — old denials must stay quiet, a fresh one must still push."""
    state, daemon, _ = world
    watch = _armed(daemon)
    _deny("@33")
    watch.check(owner=daemon, now=1000)
    assert len(state["sent"]) == 1

    event_log.new_boot()
    _deny("@7")
    watch.check(owner=daemon, now=1000 + notify.DENIED_COOLDOWN_S)
    assert [m.split(":")[0] for m in state["sent"]] == ["@33 tradeplan-2", "@7 builder"]
