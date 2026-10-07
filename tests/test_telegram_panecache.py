"""CMX-18: the pane watch re-captures only panes whose tmux ``window_activity`` moved.

Driven through the REAL :class:`PermissionGateWatcher` with the real
:class:`ActivityGatedCapture`, so "relayed exactly once" is asserted on what reaches the
sender, not on the cache's bookkeeping. tmux is never called: the capture and the activity
stamps are fixtures (the stamp format itself is pinned against ``parse_activity``).
"""
from __future__ import annotations

import threading

from chela import epoch, main
from chela.telegram.gatewatch import PermissionGateWatcher
from chela.telegram.panecache import ActivityGatedCapture, parse_activity

ASKUQ_PANE = """\
 ☐ Fruit

Which fruit do you prefer?

❯ 1. Apple
     A crisp red fruit
  2. Banana
     A soft yellow fruit
  3. Cherry
     A small red fruit
  4. Type something.
─────
  5. Chat about this

Enter to select · ↑/↓ to navigate · Esc to cancel
"""

IDLE_PANE = "❯ \n"


class _Registry:
    chat_id = "-1001"

    def __init__(self, wids):
        self._map = {w: str(5000 + i) for i, w in enumerate(wids)}

    def windows(self):
        return list(self._map)

    def thread_for_window(self, wid):
        return self._map.get(wid)


class _Fleet:
    """Fake panes + fake tmux: per-window text, activity stamp, and a capture counter."""

    def __init__(self, wids, t0=1_000):
        self.text = {w: IDLE_PANE for w in wids}
        self.stamp = {w: t0 for w in wids}
        self.captures: dict[str, int] = {w: 0 for w in wids}
        self.wall = float(t0 + 5)      # the clock is already past every stamp
        self.mono = 0.0
        self.activity_calls = 0

    def capture(self, wid):
        self.captures[wid] += 1
        return self.text[wid]

    def activity(self):
        self.activity_calls += 1
        return dict(self.stamp)

    def write(self, wid, text):
        """Pane output, the way tmux sees it: the text changes AND the stamp moves."""
        self.text[wid] = text
        self.stamp[wid] = int(self.wall)

    def advance(self, seconds):
        self.wall += seconds
        self.mono += seconds


def _watcher(fleet, wids, sends, sweep=30.0):
    gated = ActivityGatedCapture(
        fleet.capture, activity=fleet.activity, sweep=sweep,
        wall=lambda: fleet.wall, now=lambda: fleet.mono)

    def sender(text, parse_mode=None, thread=None, reply_markup=None):
        sends.append((thread, text))
        return True

    return PermissionGateWatcher(
        sender, _Registry(wids), capture=fleet.capture, tick_capture=gated.tick)


def _ticks(watcher, fleet, wids, n, every=2.0):
    for _ in range(n):
        watcher.poll(wids)
        fleet.advance(every)


def _fruit_sends(sends):
    return [s for s in sends if "Which fruit" in s[1]]


def test_parse_activity_reads_the_list_windows_format():
    assert parse_activity("@0 1791322535\n@12 1791322540\n\n%5 1791322541\n@3 x\n") == {
        "@0": 1791322535, "@12": 1791322540}


def test_an_idle_fleet_is_captured_once_not_every_tick():
    wids = [f"@{i}" for i in range(5)]
    fleet = _Fleet(wids)
    watcher = _watcher(fleet, wids, [])

    _ticks(watcher, fleet, wids, 10)

    assert fleet.captures == {w: 1 for w in wids}
    assert fleet.activity_calls == 10          # ONE list-windows per tick, not per window


def test_a_pane_that_changes_between_ticks_relays_exactly_once():
    wids = ["@0", "@1", "@2"]
    fleet = _Fleet(wids)
    sends = []
    watcher = _watcher(fleet, wids, sends)

    _ticks(watcher, fleet, wids, 3)
    fleet.write("@1", ASKUQ_PANE)
    _ticks(watcher, fleet, wids, 10)

    fruit = _fruit_sends(sends)
    assert len(fruit) == 1, fruit
    assert fruit[0][0] == "5001"
    # The idle windows were not re-read while @1 changed.
    assert fleet.captures["@0"] == 1 and fleet.captures["@2"] == 1


