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


# ================================================================================================
# --- teardown (CMX-100): the symmetric close ----------------------------------------------------
# ================================================================================================

# The all-conditions-hold kwargs for should_teardown: a teardown SHOULD fire (queue drained).
def _all_teardown_go() -> dict:
    return dict(we_launched_it=True, orchestrator_idle=True, has_pending_work=False,
                lease_active=True)


def test_should_teardown_fires_when_drained_idle_and_owned():
    go, reason = autolaunch.should_teardown(**_all_teardown_go())
    assert go is True
    assert reason.strip()


def test_should_teardown_fires_when_lease_lapsed_even_with_pending_work():
    # 🔴 THE LEASE-EXPIRY CASE — the original backlog ask: pending work but no active lease means
    # nothing can act on it anyway (should_launch itself requires lease_active), so the window is
    # torn down rather than left running unattended. Corrupt this branch away and the lease-expiry
    # teardown never fires.
    go, reason = autolaunch.should_teardown(
        we_launched_it=True, orchestrator_idle=True, has_pending_work=True, lease_active=False,
    )
    assert go is True
    assert "lease" in reason.lower()


@pytest.mark.parametrize("flip", [
    {"we_launched_it": False},
    {"orchestrator_idle": False},
])
def test_should_teardown_is_fail_closed_on_the_safety_gates(flip):
    """🔴 FAIL-CLOSED (the destructive direction) — flip ownership or idleness off and teardown
    must be withheld, no matter how "done" the queue/lease look. Corrupt should_teardown to drop
    either check and this goes red: a hand-run session or a mid-turn window gets killed."""
    kwargs = {**_all_teardown_go(), **flip}
    go, reason = autolaunch.should_teardown(**kwargs)
    assert go is False
    assert reason.strip(), "a withheld teardown must say why"


def test_should_teardown_withholds_when_there_is_still_work_and_an_active_lease():
    go, reason = autolaunch.should_teardown(
        we_launched_it=True, orchestrator_idle=True, has_pending_work=True, lease_active=True,
    )
    assert go is False
    assert reason.strip()


def test_the_ownership_gate_specifically_blocks_a_hand_run_session():
    # Called out on its own, mirroring test_the_lease_gate_specifically_blocks_without_a_lease:
    # everything else says "done", but this window was never confirmed as chela's own launch.
    go, reason = autolaunch.should_teardown(**{**_all_teardown_go(), "we_launched_it": False})
    assert go is False
    assert "hand-run" in reason.lower() or "confirmed" in reason.lower()


# --- we_launched(): the ownership stamp, read fail-closed ---------------------------------------

def test_we_launched_is_false_with_no_stamp():
    assert autolaunch.we_launched("@7") is False


def test_we_launched_is_true_only_for_the_recorded_wid():
    autolaunch.record_launch("@7")
    assert autolaunch.we_launched("@7") is True
    assert autolaunch.we_launched("@8") is False


def test_we_launched_is_false_on_a_corrupt_stamp():
    autolaunch.record_launch("@7")
    autolaunch._state_path().write_text("not json", encoding="utf-8")
    # 🔴 FAIL-CLOSED — a corrupt stamp must never be read as "yes, we own this window".
    assert autolaunch.we_launched("@7") is False


def test_we_launched_is_false_for_an_empty_wid():
    autolaunch.record_launch("")
    assert autolaunch.we_launched("") is False


# --- evaluate_teardown(): the impure read wired to the pure decision -----------------------------

@pytest.fixture
def owned_and_idle(monkeypatch):
    """The orchestrator wid is registered, we launched it, and it is idle."""
    monkeypatch.setattr(autolaunch.inbox, "orchestrator_wid", lambda store=None: "@7")
    autolaunch.record_launch("@7")


def test_evaluate_teardown_says_go_when_queue_is_drained(owned_and_idle):
    lease.grant(ttl_seconds=600)
    go, reason = autolaunch.evaluate_teardown({"queue": []}, {"@7": inbox.IDLE})
    assert go is True, reason


