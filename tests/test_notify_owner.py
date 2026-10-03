"""CMX-9: every needs-input push arrived twice — the daemon loop and the dashboard thread
each ran ``notify.check_waiting`` with their own in-memory ``seen`` set. Only the holder
of ``$CHELA_DIR/notify.lock`` may send now. Two ``OwnerLock`` instances on one path stand
in for the two processes (``flock`` conflicts per open file description, even in one
process)."""
from __future__ import annotations

import pytest

from chela import notify


class _AlwaysOwner:
    """The lock disabled: every caller believes it is the announcer."""

    def acquire(self):
        return True


@pytest.fixture
def world(monkeypatch, tmp_path):
    state = {"waiting": set(), "sent": []}
    monkeypatch.setattr(notify, "NOTIFY_URL", "https://ntfy.sh/test-topic")
    monkeypatch.setattr(notify, "waiting_windows", lambda: set(state["waiting"]))
    monkeypatch.setattr(notify, "send",
                        lambda message, title=None: state["sent"].append(message) or True)
    daemon = notify.OwnerLock(tmp_path / notify.LOCK_NAME)
    dashboard = notify.OwnerLock(tmp_path / notify.LOCK_NAME)
    yield state, daemon, dashboard
    daemon.release()
    dashboard.release()


def test_two_notifiers_send_one_push_per_transition(world):
    state, daemon, dashboard = world
    seen_a, seen_b = set(), set()
    state["waiting"] = {"agent-1"}

    seen_a = notify.check_waiting(seen_a, owner=daemon)
    seen_b = notify.check_waiting(seen_b, owner=dashboard)

    assert state["sent"] == ["agent-1 is waiting for input"]
    assert seen_a == seen_b == {"agent-1"}   # the standby still tracks the set


def test_negative_control_without_the_lock_both_send(world):
    state, _, _ = world
    state["waiting"] = {"agent-1"}

    notify.check_waiting(set(), owner=_AlwaysOwner())
    notify.check_waiting(set(), owner=_AlwaysOwner())

    assert len(state["sent"]) == 2   # the test above discriminates: it is the lock


def test_standby_takes_over_when_the_holder_goes_away(world):
    state, daemon, dashboard = world
    seen_a, seen_b = set(), set()
    state["waiting"] = {"agent-1"}
    seen_a = notify.check_waiting(seen_a, owner=daemon)
    seen_b = notify.check_waiting(seen_b, owner=dashboard)
    assert len(state["sent"]) == 1

    daemon.release()   # the daemon process died: the kernel drops its flock

    seen_b = notify.check_waiting(seen_b, owner=dashboard)
    assert len(state["sent"]) == 1   # agent-1 was already announced — not again

    state["waiting"] = {"agent-1", "agent-2"}
    seen_b = notify.check_waiting(seen_b, owner=dashboard)
    assert state["sent"][1:] == ["agent-2 is waiting for input"]

    # ...and the old holder, coming back, is now the silent one.
    notify.check_waiting(set(), owner=daemon)
    assert len(state["sent"]) == 2


def test_edge_trigger_semantics_are_unchanged(world):
    state, daemon, _ = world
    seen = set()
    state["waiting"] = {"agent-1"}
    seen = notify.check_waiting(seen, owner=daemon)
    seen = notify.check_waiting(seen, owner=daemon)   # still waiting: no re-notify
    assert len(state["sent"]) == 1

    state["waiting"] = set()
    seen = notify.check_waiting(seen, owner=daemon)   # left waiting
    state["waiting"] = {"agent-1"}
    seen = notify.check_waiting(seen, owner=daemon)   # re-entered: notify again
    assert state["sent"] == ["agent-1 is waiting for input"] * 2


def test_default_owner_is_the_process_wide_lease_under_chela_dir(world, monkeypatch, tmp_path):
    """Both real call sites (daemon loop, dashboard thread) pass no owner — so the default
    must be one lease on ``$CHELA_DIR/notify.lock``, which a second instance can't take."""
    state, _, _ = world
    monkeypatch.setattr(notify.config, "CHELA_DIR", tmp_path / "chela-dir")
    lease = notify.OwnerLock()
    monkeypatch.setattr(notify, "_owner", lease)
    rival = notify.OwnerLock(tmp_path / "chela-dir" / notify.LOCK_NAME)
    try:
        state["waiting"] = {"agent-1"}
        notify.check_waiting(set())
        assert state["sent"] == ["agent-1 is waiting for input"]
        assert rival.acquire() is False
    finally:
        rival.release()
        lease.release()