def test_output_in_the_same_second_as_the_capture_is_not_missed():
    """``window_activity`` is whole seconds: a write later in the second the last capture
    began leaves the stamp unchanged. That window must still be re-captured."""
    wids = ["@0"]
    fleet = _Fleet(wids)
    fleet.stamp["@0"] = int(fleet.wall)        # it wrote something this very second
    sends = []
    watcher = _watcher(fleet, wids, sends)

    watcher.poll(wids)                          # captures IDLE_PANE at second S
    fleet.text["@0"] = ASKUQ_PANE               # ...then more output, still in second S
    fleet.advance(2)
    watcher.poll(wids)

    assert len(_fruit_sends(sends)) == 1


def test_a_moved_stamp_is_recaptured_even_behind_the_wall_clock():
    """The stamp moved, but to a second the capture clock says is in the past (clock skew
    between tmux and this process). Moved is moved: re-capture."""
    wids = ["@0"]
    fleet = _Fleet(wids)
    sends = []
    watcher = _watcher(fleet, wids, sends)

    _ticks(watcher, fleet, wids, 2)
    fleet.text["@0"] = ASKUQ_PANE
    fleet.stamp["@0"] += 1                      # still well before the last capture's second
    _ticks(watcher, fleet, wids, 2)

    assert len(_fruit_sends(sends)) == 1


def test_content_change_without_activity_is_caught_by_the_backstop_sweep():
    wids = ["@0", "@1"]
    fleet = _Fleet(wids)
    sends = []
    watcher = _watcher(fleet, wids, sends, sweep=30.0)

    _ticks(watcher, fleet, wids, 2)
    fleet.text["@0"] = ASKUQ_PANE               # changed, but the stamp never moved
    _ticks(watcher, fleet, wids, 5)             # 10 s: inside the sweep, still the old text
    assert _fruit_sends(sends) == []

    _ticks(watcher, fleet, wids, 20)            # 40 more seconds: the sweep has fired
    assert len(_fruit_sends(sends)) == 1


def test_a_window_tmux_does_not_list_is_captured_every_tick():
    wids = ["@0", "@9"]
    fleet = _Fleet(["@0"])
    fleet.text["@9"] = IDLE_PANE
    fleet.captures["@9"] = 0
    watcher = _watcher(fleet, wids, [])

    _ticks(watcher, fleet, wids, 4)

    assert fleet.captures == {"@0": 1, "@9": 4}


def test_unreadable_activity_falls_back_to_capturing_everything():
    wids = ["@0", "@1"]
    fleet = _Fleet(wids)
    fleet.activity = lambda: None
    watcher = _watcher(fleet, wids, [])

    _ticks(watcher, fleet, wids, 3)

    assert fleet.captures == {"@0": 3, "@1": 3}


def test_a_window_that_leaves_and_returns_is_captured_fresh():
    fleet = _Fleet(["@0", "@1"])
    watcher = _watcher(fleet, ["@0", "@1"], [])

    _ticks(watcher, fleet, ["@0", "@1"], 2)
    _ticks(watcher, fleet, ["@0"], 2)
    _ticks(watcher, fleet, ["@0", "@1"], 1)

    assert fleet.captures == {"@0": 1, "@1": 2}


def test_a_mirror_refresh_always_reads_the_pane_fresh():
    """A D-pad tap must see the pane it just changed — never the tick cache."""
    wids = ["@0"]
    fleet = _Fleet(wids)
    watcher = _watcher(fleet, wids, [])
    _ticks(watcher, fleet, wids, 2)

    watcher.refresh_mirror("@0")

    assert fleet.captures["@0"] == 2


