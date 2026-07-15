"""🎭🤖 The orchestrator auto-launch — inbox-woken, attended-lease-gated (CMX-90).

The launch ACTION is a tmux spawn (untestable here); the launch DECISION is a pure, fail-closed
function, and that is where the guard lives. These corrupt each gate and watch a launch that
should have been withheld fire — and prove ``maybe_wake`` only spawns when the gate says go.
"""
from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from chela import config, inbox
from chela.personas import autolaunch, lease


# The all-conditions-hold kwargs for should_launch: a launch SHOULD fire. Each test flips exactly
# one to False and asserts the launch is withheld — i.e. every gate is load-bearing.
def _all_go() -> dict:
    return dict(flag_on=True, lease_active=True, has_pending_work=True,
                orchestrator_live=False, recently_launched=False)


def test_should_launch_fires_only_when_every_condition_holds():
    go, reason = autolaunch.should_launch(**_all_go())
    assert go is True
    assert reason == ""


@pytest.mark.parametrize("flip", [
    {"flag_on": False},
    {"lease_active": False},
    {"has_pending_work": False},
    {"orchestrator_live": True},
    {"recently_launched": True},
])
def test_should_launch_is_fail_closed_on_any_single_gate(flip):
    """🔴 FAIL-CLOSED — flip ANY one input off (or a 'no need' input on) and the launch is
    withheld with a non-empty reason. Corrupt should_launch to drop a gate (e.g. stop checking
    ``lease_active``) and the matching row here goes red — a launch fires that should not."""
    kwargs = {**_all_go(), **flip}
    go, reason = autolaunch.should_launch(**kwargs)
    assert go is False
    assert reason.strip(), "a withheld launch must say why"


def test_the_flag_is_off_by_default():
    # 🔴 DEFAULT-OFF — auto-launching a merge-authority agent must be opt-in. The test env does
    # not set CHELA_ORCHESTRATOR, so the latched config value must read False. Corrupt the config
    # expression to default-on (flip `in` → `not in`) and this goes red.
    assert config.ORCHESTRATOR_ENABLED is False
    assert autolaunch.enabled() is False


def test_the_lease_gate_specifically_blocks_without_a_lease():
    # The supervision gate, called out on its own: everything else armed, but no attended-lease.
    go, reason = autolaunch.should_launch(**{**_all_go(), "lease_active": False})
    assert go is False
    assert "attended" in reason.lower() or "attend" in reason.lower()


# --- evaluate(): the impure read wired to the pure decision -----------------------------------

@pytest.fixture
def armed(monkeypatch):
    """Flag on + attended-lease active + no live orchestrator + not recently launched."""
    monkeypatch.setattr(config, "ORCHESTRATOR_ENABLED", True)
    lease.grant(ttl_seconds=600)
    monkeypatch.setattr(autolaunch.inbox, "address_state",
                        lambda *a, **k: (inbox.ADDR_NONE, "nobody registered"))
    # ensure no stale launch stamp
    monkeypatch.setattr(autolaunch, "recently_launched", lambda *a, **k: False)


def test_evaluate_says_go_when_armed_and_work_is_queued(armed):
    go, reason = autolaunch.evaluate({"queue": [{"kind": "run_review"}]}, {}, None)
    assert go is True, reason


def test_evaluate_withholds_when_no_work_is_queued(armed):
    # inbox-woken: an empty queue is nothing to wake for, even fully armed.
    go, reason = autolaunch.evaluate({"queue": []}, {}, None)
    assert go is False
    assert "pending" in reason or "work" in reason


def test_evaluate_withholds_when_an_orchestrator_is_already_live(armed, monkeypatch):
    monkeypatch.setattr(autolaunch.inbox, "address_state",
                        lambda *a, **k: (inbox.ADDR_OK, ""))
    go, reason = autolaunch.evaluate({"queue": [{"kind": "run_review"}]}, {}, None)
    assert go is False
    assert "already" in reason


def test_evaluate_withholds_when_the_flag_is_off(armed, monkeypatch):
    monkeypatch.setattr(config, "ORCHESTRATOR_ENABLED", False)
    go, reason = autolaunch.evaluate({"queue": [{"kind": "run_review"}]}, {}, None)
    assert go is False
    assert "off" in reason or "CHELA_ORCHESTRATOR" in reason