def test_evaluate_teardown_withholds_with_pending_work_and_active_lease(owned_and_idle):
    lease.grant(ttl_seconds=600)
    go, reason = autolaunch.evaluate_teardown(
        {"queue": [{"kind": "run_review"}]}, {"@7": inbox.IDLE},
    )
    assert go is False


def test_evaluate_teardown_withholds_when_busy(owned_and_idle):
    lease.grant(ttl_seconds=600)
    go, reason = autolaunch.evaluate_teardown({"queue": []}, {"@7": inbox.BUSY})
    assert go is False
    assert "mid-turn" in reason or "working" in reason


def test_evaluate_teardown_withholds_when_the_idle_state_is_unknown(owned_and_idle):
    """🔴 FAIL-SAFE ON UNKNOWN (load-bearing) — an unknown/unreadable window status is NOT idle.
    With the queue drained AND the lease active, the only thing between @7 and a graceful teardown
    is its idle state; when that state cannot be read (the wid is absent from the status map),
    teardown must be WITHHELD, never fired on the ambiguity — the same fail-closed discipline the
    launch gate uses. Corrupt (default unknown→idle, e.g. ``!= BUSY`` instead of ``== IDLE``) →
    this goes red because the graceful teardown then fires on an unreadable window."""
    lease.grant(ttl_seconds=600)
    go, reason = autolaunch.evaluate_teardown({"queue": []}, {})   # @7 absent ⇒ status unknown
    assert go is False, reason


def test_evaluate_teardown_withholds_when_nobody_is_registered():
    go, reason = autolaunch.evaluate_teardown({"queue": []}, {})
    assert go is False
    assert "nothing to tear down" in reason


def test_evaluate_teardown_withholds_when_the_window_was_not_our_launch(monkeypatch):
    monkeypatch.setattr(autolaunch.inbox, "orchestrator_wid", lambda store=None: "@9")
    # no record_launch call — nobody stamped @9 as an auto-launch
    go, reason = autolaunch.evaluate_teardown({"queue": []}, {"@9": inbox.IDLE})
    assert go is False
    assert "hand-run" in reason.lower() or "confirmed" in reason.lower()


# --- maybe_teardown(): only tears down when evaluate_teardown says go ----------------------------

def test_maybe_teardown_tears_down_only_when_evaluate_teardown_says_go(monkeypatch):
    """🔴 WIRING — maybe_teardown must call teardown() iff evaluate_teardown() says go. Corrupt
    maybe_teardown to always tear down (drop the ``if not go`` guard) and the withheld case goes
    red."""
    calls = []
    monkeypatch.setattr(autolaunch, "teardown",
                        lambda wid, reason: calls.append((wid, reason)) or {"ok": True})

    monkeypatch.setattr(autolaunch, "evaluate_teardown", lambda *a, **k: (False, "withheld"))
    assert autolaunch.maybe_teardown({"queue": []}, {}) is None
    assert calls == [], "teardown must NOT be called when the gate withholds"

    monkeypatch.setattr(autolaunch, "evaluate_teardown", lambda *a, **k: (True, "drained"))
    monkeypatch.setattr(autolaunch.inbox, "orchestrator_wid", lambda store=None: "@7")
    result = autolaunch.maybe_teardown({"queue": []}, {})
    assert result == {"ok": True}
    assert calls == [("@7", "drained")]


# --- teardown(): the action — kills the window, clears the stamp, logs --------------------------

def test_teardown_kills_the_recorded_window_and_clears_the_stamp(monkeypatch):
    autolaunch.record_launch("@7")
    killed = []

    def fake_run(argv, *a, **k):
        if argv[:2] == ["tmux", "kill-window"]:
            killed.append(argv)
        return SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = autolaunch.teardown("@7", "the inbox queue is drained")
    assert result == {"ok": True, "wid": "@7", "reason": "the inbox queue is drained"}
    assert killed and killed[0][-1] == f"{config.TMUX_SESSION}:@7"
    # the launch stamp is gone — a stale cooldown must not survive the window it described
    assert autolaunch._read_launch_stamp() is None