# ── epoch: one tmux ask per tick, not per window ─────────────────────────────


def _count_asks(monkeypatch):
    calls = []

    def fake_ask():
        calls.append(1)
        return "123-456"

    monkeypatch.setattr(epoch, "_ask", fake_ask)
    return calls


def test_epoch_per_tick_asks_tmux_once(monkeypatch):
    calls = _count_asks(monkeypatch)

    with epoch.per_tick():
        assert [epoch.current() for _ in range(10)] == ["123-456"] * 10
    assert len(calls) == 1

    epoch.current()
    epoch.current()
    assert len(calls) == 3                     # outside a tick: every call asks


def test_epoch_per_tick_reasks_after_max_age(monkeypatch):
    calls = _count_asks(monkeypatch)
    clock = [100.0]
    monkeypatch.setattr(epoch.time, "monotonic", lambda: clock[0])

    with epoch.per_tick():
        epoch.current()
        clock[0] += epoch._TICK_MAX_AGE
        epoch.current()
    assert len(calls) == 2


def test_epoch_per_tick_is_per_thread(monkeypatch):
    calls = _count_asks(monkeypatch)
    with epoch.per_tick():
        epoch.current()
        t = threading.Thread(target=lambda: [epoch.current() for _ in range(3)])
        t.start()
        t.join()
    assert len(calls) == 4


class _OneTick:
    def __init__(self, stop, per_poll):
        self.stop, self.per_poll = stop, per_poll

    def poll(self, wids):
        for _ in range(self.per_poll):
            epoch.current()
        self.stop.set()


def test_the_outbound_loop_resolves_the_epoch_once_per_tick(monkeypatch):
    calls = _count_asks(monkeypatch)
    stop = threading.Event()
    main._outbound_loop(_OneTick(stop, 40), _Registry(["@0"]), 1, stop)
    assert len(calls) == 1


def test_the_pane_loop_resolves_the_epoch_once_per_tick(monkeypatch):
    calls = _count_asks(monkeypatch)
    stop = threading.Event()
    main._pane_loop(_OneTick(stop, 40), _Registry(["@0"]), 1, stop)
    assert len(calls) == 1


# ── round 3: guards on the invariants the tests above left coinciding ────────


def test_the_capture_second_is_read_before_the_capture_not_after():
    """The clean rule compares the stamp against the second the capture BEGAN. A capture
    that straddles a second boundary — output written mid-capture, in the same second the
    stamp already reads — must be re-captured. Read the clock after the capture and that
    second looks older than the capture, so the write is lost until the sweep."""
    wids = ["@0"]
    fleet = _Fleet(wids)
    fleet.wall = 1_005.5
    fleet.stamp["@0"] = 1_005                   # it wrote earlier in this second
    real = fleet.capture

    def slow_capture(wid):
        pane = real(wid)                        # reads the idle pane...
        fleet.text[wid] = ASKUQ_PANE            # ...then the agent writes, still in 1005
        fleet.wall = 1_006.2                    # and the capture returns in the next second
        return pane

    fleet.capture = slow_capture
    sends = []
    watcher = _watcher(fleet, wids, sends)

    watcher.poll(wids)
    assert _fruit_sends(sends) == []
    fleet.advance(2)
    watcher.poll(wids)

    assert len(_fruit_sends(sends)) == 1
    assert fleet.captures["@0"] == 2


def test_the_sweep_fires_at_exactly_the_sweep_interval():
    """'Every window is re-captured at least every ``sweep`` seconds' — at the interval,
    not one tick after it."""
    wids = ["@0"]
    fleet = _Fleet(wids)
    sends = []
    watcher = _watcher(fleet, wids, sends, sweep=10.0)

    watcher.poll(wids)                          # captured at mono 0
    fleet.text["@0"] = ASKUQ_PANE               # silent change: stamp never moves
    fleet.advance(9.9)
    watcher.poll(wids)
    assert fleet.captures["@0"] == 1 and _fruit_sends(sends) == []

    fleet.advance(0.1)                          # exactly 10 s since the capture
    watcher.poll(wids)
    assert fleet.captures["@0"] == 2
    assert len(_fruit_sends(sends)) == 1