def test_evaluate_withholds_when_the_lease_lapsed(armed):
    lease.release()  # the human stopped attending
    go, reason = autolaunch.evaluate({"queue": [{"kind": "run_review"}]}, {}, None)
    assert go is False
    assert "lease" in reason or "attend" in reason


# --- maybe_wake(): only spawns when the gate says go ------------------------------------------

def test_maybe_wake_launches_only_when_evaluate_says_go(monkeypatch):
    """🔴 WIRING — maybe_wake must call wake() iff evaluate() says go. Corrupt maybe_wake to
    always call wake (drop the ``if not go`` guard) and the withheld case goes red."""
    calls = []
    monkeypatch.setattr(autolaunch, "wake", lambda *a, **k: calls.append(k or a) or {"ok": True})

    monkeypatch.setattr(autolaunch, "evaluate", lambda *a, **k: (False, "withheld"))
    assert autolaunch.maybe_wake({"queue": []}, {}, None) is None
    assert calls == [], "wake must NOT be called when the gate withholds"

    monkeypatch.setattr(autolaunch, "evaluate", lambda *a, **k: (True, ""))
    result = autolaunch.maybe_wake({"queue": [{"kind": "x"}]}, {}, None)
    assert result == {"ok": True}
    assert len(calls) == 1, "wake MUST be called exactly once when the gate says go"


# --- the PRODUCTION call-site: the daemon loop actually calls maybe_wake each tick ------------

def test_the_daemon_loop_calls_maybe_wake_on_the_inbox_tick(monkeypatch):
    """🔴 WIRING (production call-site) — every test above exercises ``autolaunch`` in isolation,
    so they ALL stay green even if ``cmd_run`` never calls it: the whole feature can be reverted
    (``autolaunch.maybe_wake(...)`` → ``pass`` in ``chela/main.py``) with the suite green. This
    drives ONE real tick of the daemon loop and proves the wire is connected — replace that call
    with ``pass`` and this goes red, which is the only thing that keeps the feature wired in.
    """
    from chela import main

    # install() no-op so pytest's own signal handlers survive; the end-of-tick wait() sets the
    # stop flag so EXACTLY ONE iteration of the `while not stop.stopping` loop runs.
    monkeypatch.setattr(main.GracefulShutdown, "install", lambda self: self)

    def stop_after_one(self, _seconds):
        self._event.set()
        return True

    monkeypatch.setattr(main.GracefulShutdown, "wait", stop_after_one)

    # Keep the tick inert: no scheduler work, no window renames, no dispatch, no notify, no rooms.
    monkeypatch.setattr(main.scheduler, "init", lambda: None)
    monkeypatch.setattr(main.scheduler, "tick", lambda: 0)
    monkeypatch.setattr(main.agent_manager, "reconcile_window_names", lambda: [])
    monkeypatch.setattr(main, "DISPATCH_WORKFLOWS", [])
    monkeypatch.setattr(main.notify, "enabled", lambda: False)
    monkeypatch.setattr(main.rooms, "has_pending", lambda: False)

    # The inbox branch must run (that is where the auto-launch call lives), and it must be armed.
    monkeypatch.setattr(main.inbox, "enabled", lambda: True)
    monkeypatch.setattr(main.inbox, "orchestrator_wid", lambda: None)
    monkeypatch.setattr(main.inbox, "tick", lambda statuses: statuses)
    monkeypatch.setattr(main.inbox, "load", lambda: {"queue": [{"kind": "run_review"}]})
    monkeypatch.setattr(main.epoch, "current", lambda: "e1")
    monkeypatch.setattr(main.autolaunch, "enabled", lambda: True)

    calls: list[tuple] = []
    monkeypatch.setattr(main.autolaunch, "maybe_wake",
                        lambda *a, **k: calls.append(a) or None)

    main.cmd_run(SimpleNamespace())

    assert len(calls) == 1, (
        "cmd_run did NOT call autolaunch.maybe_wake on the inbox tick — the feature is unwired "
        "and can be reverted with the suite green"
    )
    # ...and it must feed the wire the LIVE inbox + statuses + epoch, not empty placeholders.
    args = calls[0]
    assert args[0] == {"queue": [{"kind": "run_review"}]}   # inbox.load()
    assert args[1] == {}                                    # inbox.tick()'s status snapshot
    assert args[2] == "e1"                                  # epoch.current()