def test_teardown_unregisters_the_inbox_address(monkeypatch):
    """🔴 ATOMICITY (load-bearing) — teardown must UNREGISTER the inbox address, not only kill the
    window and clear the stamp. Killing the window while leaving its wid registered leaves a DEAD
    ADDRESS: :func:`inbox.orchestrator_wid` keeps returning it and :func:`inbox.deliver` refuses
    to write to it (ADDR_GONE) while the queue silently backs up. Corrupt (drop the
    ``inbox.unregister(wid)`` call from :func:`teardown`) → this goes red."""
    autolaunch.record_launch("@7")
    unregistered = []
    monkeypatch.setattr(autolaunch.inbox, "unregister",
                        lambda wid: unregistered.append(wid) or {"ok": True, "wid": wid})
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="", returncode=0))

    autolaunch.teardown("@7", "the inbox queue is drained")

    assert unregistered == ["@7"], "teardown must clear the registered address for the killed window"


def test_teardown_survives_a_missing_stamp_file(monkeypatch):
    # No record_launch call — the stamp file never existed. teardown() must not raise trying to
    # unlink it (best-effort, exactly like judge._cleanup's ordering).
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="", returncode=0))
    result = autolaunch.teardown("@7", "the attended-lease has lapsed")
    assert result["ok"] is True


# --- the PRODUCTION call-site: the daemon loop actually calls maybe_teardown each tick -----------

def test_the_daemon_loop_calls_maybe_teardown_on_the_inbox_tick(monkeypatch):
    """🔴 WIRING (production call-site) — mirrors the launch wiring test: drives ONE real tick of
    the daemon loop and proves maybe_teardown is reached. Replace the call in `chela/main.py`
    with `pass` and this goes red, which is the only thing that keeps the teardown wired in.
    """
    from chela import main

    monkeypatch.setattr(main.GracefulShutdown, "install", lambda self: self)

    def stop_after_one(self, _seconds):
        self._event.set()
        return True

    monkeypatch.setattr(main.GracefulShutdown, "wait", stop_after_one)
    monkeypatch.setattr(main.scheduler, "init", lambda: None)
    monkeypatch.setattr(main.scheduler, "tick", lambda: 0)
    monkeypatch.setattr(main.agent_manager, "reconcile_window_names", lambda: [])
    monkeypatch.setattr(main, "DISPATCH_WORKFLOWS", [])
    monkeypatch.setattr(main.notify, "enabled", lambda: False)
    monkeypatch.setattr(main.rooms, "has_pending", lambda: False)

    monkeypatch.setattr(main.inbox, "enabled", lambda: True)
    monkeypatch.setattr(main.inbox, "orchestrator_wid", lambda: None)
    monkeypatch.setattr(main.inbox, "tick", lambda statuses: statuses)
    monkeypatch.setattr(main.inbox, "load", lambda: {"queue": []})
    monkeypatch.setattr(main.epoch, "current", lambda: "e1")
    # Auto-launch itself OFF for this test — teardown must still be reached (it is checked
    # regardless of the launch flag; ownership is proven by the stamp, not by the flag).
    monkeypatch.setattr(main.autolaunch, "enabled", lambda: False)

    launch_calls: list[tuple] = []
    monkeypatch.setattr(main.autolaunch, "maybe_wake", lambda *a, **k: launch_calls.append(a))
    teardown_calls: list[tuple] = []
    monkeypatch.setattr(main.autolaunch, "maybe_teardown",
                        lambda *a, **k: teardown_calls.append(a) or None)

    main.cmd_run(SimpleNamespace())

    assert launch_calls == [], "maybe_wake must not run when the launch flag is off"
    assert len(teardown_calls) == 1, (
        "cmd_run did NOT call autolaunch.maybe_teardown on the inbox tick — teardown is unwired "
        "and can be reverted with the suite green"
    )
    args = teardown_calls[0]
    assert args[0] == {"queue": []}   # inbox.load()
    assert args[1] == {}              # inbox.tick()'s status snapshot