def test_the_sweep_is_measured_from_each_windows_own_last_capture():
    """A window re-captured because its stamp moved restarts ITS sweep clock; an idle
    window keeps its own. Neither is swept early nor late because of the other."""
    wids = ["@0", "@1"]
    fleet = _Fleet(wids)
    watcher = _watcher(fleet, wids, [], sweep=10.0)

    watcher.poll(wids)                          # both captured at mono 0
    fleet.advance(6)
    fleet.text["@1"] = "busy\n"
    fleet.stamp["@1"] = int(fleet.wall) - 1     # moved, and older than the next capture
    watcher.poll(wids)                          # @1 re-captured at mono 6
    fleet.advance(4)
    watcher.poll(wids)                          # mono 10: @0 is due, @1 is not
    assert fleet.captures == {"@0": 2, "@1": 2}
    fleet.advance(6)
    watcher.poll(wids)                          # mono 16: @1 is due, @0 is not
    assert fleet.captures == {"@0": 2, "@1": 3}


def test_a_cached_window_is_handed_exactly_its_own_last_text():
    """Served-from-cache must be THAT window's last capture — never a neighbour's, never
    an empty string."""
    wids = ["@0", "@1"]
    fleet = _Fleet(wids)
    fleet.text["@0"] = "zero\n"
    fleet.text["@1"] = "one\n"
    gated = ActivityGatedCapture(
        fleet.capture, activity=fleet.activity,
        wall=lambda: fleet.wall, now=lambda: fleet.mono)

    first = gated.tick(wids)
    assert [first(w) for w in wids] == ["zero\n", "one\n"]
    fleet.advance(2)
    second = gated.tick(wids)
    assert [second(w) for w in wids] == ["zero\n", "one\n"]
    assert fleet.captures == {"@0": 1, "@1": 1}


def test_a_stamp_that_moves_again_after_a_recapture_is_recaptured_again():
    """The remembered stamp is the one seen at the LATEST capture — not the first."""
    wids = ["@0"]
    fleet = _Fleet(wids)
    watcher = _watcher(fleet, wids, [])

    for n in range(3):
        _ticks(watcher, fleet, wids, 2)
        fleet.write("@0", f"out {n}\n")
    _ticks(watcher, fleet, wids, 3)

    # one initial + one per write (+1 each for the same-second rule, since each write is
    # stamped with the second its capture begins in), and nothing while idle after
    assert fleet.captures["@0"] == 1 + 3 * 2
    before = fleet.captures["@0"]
    _ticks(watcher, fleet, wids, 5)
    assert fleet.captures["@0"] == before


def test_activity_that_raises_falls_back_to_capturing_everything():
    wids = ["@0", "@1"]
    fleet = _Fleet(wids)

    def boom():
        raise RuntimeError("tmux went away")

    fleet.activity = boom
    watcher = _watcher(fleet, wids, [])

    _ticks(watcher, fleet, wids, 3)

    assert fleet.captures == {"@0": 3, "@1": 3}


def test_a_tick_capture_that_raises_still_polls_every_pane():
    """If the tick source itself blows up, the watcher reads every pane directly — a
    prompt is still relayed, and only once."""
    wids = ["@0", "@1"]
    fleet = _Fleet(wids)
    fleet.text["@1"] = ASKUQ_PANE
    sends = []

    def sender(text, parse_mode=None, thread=None, reply_markup=None):
        sends.append((thread, text))
        return True

    def broken_tick(_wids):
        raise RuntimeError("cache exploded")

    watcher = PermissionGateWatcher(
        sender, _Registry(wids), capture=fleet.capture, tick_capture=broken_tick)

    _ticks(watcher, fleet, wids, 3)

    assert fleet.captures == {"@0": 3, "@1": 3}
    fruit = _fruit_sends(sends)
    assert len(fruit) == 1 and fruit[0][0] == "5001"