def test_the_daemon_loop_skips_maybe_wake_when_autolaunch_is_disabled(monkeypatch):
    """The flip side of the wire: with the flag off (the default), the loop must NOT reach
    maybe_wake — the same one-tick harness, autolaunch.enabled() False, so the guarded call is
    skipped. Corrupt the `if autolaunch.enabled():` guard in cmd_run and this goes red.
    """
    from chela import main

    monkeypatch.setattr(main.GracefulShutdown, "install", lambda self: self)
    monkeypatch.setattr(main.GracefulShutdown, "wait",
                        lambda self, _s: (self._event.set(), True)[1])
    monkeypatch.setattr(main.scheduler, "init", lambda: None)
    monkeypatch.setattr(main.scheduler, "tick", lambda: 0)
    monkeypatch.setattr(main.agent_manager, "reconcile_window_names", lambda: [])
    monkeypatch.setattr(main, "DISPATCH_WORKFLOWS", [])
    monkeypatch.setattr(main.notify, "enabled", lambda: False)
    monkeypatch.setattr(main.rooms, "has_pending", lambda: False)
    monkeypatch.setattr(main.inbox, "enabled", lambda: True)
    monkeypatch.setattr(main.inbox, "orchestrator_wid", lambda: None)
    monkeypatch.setattr(main.inbox, "tick", lambda statuses: statuses)
    monkeypatch.setattr(main.inbox, "load", lambda: {"queue": [{"kind": "run_review"}]})
    monkeypatch.setattr(main.epoch, "current", lambda: "e1")
    monkeypatch.setattr(main.autolaunch, "enabled", lambda: False)

    calls: list[tuple] = []
    monkeypatch.setattr(main.autolaunch, "maybe_wake", lambda *a, **k: calls.append(a))

    main.cmd_run(SimpleNamespace())

    assert calls == [], "maybe_wake must not be reached when auto-launch is disabled"


# --- the relaunch cooldown --------------------------------------------------------------------

def test_recently_launched_reflects_the_stamp():
    assert autolaunch.recently_launched() is False       # no stamp yet
    autolaunch.record_launch("@9", now=1000.0)
    assert autolaunch.recently_launched(now=1000.0 + 10) is True
    # past the cooldown → not recent any more
    assert autolaunch.recently_launched(now=1000.0 + autolaunch.RELAUNCH_COOLDOWN_SECONDS + 1) is False


def test_recently_launched_is_false_on_a_corrupt_stamp():
    autolaunch.record_launch("@9", now=1000.0)
    autolaunch._state_path().write_text("nonsense", encoding="utf-8")
    # fail-OPEN here by design (a corrupt stamp must not WITHHOLD an otherwise-armed launch)
    assert autolaunch.recently_launched(now=1000.0 + 10) is False


# --- the spawn stamps the ACTOR: the wiring the action-gate depends on ------------------------

def test_the_spawned_window_exports_the_auto_orchestrator_actor_stamp(monkeypatch):
    """🔴 ACTION-GATE WIRING — the launched window MUST export CHELA_ACTOR=auto-orchestrator, or
    ``contract.merge`` can never tell an auto-orchestrator merge apart from a human's and the
    attended-lease action-gate silently never fires in production. Drop the actor from the export
    in ``_spawn_orchestrator_window`` and this goes red."""
    sent: list[str] = []

    def fake_run(argv, *a, **k):
        if argv[:2] == ["tmux", "new-window"]:
            return SimpleNamespace(stdout="@42\n", returncode=0)
        if argv[:2] == ["tmux", "send-keys"]:
            sent.append(argv[4])          # the keystrokes being sent to the pane
        return SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    target = autolaunch._spawn_orchestrator_window("/tmp/repo")
    assert target == "@42"
    export = next((s for s in sent if "export CHELA_WID" in s), "")
    assert f"{config.ACTOR_ENV}={config.AUTO_ORCHESTRATOR_ACTOR}" in export, sent
    assert "CHELA_WID=@42" in export      # self-identity still exported alongside


# --- orchestrator_live(): maps the inbox address state ----------------------------------------

@pytest.mark.parametrize("state,expected", [
    (inbox.ADDR_OK, True),
    (inbox.ADDR_UNSTAMPED, True),
    (inbox.ADDR_NONE, False),
    (inbox.ADDR_GONE, False),
    (inbox.ADDR_DANGLING, False),
])
def test_orchestrator_live_maps_address_state(monkeypatch, state, expected):
    monkeypatch.setattr(autolaunch.inbox, "address_state", lambda *a, **k: (state, ""))
    assert autolaunch.orchestrator_live({}, {}, None) is expected