def test_window_activity_asks_tmux_once_for_every_window_of_every_session(monkeypatch):
    from chela.telegram import panecache

    calls = []

    class _Done:
        returncode = 0
        stdout = "@0 1791322535\n@7 1791322540\n"

    def fake_run(argv, **kw):
        calls.append(argv)
        return _Done()

    monkeypatch.setattr(panecache.subprocess, "run", fake_run)

    assert panecache.window_activity() == {"@0": 1791322535, "@7": 1791322540}
    assert calls == [["tmux", "list-windows", "-a", "-F", "#{window_id} #{window_activity}"]]


def test_window_activity_is_unknown_when_tmux_fails(monkeypatch):
    from chela.telegram import panecache

    class _Failed:
        returncode = 1
        stdout = "@0 1791322535\n"

    monkeypatch.setattr(panecache.subprocess, "run", lambda argv, **kw: _Failed())
    assert panecache.window_activity() is None

    def missing(argv, **kw):
        raise FileNotFoundError("tmux")

    monkeypatch.setattr(panecache.subprocess, "run", missing)
    assert panecache.window_activity() is None


def test_epoch_nested_per_tick_shares_and_keeps_the_outer_memo(monkeypatch):
    """Nesting is not a new tick: the inner block reuses the outer answer, and leaving it
    must not drop the outer memo (the outer tick would then ask per window again)."""
    calls = _count_asks(monkeypatch)

    with epoch.per_tick():
        epoch.current()
        with epoch.per_tick():
            epoch.current()
        epoch.current()
        epoch.current()
    assert len(calls) == 1

    epoch.current()
    assert len(calls) == 2                     # and the outer exit still clears it


def test_epoch_per_tick_hands_back_the_answer_it_got(monkeypatch):
    answers = iter(["111-1", "222-2"])
    monkeypatch.setattr(epoch, "_ask", lambda: next(answers))
    clock = [0.0]
    monkeypatch.setattr(epoch.time, "monotonic", lambda: clock[0])

    with epoch.per_tick():
        assert epoch.current() == "111-1"
        clock[0] += epoch._TICK_MAX_AGE - 0.01
        assert epoch.current() == "111-1"
        clock[0] += 0.01
        assert epoch.current() == "222-2"       # re-asked: the NEW answer, not the old
        assert epoch.current() == "222-2"


def test_epoch_per_tick_memoises_an_unknown_answer_too(monkeypatch):
    """``None`` (tmux unreachable) is an answer: a dead server must not cost one spawn
    per window per tick either."""
    calls = []

    def fake_ask():
        calls.append(1)
        return None

    monkeypatch.setattr(epoch, "_ask", fake_ask)
    with epoch.per_tick():
        assert [epoch.current() for _ in range(5)] == [None] * 5
    assert len(calls) == 1


def test_tick_itself_survives_activity_that_raises():
    """The cache's own fallback, not the watcher's: a raising stamp read must still hand
    back a working capture (and keep the cache), not throw the whole tick away."""
    fleet = _Fleet(["@0"])
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("tmux went away")
        return dict(fleet.stamp)

    gated = ActivityGatedCapture(
        fleet.capture, activity=flaky, wall=lambda: fleet.wall, now=lambda: fleet.mono)

    assert gated.tick(["@0"])("@0") == IDLE_PANE
    fleet.advance(2)
    assert gated.tick(["@0"])("@0") == IDLE_PANE        # raised: captured, not skipped
    assert fleet.captures["@0"] == 2
    fleet.advance(2)
    gated.tick(["@0"])("@0")                            # back: its stamp is now known
    fleet.advance(2)
    assert gated.tick(["@0"])("@0") == IDLE_PANE        # ...so it is served from cache
    assert fleet.captures["@0"] == 3
